"""Single-process SQLite ledger. Every public lookup requires an owner."""
from contextlib import contextmanager
import hashlib
import asyncio
from datetime import datetime, timezone
import json
import os
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException
from .quantity_review import quantity_notices


def fail(code, message, status=400):
    raise HTTPException(status, {'code': code, 'message': message})


def uid():
    return uuid4().hex


class Store:
    def __init__(self, settings):
        self.settings = settings
        self.image_slots = asyncio.Semaphore(2)
        self.root = settings.data_dir
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.root / 'images').mkdir(mode=0o700, exist_ok=True)
        self.path = self.root / 'assistant.sqlite3'
        with self.tx() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS visitors(id TEXT PRIMARY KEY, expires REAL, used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS visitor_bindings(visitor TEXT PRIMARY KEY, subject TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, owner TEXT, title TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY, owner TEXT, conversation TEXT, expires REAL,
                bytes INTEGER, width INTEGER, height INTEGER);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, owner TEXT, conversation TEXT, key TEXT,
                fingerprint TEXT, text TEXT, images TEXT, phase TEXT, answer TEXT DEFAULT '',
                error TEXT, created REAL, vision TEXT DEFAULT '[]', metadata TEXT DEFAULT '{}',
                UNIQUE(owner,key));
            CREATE TABLE IF NOT EXISTS events(job TEXT, seq INTEGER, kind TEXT, payload TEXT,
                PRIMARY KEY(job,seq));
            CREATE TABLE IF NOT EXISTS feedback(job TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS model_budget(day TEXT PRIMARY KEY, reserved INTEGER);
            CREATE TABLE IF NOT EXISTS channel_events(event TEXT PRIMARY KEY, message TEXT UNIQUE, owner TEXT,
                conversation TEXT, payload TEXT, state TEXT, job TEXT, reply_state TEXT, created REAL);
            CREATE INDEX IF NOT EXISTS jobs_conversation ON jobs(conversation,created);
            ''')
            db.execute("UPDATE jobs SET phase='interrupted',error='服务重启，本次请求已中断，请手动重试' "
                       "WHERE phase NOT IN ('done','failed','cancelled','interrupted')")
            db.execute("UPDATE channel_events SET state='interrupted' WHERE state IN ('received','processing')")
            db.execute("UPDATE channel_events SET reply_state='unknown' WHERE reply_state='sending'")
        os.chmod(self.path, 0o600)

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def one(self, sql, args=()):
        with self.tx() as db:
            row = db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql, args=()):
        with self.tx() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.tx() as db:
            db.execute(sql, args)

    def own(self, table, ident, owner):
        assert table in ('jobs', 'conversations', 'images')
        row = self.one(f'SELECT * FROM {table} WHERE id=? AND owner=?', (ident, owner))
        if not row or (table == 'images' and row['expires'] < time.time()):
            fail('not_found', '内容不存在或已过期', 404)
        return row

    def conversation(self, owner, title='新对话'):
        with self.tx() as db:
            count = db.execute('SELECT count(*) FROM conversations WHERE owner=?', (owner,)).fetchone()[0]
            if count >= 40:
                fail('conversation_limit', '最多保留40个短期对话，请清理不再需要的对话', 429)
            ident = uid()
            db.execute('INSERT INTO conversations VALUES (?,?,?,?)', (ident, owner, title[:40], time.time()))
        return ident

    def submit(self, owner, conversation, data, model_profile=None):
        self.own('conversations', conversation, owner)
        snapshot = data.model_dump(exclude_none=True)
        fingerprint = hashlib.sha256(json.dumps([conversation, snapshot], sort_keys=True).encode()).hexdigest()
        with self.tx() as db:
            old = db.execute('SELECT * FROM jobs WHERE owner=? AND key=?', (owner, data.request_key)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    fail('request_key_conflict', '请求键已用于其他内容', 409)
                return dict(old), False
            active = "phase NOT IN ('done','failed','cancelled','interrupted')"
            if db.execute(f'SELECT 1 FROM jobs WHERE conversation=? AND {active}', (conversation,)).fetchone():
                fail('conversation_busy', '请等待当前回答或取消后再发送', 409)
            if db.execute(f'SELECT count(*) FROM jobs WHERE {active}').fetchone()[0] >= self.settings.queue_limit:
                fail('queue_full', '当前请求较多，请稍后重试', 429)
            visitor = db.execute('SELECT * FROM visitors WHERE id=?', (owner,)).fetchone()
            if not visitor or visitor['expires'] < time.time() or visitor['used'] >= self.settings.visitor_budget:
                fail('budget_exhausted', '当前访客的推理额度已用完或身份已过期', 429)
            if len(data.attachment_ids) > self.settings.max_images:
                fail('image_count', '图片数量超限', 422)
            for image in data.attachment_ids:
                record = db.execute('SELECT * FROM images WHERE id=? AND owner=? AND conversation=?',
                                    (image, owner, conversation)).fetchone()
                if not record or record['expires'] < time.time():
                    fail('attachment_unavailable', '图片不存在、已过期或不属于本对话', 404)
            day = datetime.now(timezone.utc).date().isoformat()
            db.execute('INSERT OR IGNORE INTO model_budget VALUES (?,0)', (day,))
            reserved = db.execute('SELECT reserved FROM model_budget WHERE day=?', (day,)).fetchone()[0]
            cost = 1 + len(data.attachment_ids)
            if reserved + cost > self.settings.daily_model_calls:
                fail('daily_budget', '本服务今日推理额度已用完，请稍后再试', 429)
            db.execute('UPDATE model_budget SET reserved=reserved+? WHERE day=?', (cost, day))
            ident = uid()
            db.execute('INSERT INTO jobs(id,owner,conversation,key,fingerprint,text,images,phase,created,metadata) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (ident, owner, conversation, data.request_key, fingerprint, data.text,
                        json.dumps(data.attachment_ids), 'queued', time.time(),
                        json.dumps({'model_profile': model_profile,
                                    'review_notices': quantity_notices(data.text)}, ensure_ascii=False)))
            db.execute('UPDATE visitors SET used=used+1 WHERE id=?', (owner,))
            db.execute("UPDATE conversations SET title=? WHERE id=? AND title='新对话'",
                       ((data.text or '图片工艺分析')[:30], conversation))
        return self.own('jobs', ident, owner), True

    def event(self, job, kind, payload):
        with self.tx() as db:
            seq = db.execute('SELECT coalesce(max(seq),0)+1 FROM events WHERE job=?', (job,)).fetchone()[0]
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (job, seq, kind, json.dumps(payload, ensure_ascii=False)))

    def clear(self, conversation, owner):
        self.own('conversations', conversation, owner)
        with self.tx() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM images WHERE conversation=?', (conversation,))]
            db.execute('DELETE FROM feedback WHERE job IN (SELECT id FROM jobs WHERE conversation=?)', (conversation,))
            db.execute('DELETE FROM events WHERE job IN (SELECT id FROM jobs WHERE conversation=?)', (conversation,))
            db.execute('DELETE FROM jobs WHERE conversation=?', (conversation,))
            db.execute('DELETE FROM images WHERE conversation=?', (conversation,))
            db.execute('DELETE FROM conversations WHERE id=?', (conversation,))
        for ident in ids:
            (self.root / 'images' / (ident + '.png')).unlink(missing_ok=True)

    def cleanup(self):
        now = time.time()
        for row in self.all('SELECT c.id,c.owner FROM conversations c JOIN visitors v ON c.owner=v.id '
                            'WHERE v.expires<?', (now,)):
            self.clear(row['id'], row['owner'])
        for row in self.all('SELECT id FROM images WHERE expires<?', (now,)):
            (self.root / 'images' / (row['id'] + '.png')).unlink(missing_ok=True)
        self.execute('DELETE FROM images WHERE expires<?', (now,))
        self.execute('DELETE FROM visitor_bindings WHERE visitor IN (SELECT id FROM visitors WHERE expires<?)', (now,))
        self.execute('DELETE FROM visitors WHERE expires<?', (now,))
        self.execute('DELETE FROM channel_events WHERE created<?', (now - self.settings.ttl_seconds,))
        # Orphaned files can only be this app's own re-encoded uploads after a crash.
        for path in (self.root / 'images').glob('*.png'):
            if path.stat().st_mtime < now - self.settings.ttl_seconds:
                path.unlink(missing_ok=True)
