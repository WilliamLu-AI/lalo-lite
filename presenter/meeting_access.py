"""Keep local application controls private when serving meeting guests on LAN."""
import ipaddress
import re
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware


def meeting_public_origin(settings):
    value = settings.get('LALO_MEETING_PUBLIC_URL').rstrip('/')
    if not value:
        return ''
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment
            or not re.fullmatch(r'[a-zA-Z0-9.-]+', parsed.hostname)):
        raise ValueError('LALO_MEETING_PUBLIC_URL must be an HTTPS origin without a path or credentials')
    parsed.port  # Validate the optional port before using the origin.
    return value


class MeetingTrustedHost:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        try:
            origin = meeting_public_origin(self.settings)
        except ValueError:
            if scope['type'] == 'websocket':
                return await send({'type':'websocket.close', 'code':1008})
            return await JSONResponse({'detail':'Invalid meeting public URL configuration'}, status_code=503)(scope, receive, send)
        hosts = ['localhost', '127.0.0.1', '[::1]', 'testserver']
        if origin:
            hosts.append(urlsplit(origin).hostname)
        await TrustedHostMiddleware(self.app, allowed_hosts=hosts)(scope, receive, send)


class MeetingGuestBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        peer = scope.get('client')
        try:
            local = bool(peer and ipaddress.ip_address(peer[0]).is_loopback)
        except ValueError:
            local = False
        path = scope.get('path', '')
        public = (scope['type'] == 'http' and (
            (scope.get('method') in ('GET', 'HEAD') and (path == '/' or path.startswith('/static/')))
            or (scope.get('method') == 'POST' and path in (
                '/api/meetings/join', '/api/meetings/room', '/api/meetings/end'))))
        if local or public:
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
        else:
            await JSONResponse({'detail': 'Local application access required'}, status_code=403)(scope, receive, send)
