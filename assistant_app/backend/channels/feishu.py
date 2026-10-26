"""Application bot, independent of webhook notifications; no automatic sends when disabled."""
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import quote

import httpx

from ..image_processing import save_image
from ..schemas import MessageInput
from ..sessions import fail
from ..orchestration import TERMINAL

API = 'https://open.feishu.cn/open-apis'


def allowlist(value):
    return {x.strip() for x in value.split(',') if x.strip()}


class FeishuChannel:
    def __init__(self, settings, store, jobs):
        self.settings, self.store, self.jobs = settings, store, jobs
        self.http = httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False)
        self.token_value, self.token_expires = '', 0

    async def close(self):
        await self.http.aclose()

    def eligible(self, payload):
        s = self.settings
        header, event = payload.get('header', {}), payload.get('event', {})
        sender, msg = event.get('sender', {}), event.get('message', {})
        user = sender.get('sender_id', {}).get('open_id')
        if (header.get('event_type') != 'im.message.receive_v1' or sender.get('sender_type') != 'user'
                or sender.get('tenant_key') not in allowlist(s.feishu_tenants)
                or user not in allowlist(s.feishu_users) or user == s.feishu_bot_open_id):
            return None
        if not msg.get('message_id') or not msg.get('chat_id') or msg.get('chat_type') not in ('p2p', 'group'):
            return None
        if msg['chat_type'] == 'group':
            if msg['chat_id'] not in allowlist(s.feishu_chats):
                return None
            if not any(m.get('id', {}).get('open_id') == s.feishu_bot_open_id for m in msg.get('mentions', [])):
                return None
        # A user's private/group/thread context is never shared with another user or channel.
        identity = ['feishu', sender['tenant_key'], msg['chat_type'], msg['chat_id'],
                    msg.get('root_id') or msg.get('thread_id') or '', user]
        owner = 'feishu:' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        return owner, msg

    async def receive(self, request):
        s = self.settings
        if not s.feishu_enabled:
            fail('feishu_disabled', '飞书通道尚未配置完成', 503)
        raw = await request.body()
        timestamp = request.headers.get('x-lark-request-timestamp', '')
        nonce = request.headers.get('x-lark-request-nonce', '')
        try:
            if abs(time.time() - int(timestamp)) > 300 or not 8 <= len(nonce) <= 128:
                raise ValueError()
            if s.feishu_transport == 'ws':
                expected = hmac.new(s.feishu_bridge_secret.get_secret_value().encode(),
                                    timestamp.encode() + nonce.encode() + raw, hashlib.sha256).hexdigest()
            else:
                expected = hashlib.sha256((timestamp + nonce + s.feishu_encrypt_key.get_secret_value()).encode() + raw).hexdigest()
            if not hmac.compare_digest(expected, request.headers.get('x-lark-signature', '')):
                raise ValueError()
            payload = json.loads(raw)
            if s.feishu_transport == 'http':
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                from cryptography.hazmat.primitives.padding import PKCS7
                encrypted = base64.b64decode(payload['encrypt'], validate=True)
                key = hashlib.sha256(s.feishu_encrypt_key.get_secret_value().encode()).digest()
                decryptor = Cipher(algorithms.AES(key), modes.CBC(encrypted[:16])).decryptor()
                padded = decryptor.update(encrypted[16:]) + decryptor.finalize()
                unpadder = PKCS7(128).unpadder()
                payload = json.loads(unpadder.update(padded) + unpadder.finalize())
                token = payload.get('token') or payload.get('header', {}).get('token', '')
                if not hmac.compare_digest(token, s.feishu_verification_token.get_secret_value()):
                    raise ValueError()
            if payload.get('type') == 'url_verification':
                return {'challenge': payload['challenge']}
            header = payload['header']
            if header.get('app_id') != s.feishu_app_id:
                raise ValueError()
            created = int(header['create_time'])
            if created > 10**11:
                created /= 1000
            if abs(time.time() - created) > 300:
                raise ValueError()
            event_id = header['event_id']
            if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            fail('feishu_verification', '飞书事件验证失败', 403)
        eligible = self.eligible(payload)
        if not eligible:
            return {'accepted': False, 'reason': 'outside_allowed_scope'}
        owner, message = eligible
        with self.store.tx() as db:
            old = db.execute('SELECT event FROM channel_events WHERE event=? OR message=?',
                             (event_id, message['message_id'])).fetchone()
            if old:
                return {'accepted': True, 'deduplicated': True}
            if db.execute("SELECT count(*) FROM channel_events WHERE state='received'").fetchone()[0] >= s.queue_limit:
                fail('feishu_queue_full', '飞书接收队列已满', 503)
            # The event is durable before acknowledging; no image or model HTTP call on callback path.
            db.execute('INSERT OR IGNORE INTO visitors(id,expires) VALUES (?,?)', (owner, time.time() + s.ttl_seconds))
            conv = db.execute('SELECT id FROM conversations WHERE owner=?', (owner,)).fetchone()
            if not conv:
                from ..sessions import uid
                conversation = uid()
                db.execute('INSERT INTO conversations VALUES (?,?,?,?)', (conversation, owner, '飞书对话', time.time()))
            else:
                conversation = conv['id']
            db.execute('INSERT INTO channel_events VALUES (?,?,?,?,?,?,?,?,?)',
                (event_id, message['message_id'], owner, conversation,
                 json.dumps(message, ensure_ascii=False), 'received', None, 'pending', time.time()))
        return {'accepted': True, 'deduplicated': False}

    async def token(self):
        if time.time() < self.token_expires:
            return self.token_value
        response = await self.http.post(API + '/auth/v3/tenant_access_token/internal', json={
            'app_id': self.settings.feishu_app_id, 'app_secret': self.settings.feishu_app_secret.get_secret_value()})
        response.raise_for_status()
        data = response.json()
        if data.get('code') != 0:
            raise ValueError('feishu_token_failed')
        self.token_value, self.token_expires = data['tenant_access_token'], time.time() + min(data['expire'], 3600) - 60
        return self.token_value

    async def image(self, message_id, key):
        url = API + '/im/v1/messages/' + quote(message_id, safe='') + '/resources/' + quote(key, safe='')
        async with self.http.stream('GET', url, params={'type': 'image'},
                                    headers={'Authorization': 'Bearer ' + await self.token()}) as response:
            response.raise_for_status()
            raw = bytearray()
            async for block in response.aiter_bytes():
                raw.extend(block)
                if len(raw) > self.settings.image_bytes:
                    raise ValueError('feishu_image_too_large')
            return bytes(raw)

    def content(self, msg):
        data = json.loads(msg['content'])
        text, images = [], []
        if msg['message_type'] == 'text':
            text = [data['text']]
        elif msg['message_type'] == 'image':
            images = [data['image_key']]
        elif msg['message_type'] == 'post':
            data = data.get('zh_cn') or data.get('en_us') or data
            text = [data.get('title', '')]
            for paragraph in data.get('content', []):
                for part in paragraph:
                    if part.get('tag') == 'text':
                        text.append(part.get('text', ''))
                    elif part.get('tag') == 'img':
                        images.append(part['image_key'])
        else:
            raise ValueError('unsupported_feishu_message')
        if len(images) > self.settings.max_images:
            raise ValueError('feishu_image_count')
        combined = '\n'.join(text)
        for mention in msg.get('mentions', []):
            combined = combined.replace(mention.get('key', ''), '')
        return combined.strip(), images

    async def tick(self):
        if not self.settings.feishu_enabled:
            return
        # One short cycle per maintenance tick; model inference runs in the shared Jobs executor.
        row = self.store.one("SELECT * FROM channel_events WHERE state='received' ORDER BY created LIMIT 1")
        if row:
            active = self.store.one("SELECT id FROM jobs WHERE conversation=? AND phase NOT IN "
                                    "('done','failed','cancelled','interrupted')", (row['conversation'],))
            if not active:
                self.store.execute("UPDATE channel_events SET state='processing' WHERE event=?", (row['event'],))
                try:
                    msg = json.loads(row['payload'])
                    text, keys = self.content(msg)
                    attachments = []
                    for key in keys:
                        image = await save_image(self.store, row['owner'], row['conversation'],
                                                 await self.image(row['message'], key))
                        attachments.append(image['id'])
                    data = MessageInput(text=text, attachment_ids=attachments,
                                        request_key='feishu_' + hashlib.sha256(row['message'].encode()).hexdigest())
                    job, fresh = self.store.submit(row['owner'], row['conversation'], data)
                    self.store.execute("UPDATE channel_events SET state='submitted',job=?,payload='{}' WHERE event=?",
                                       (job['id'], row['event']))
                    if fresh:
                        self.jobs.start(job)
                except Exception:
                    self.store.execute("UPDATE channel_events SET state='failed',payload='{}' WHERE event=?", (row['event'],))
        for row in self.store.all("SELECT * FROM channel_events WHERE state='submitted' AND reply_state='pending'"):
            job = self.store.one('SELECT * FROM jobs WHERE id=?', (row['job'],))
            if not job or job['phase'] not in TERMINAL:
                continue
            try:
                token = await self.token()
                self.store.execute("UPDATE channel_events SET reply_state='sending' WHERE event=?", (row['event'],))
                response = await self.http.post(API + '/im/v1/messages/' + quote(row['message'], safe='') + '/reply',
                    headers={'Authorization': 'Bearer ' + token}, json={'msg_type': 'text',
                    'content': json.dumps({'text': (job['answer'] or job['error'] or '本次处理已取消')[:12000]}, ensure_ascii=False),
                    'uuid': hashlib.sha256(row['message'].encode()).hexdigest()[:32]})
                response.raise_for_status()
                result = response.json()
                self.store.execute('UPDATE channel_events SET reply_state=? WHERE event=?',
                                   ('sent' if result.get('code') == 0 else 'failed', row['event']))
            except Exception:
                # Unknown send outcome is never resent automatically.
                self.store.execute("UPDATE channel_events SET reply_state='unknown' WHERE event=?", (row['event'],))
