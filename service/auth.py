"""Cookie sessions, CSRF and per-message WebSocket authorization."""
import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import os
import secrets
from urllib.parse import urlsplit
from starlette.responses import JSONResponse
from starlette.requests import Request
from fastapi import HTTPException
from pydantic import BaseModel, Field

COOKIE = 'ember_session'


def password_hash(value):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), salt, 310000)
    return f'pbkdf2_sha256:310000:{salt.hex()}:{digest.hex()}'


def verify(value, encoded):
    try:
        algorithm, rounds, salt, expected = encoded.split(':')
        if algorithm != 'pbkdf2_sha256' or not 100000 <= int(rounds) <= 1000000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', value.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@dataclass
class AuthConfig:
    verifier: str
    secure: bool = False
    origins: tuple = ()
    lifetime: int = 30 * 86400

    @property
    def credential(self):
        return hashlib.sha256(self.verifier.encode()).hexdigest()

    @classmethod
    def environment(cls):
        value = os.getenv('EMBER_ACCESS_CODE_HASH', '')
        parts = value.split(':')
        if (len(parts) != 4 or parts[0] != 'pbkdf2_sha256' or not parts[1].isdigit()
                or not 100000 <= int(parts[1]) <= 1000000
                or len(parts[2]) != 32 or len(parts[3]) != 64
                or any(c not in '0123456789abcdef' for c in parts[2]+parts[3])):
            raise ValueError('请先运行 service_setup.py 生成服务访问配置')
        public = os.getenv('EMBER_PUBLIC_URL', '').rstrip('/')
        origins = tuple(x.rstrip('/') for x in (public + ',' + os.getenv('EMBER_ALLOWED_ORIGINS', '')).split(',') if x)
        for origin in origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
                raise ValueError('EMBER_PUBLIC_URL 和允许的来源必须是完整的 origin，不能带路径')
        secure = os.getenv('EMBER_COOKIE_SECURE', 'true' if public.startswith('https:') else 'false').lower() == 'true'
        if public.startswith('https:') and not secure:
            raise ValueError('HTTPS 服务必须启用 Secure 会话 Cookie')
        return cls(value, secure, origins)


def csrf(token):
    return hmac.new(token.encode(), b'ember-csrf-v1', hashlib.sha256).hexdigest()


def token_from(scope):
    from http.cookies import SimpleCookie, CookieError
    try:
        cookies = SimpleCookie()
        cookies.load(dict(scope['headers']).get(b'cookie', b'').decode('latin1'))
        return cookies[COOKIE].value if COOKIE in cookies else ''
    except CookieError:
        return ''


def allowed_origin(scope, config):
    headers = dict(scope['headers'])
    origin = headers.get(b'origin', b'').decode('latin1')
    if not origin:
        return scope['type'] != 'websocket'
    own = ('https' if scope.get('scheme') in ('https','wss') else 'http') + '://' + headers.get(b'host', b'').decode('latin1')
    return origin == own or origin in config.origins


class OwnerMiddleware:
    def __init__(self, app, store, config):
        self.app, self.store, self.config = app, store, config

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        path = scope['path']
        public_file = path in ('/', '/index.html', '/favicon.ico', '/live2d.min.js', '/live2dcubismcore.min.js', '/bg.png') or path.startswith(('/assets/','/models/','/scenes/'))
        if scope['type'] == 'http' and (public_file or path.startswith('/health/') or scope['method'] == 'OPTIONS'):
            return await self.app(scope, receive, send)
        if not allowed_origin(scope, self.config):
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 4403})
            return await JSONResponse({'detail': '此来源未获准连接'}, 403)(scope, receive, send)
        if path in ('/auth/login', '/auth/session') and scope['type'] == 'http':
            return await self.app(scope, receive, send)
        token = token_from(scope)
        try:
            session = await asyncio.to_thread(self.store.session, token, self.config.credential)
        except Exception:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1013})
            return await JSONResponse({'detail': '登录服务暂不可用，请稍后重试'}, 503)(scope, receive, send)
        if not session:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 4401})
            return await JSONResponse({'detail': '请先连接你的设备', 'code': 'authentication_required'}, 401)(scope, receive, send)
        scope['owner_session'] = session
        if scope['type'] == 'http':
            if scope['method'] not in ('GET','HEAD') and not hmac.compare_digest(
                    dict(scope['headers']).get(b'x-ember-csrf', b'').decode('latin1'), csrf(token)):
                return await JSONResponse({'detail': '操作凭据已失效，请重新连接'}, 403)(scope, receive, send)
            async def private_send(message):
                if message['type'] == 'http.response.start':
                    message['headers'] = list(message.get('headers', [])) + [(b'cache-control', b'no-store')]
                await send(message)
            return await self.app(scope, receive, private_send)
        closed = False
        async def valid():
            nonlocal closed
            try:
                ok = await asyncio.to_thread(self.store.session, token, self.config.credential)
            except Exception:
                ok = None
            if not ok and not closed:
                closed = True
                await send({'type': 'websocket.close', 'code': 4401})
            return bool(ok)
        async def guarded_receive():
            message = await receive()
            if message['type'] == 'websocket.receive' and not await valid():
                return {'type': 'websocket.disconnect', 'code': 4401}
            return message
        async def guarded_send(message):
            if closed:
                return
            if message['type'] == 'websocket.send' and not await valid():
                return
            await send(message)
        await self.app(scope, guarded_receive, guarded_send)


def install_auth(app, store=None, config=None):
    if store is None:
        @app.get('/auth/session')
        async def local_session():
            return {'required': False, 'authenticated': False}
        return
    app.add_middleware(OwnerMiddleware, store=store, config=config)

    class Login(BaseModel):
        access_code: str = Field(min_length=1, max_length=256)
        label: str = Field(default='我的设备', max_length=80)

    @app.get('/auth/session')
    async def session(request: Request):
        token = request.cookies.get(COOKIE, '')
        try:
            record = await asyncio.to_thread(store.session, token, config.credential)
        except Exception as exc:
            raise HTTPException(503, '连接服务暂不可用，请重试') from exc
        return JSONResponse({'required': True, 'authenticated': bool(record), 'session_id': record['id'] if record else None,
            'csrf': csrf(token) if record else None}, headers={'Cache-Control': 'no-store'})

    @app.post('/auth/login')
    async def login(body: Login, request: Request):
        if not await asyncio.to_thread(store.login_allowed, request.client.host if request.client else ''):
            raise HTTPException(429, '尝试次数过多，请五分钟后再试')
        if not await asyncio.to_thread(verify, body.access_code, config.verifier):
            raise HTTPException(401, '访问码不正确')
        token = secrets.token_urlsafe(32)
        key = await asyncio.to_thread(store.issue, token, config.credential, body.label, config.lifetime)
        response = JSONResponse({'authenticated': True, 'required': True, 'csrf': csrf(token), 'session_id': key})
        response.headers['Cache-Control'] = 'no-store'
        response.set_cookie(COOKIE, token, max_age=config.lifetime, httponly=True, secure=config.secure, samesite='strict')
        return response

    @app.post('/auth/logout')
    async def logout(request: Request):
        await asyncio.to_thread(store.revoke, request.scope['owner_session']['id'])
        response = JSONResponse({'success': True})
        response.delete_cookie(COOKIE, secure=config.secure, httponly=True, samesite='strict')
        return response

    @app.get('/auth/devices')
    async def devices():
        return {'items': await asyncio.to_thread(store.devices, config.credential)}

    @app.post('/auth/devices/{key}/revoke')
    async def revoke(key: str):
        await asyncio.to_thread(store.revoke, key)
        return {'success': True}
