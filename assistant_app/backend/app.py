import asyncio
from contextlib import asynccontextmanager
import fcntl
import json
import logging
import re
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt

from .image_processing import save_image
from .model_clients import ModelClients, text_artifact_state
from .orchestration import Jobs, TERMINAL
from .schemas import MessageInput, FeedbackInput
from .security import Security, gateway_identity
from .sessions import Store, fail, uid
from .settings import Settings, APP_ROOT

markdown = MarkdownIt('commonmark', {'html': False}).disable('image').enable('table')
LOG = logging.getLogger('coating_assistant')


class RequestGuard:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.body_slots = asyncio.Semaphore(settings.body_concurrency)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        supplied = headers.get(b'x-request-id', b'').decode('ascii', errors='ignore')
        request_id = supplied if re.fullmatch(r'[a-zA-Z0-9._-]{1,80}', supplied) else uid()
        scope.setdefault('state', {})['request_id'] = request_id
        started, status = time.monotonic(), 500
        async def secured_send(msg):
            nonlocal status
            if msg['type'] == 'http.response.start':
                status = msg['status']
                msg['headers'] += [(b'x-request-id', request_id.encode()),
                    (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'no-referrer'), (b'x-frame-options', b'DENY'),
                    (b'cache-control', b'no-store'),
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")]
            await send(msg)
        async def reject(code, message, status_code):
            LOG.info('request_rejected id=%s method=%s code=%s status=%d',
                     request_id, scope['method'], code, status_code)
            await JSONResponse({'detail': {'code': code, 'message': message}}, status_code)(scope, receive, secured_send)
        try:
            host = urlsplit('//' + headers.get(b'host', b'').decode('ascii', errors='ignore')).hostname
        except ValueError:
            host = None
        allowed = {'127.0.0.1', 'localhost', '::1', urlsplit(self.settings.public_origin).hostname}
        if host not in allowed:
            return await reject('host_denied', '请求主机无效', 400)
        # The exact channel callback has its own signature/token authentication;
        # browser SSO headers must not replace that independent protocol.
        if scope['path'] != '/api/assistant/channels/feishu/events':
            try:
                gateway_identity({key.decode(): value.decode('utf-8', errors='replace') for key,value in headers.items()}, self.settings)
            except HTTPException as exc:
                return await reject(exc.detail['code'], exc.detail['message'], exc.status_code)
        limit = self.settings.image_bytes * self.settings.max_images + 1024 * 1024
        if not scope['path'].endswith('/uploads'):
            limit = 1024 * 1024
        if self.body_slots.locked():
            return await reject('body_capacity', '上传或请求接收繁忙，请稍后重试', 429)
        total, chunks = 0, []
        # Bound simultaneous body buffers as well as each body and read deadline.
        async with self.body_slots:
            deadline = time.monotonic() + 30
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return await reject('body_timeout', '上传超时，请重试', 408)
                try:
                    msg = await asyncio.wait_for(receive(), min(10, remaining))
                except TimeoutError:
                    return await reject('body_timeout', '上传超时，请重试', 408)
                if msg['type'] == 'http.disconnect':
                    return
                total += len(msg.get('body', b''))
                if total > limit:
                    return await reject('body_too_large', '请求内容过大', 413)
                chunks.append(msg)
                if not msg.get('more_body', False):
                    break
        async def bounded_receive():
            return chunks.pop(0) if chunks else await receive()
        try:
            await self.app(scope, bounded_receive, secured_send)
        finally:
            route = getattr(scope.get('route'), 'path', 'unmatched')
            LOG.info('request id=%s method=%s route=%s status=%d elapsed_ms=%d',
                     request_id, scope['method'], route, status, round((time.monotonic()-started)*1000))


def public_job(row):
    return {'id': row['id'], 'conversation_id': row['conversation'], 'text': row['text'],
            'attachment_ids': json.loads(row['images']), 'phase': row['phase'],
            'answer': row['answer'], 'answer_html': markdown.render(row['answer']),
            'error': row['error'], 'vision': json.loads(row['vision']),
            'metadata': json.loads(row['metadata']), 'created': row['created']}


def create_app(settings=None, models=None, model_client_factory=None):
    s = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        s.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (s.data_dir / 'service.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('助手只支持一个API进程；此数据目录已被使用') from None
        store = Store(s)
        app.state.store, app.state.settings = store, s
        app.state.security = Security(s, store)
        app.state.models = models or ModelClients(s)
        from .model_catalog import ModelCatalog
        app.state.model_catalog = ModelCatalog(s, app.state.models, model_client_factory)
        app.state.jobs = Jobs(store, app.state.models)
        app.state.status_cache = (0, None)
        from .channels.feishu import FeishuChannel
        app.state.feishu = FeishuChannel(s, store, app.state.jobs)
        async def maintenance():
            while True:
                expired = store.all('SELECT j.id FROM jobs j JOIN visitors v ON j.owner=v.id '
                                    "WHERE v.expires<? AND j.phase NOT IN ('done','failed','cancelled','interrupted')", (time.time(),))
                for job in expired:
                    await app.state.jobs.cancel(job['id'])
                store.cleanup()
                try:
                    await app.state.feishu.tick()
                except Exception:
                    # An invalid external event never stops local TTL cleanup or future cycles.
                    pass
                await asyncio.sleep(2)
        background = asyncio.create_task(maintenance())
        try:
            yield
        finally:
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)
            await app.state.jobs.close()
            await app.state.model_catalog.close()
            await app.state.models.close()
            await app.state.feishu.close()
            lock.close()

    app = FastAPI(title='涂镀智溯·多模态工艺助手', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestGuard, settings=s)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'detail': {'code': 'invalid_input', 'message': '输入格式不正确或超过上限'}}, 422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        LOG.error('request_failed id=%s exception_type=%s',
                  getattr(request.state, 'request_id', 'unknown'), type(exc).__name__)
        return JSONResponse({'detail': {'code': 'internal_error', 'message': '服务暂时无法处理请求'}}, 500)

    def owner(request, write=False):
        return app.state.security.owner(request, write)

    async def status():
        cached_at, cached = app.state.status_cache
        if cached and time.monotonic() - cached_at < 8:
            return cached
        text, vision = await asyncio.gather(app.state.models.available('text'), app.state.models.available('vision'))
        artifact = text_artifact_state(s)
        usable = artifact in ('base_preview', 'finetuned_lora', 'finetuned_merged', 'approved_cloud')
        result = {'text': {'base': s.text_served_model if artifact == 'approved_cloud' else 'Qwen3.5-9B',
                          'provider': '阿里云百炼' if artifact == 'approved_cloud' else '本地Qwen',
                          'served_model': s.text_served_model,
                          'endpoint_available': text, 'available': text and usable, 'state': artifact,
                          'runtime_adapter_verified': False},
                  'vision': {'base': s.vision_served_model if s.vision_mode == 'approved_cloud' else 'Qwen3-VL-8B-Instruct',
                             'provider': '阿里云百炼' if s.vision_mode == 'approved_cloud' else '本地Qwen',
                             'served_model': s.vision_served_model, 'available': vision, 'state': s.vision_mode},
                  'feishu': {'enabled': s.feishu_enabled, 'transport': s.feishu_transport,
                             'real_integration_verified': False},
                  'mock': bool(getattr(app.state.models, 'is_mock', False)),
                  'storage': 'available', 'limits': {'max_images': s.max_images,
                      'image_bytes': s.image_bytes, 'image_pixels': s.image_pixels, 'ttl_seconds': s.ttl_seconds}}
        app.state.status_cache = time.monotonic(), result
        return result

    @app.get('/health/live')
    def live():
        return {'web': 'alive'}

    @app.get('/health/ready')
    async def ready():
        state = await status()
        return JSONResponse(state, 200 if state['text']['available'] else 503)

    @app.get('/api/assistant/status')
    async def get_status():
        return await status()

    @app.get('/api/assistant/models')
    async def model_choices(request: Request, refresh: bool = False):
        who = owner(request)
        app.state.security.rate('models:' + who, 20)
        return await app.state.model_catalog.listing(refresh)

    @app.post('/api/assistant/session')
    def session(request: Request, response: Response):
        return app.state.security.session(request, response)

    @app.get('/api/assistant/conversations')
    def conversations(request: Request):
        return app.state.store.all('SELECT id,title,created FROM conversations WHERE owner=? ORDER BY created DESC',
                                    (owner(request),))

    @app.post('/api/assistant/conversations')
    def new_conversation(request: Request):
        who = owner(request, True)
        app.state.security.rate('conversation:' + who, 20)
        return {'id': app.state.store.conversation(who)}

    @app.get('/api/assistant/conversations/{ident}')
    def conversation(ident: str, request: Request):
        who = owner(request)
        row = app.state.store.own('conversations', ident, who)
        return {**row, 'owner': None, 'messages': [public_job(x) for x in app.state.store.all(
            'SELECT * FROM jobs WHERE conversation=? AND owner=? ORDER BY created', (ident, who))]}

    @app.delete('/api/assistant/conversations/{ident}')
    async def clear(ident: str, request: Request):
        who = owner(request, True)
        app.state.store.own('conversations', ident, who)
        for row in app.state.store.all('SELECT id FROM jobs WHERE conversation=? AND owner=?', (ident, who)):
            await app.state.jobs.cancel(row['id'])
        app.state.store.clear(ident, who)
        return {'deleted': True}

    @app.post('/api/assistant/uploads')
    async def upload(request: Request, conversation_id: str = Form(...), files: list[UploadFile] = File(...)):
        who = owner(request, True)
        app.state.security.rate('upload:' + who, 12)
        app.state.store.own('conversations', conversation_id, who)
        if not 1 <= len(files) <= s.max_images:
            fail('image_count', f'每次支持1—{s.max_images}张图片', 422)
        saved = []
        try:
            for file in files:
                raw = await file.read(s.image_bytes + 1)
                saved.append(await save_image(app.state.store, who, conversation_id, raw))
        except BaseException:
            for item in saved:
                app.state.store.execute('DELETE FROM images WHERE id=? AND owner=?', (item['id'], who))
                (s.data_dir / 'images' / (item['id'] + '.png')).unlink(missing_ok=True)
            raise
        finally:
            for file in files:
                await file.close()
        return {'attachments': saved}

    @app.get('/api/assistant/attachments/{ident}')
    def image(ident: str, request: Request):
        app.state.store.own('images', ident, owner(request))
        path = s.data_dir / 'images' / (ident + '.png')
        if not path.is_file():
            fail('image_expired', '原图已过期，请重新上传', 404)
        return FileResponse(path, media_type='image/png')

    @app.delete('/api/assistant/attachments/{ident}')
    def delete_image(ident: str, request: Request):
        who = owner(request, True)
        row = app.state.store.own('images', ident, who)
        for job in app.state.store.all('SELECT images,phase FROM jobs WHERE conversation=?', (row['conversation'],)):
            if ident in json.loads(job['images']) and job['phase'] not in TERMINAL:
                fail('image_in_use', '图片正用于处理，请先取消任务', 409)
        app.state.store.execute('DELETE FROM images WHERE id=? AND owner=?', (ident, who))
        (s.data_dir / 'images' / (ident + '.png')).unlink(missing_ok=True)
        return {'deleted': True}

    @app.post('/api/assistant/conversations/{ident}/messages')
    async def message(ident: str, data: MessageInput, request: Request):
        who = owner(request, True)
        app.state.security.rate('message:' + who, 12)
        app.state.security.rate('source:' + (request.client.host if request.client else 'unknown'), 60)
        route = app.state.model_catalog.resolve(data.model_id)
        job, fresh = app.state.store.submit(who, ident, data, route.profile)
        if fresh:
            app.state.jobs.start(job, route)
        return {'request_id': job['id'], 'phase': job['phase'], 'deduplicated': not fresh}

    @app.get('/api/assistant/requests/{ident}')
    def job(ident: str, request: Request):
        return public_job(app.state.store.own('jobs', ident, owner(request)))

    @app.post('/api/assistant/requests/{ident}/cancel')
    async def cancel(ident: str, request: Request):
        app.state.store.own('jobs', ident, owner(request, True))
        await app.state.jobs.cancel(ident)
        return {'phase': app.state.store.one('SELECT phase FROM jobs WHERE id=?', (ident,))['phase']}

    @app.get('/api/assistant/requests/{ident}/events')
    async def events(ident: str, request: Request, after: int = 0):
        who = owner(request)
        app.state.store.own('jobs', ident, who)
        app.state.security.rate('stream:' + who, 20)
        try:
            after = max(0, after, int(request.headers.get('last-event-id', '0')))
        except ValueError:
            fail('invalid_cursor', '事件游标无效', 422)
        async def stream():
            cursor, deadline = after, time.monotonic() + s.request_timeout + 10
            while time.monotonic() < deadline:
                if await request.is_disconnected():
                    break
                # Recheck identity/TTL and ownership while streaming; deleting a chat ends its stream.
                try:
                    app.state.security.owner(request)
                    row = app.state.store.own('jobs', ident, who)
                except HTTPException:
                    break
                for event in app.state.store.all('SELECT * FROM events WHERE job=? AND seq>? ORDER BY seq', (ident, cursor)):
                    cursor = event['seq']
                    payload = {'request_id': ident, 'sequence': cursor, **json.loads(event['payload'])}
                    yield f'id: {cursor}\nevent: {event["kind"]}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'
                if row['phase'] in TERMINAL:
                    break
                yield ': heartbeat\n\n'
                await asyncio.sleep(.35)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @app.post('/api/assistant/feedback')
    def feedback(data: FeedbackInput, request: Request):
        who = owner(request, True)
        row = app.state.store.own('jobs', data.request_id, who)
        if row['phase'] != 'done':
            fail('feedback_state', '只能反馈已完成的回答', 409)
        app.state.store.execute('INSERT OR REPLACE INTO feedback VALUES (?,?)', (data.request_id, data.value))
        return {'saved': True, 'used_for_training': False}

    @app.post('/api/assistant/channels/feishu/events')
    async def feishu(request: Request):
        return await app.state.feishu.receive(request)

    static = APP_ROOT / 'frontend/dist'
    if static.is_dir():
        app.mount('/', StaticFiles(directory=static, html=True), name='assistant-ui')
    else:
        @app.get('/')
        def no_frontend():
            return JSONResponse({'message': '请先构建 assistant_app/frontend，详见助手README'}, 503)
    return app
