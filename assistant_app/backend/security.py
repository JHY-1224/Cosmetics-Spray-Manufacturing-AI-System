from collections import defaultdict, deque
import hashlib
import hmac
import os
import re
import secrets
import time

from .sessions import fail

COOKIE = 'coating_assistant_visitor'


def gateway_identity(headers, settings):
    """Headers must be overwritten by an authenticated, server-owned gateway."""
    expected = settings.gateway_key.get_secret_value()
    if not expected:
        return None
    supplied = headers.get('x-assistant-gateway-key', '')
    user = headers.get('x-assistant-user', '')
    if (not hmac.compare_digest(supplied.encode(), expected.encode())
            or not re.fullmatch(r'[a-zA-Z0-9@._:+-]{1,160}', user)):
        fail('gateway_required', '请通过已授权的账号入口访问', 403)
    return user


class Security:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        key = store.root / 'session.key'
        if not key.exists():
            with key.open('xb') as out:
                os.chmod(key, 0o600)
                out.write(secrets.token_bytes(48))
        self.key = key.read_bytes()
        self.windows = defaultdict(deque)

    def digest(self, value):
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()

    def csrf(self, owner):
        return self.digest('csrf:' + owner)

    def rate(self, key, limit, seconds=60):
        now = time.monotonic()
        window = self.windows[key]
        while window and window[0] < now - seconds:
            window.popleft()
        if len(window) >= limit:
            fail('rate_limit', '操作过于频繁，请稍后重试', 429)
        window.append(now)
        if len(self.windows) > 5000:
            self.windows = defaultdict(deque, {k: v for k, v in self.windows.items()
                                               if v and v[-1] >= now - seconds})

    def origin(self, request):
        origin = request.headers.get('origin')
        allowed = {self.settings.public_origin}
        if self.settings.public_origin == 'http://127.0.0.1:5001':
            allowed.add('http://localhost:5001')
        if origin and origin not in allowed:
            fail('origin_denied', '请求来源不受信任', 403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            fail('origin_denied', '不接受跨站请求', 403)

    def owner(self, request, write=False):
        identity = gateway_identity(request.headers, self.settings)
        raw = request.cookies.get(COOKIE, '')
        owner, _, sig = raw.partition('.')
        if not owner or not hmac.compare_digest(sig, self.digest(owner)):
            fail('session_required', '访客会话已过期，请刷新页面', 401)
        visitor = self.store.one('SELECT * FROM visitors WHERE id=?', (owner,))
        if not visitor or visitor['expires'] < time.time():
            fail('session_expired', '访客会话已过期，请刷新页面', 401)
        if identity is not None:
            binding = self.store.one('SELECT subject FROM visitor_bindings WHERE visitor=?', (owner,))
            if not binding or not hmac.compare_digest(binding['subject'], self.digest('gateway:' + identity)):
                fail('session_identity_changed', '账号已切换，请刷新后重新建立会话', 401)
        if write:
            self.origin(request)
            if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), self.csrf(owner)):
                fail('csrf_denied', '请求校验失效，请刷新页面', 403)
        return owner

    def session(self, request, response):
        identity = gateway_identity(request.headers, self.settings)
        self.origin(request)
        self.rate('session:' + (request.client.host if request.client else 'unknown'), 12)
        try:
            owner = self.owner(request)
        except Exception as exc:
            from fastapi import HTTPException
            if not isinstance(exc, HTTPException):
                raise
            owner = secrets.token_hex(32)
            with self.store.tx() as db:
                db.execute('INSERT INTO visitors(id,expires) VALUES (?,?)',
                           (owner, time.time() + self.settings.ttl_seconds))
                if identity is not None:
                    db.execute('INSERT INTO visitor_bindings VALUES (?,?)',
                               (owner, self.digest('gateway:' + identity)))
        response.set_cookie(COOKIE, owner + '.' + self.digest(owner), httponly=True,
                            secure=self.settings.secure_cookie, samesite='strict',
                            max_age=self.settings.ttl_seconds, path='/')
        return {'csrf_token': self.csrf(owner), 'expires_in': self.settings.ttl_seconds}
