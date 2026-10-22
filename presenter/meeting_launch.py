"""Validated opt-in HTTPS listener configuration for LAN meetings."""
from pathlib import Path
import ssl
from urllib.parse import urlsplit

from .meeting_access import meeting_public_origin


def listener_options(settings):
    required = ('LALO_MEETING_PUBLIC_URL', 'LALO_MEETING_TLS_CERT', 'LALO_MEETING_TLS_KEY',
                'LIVEKIT_URL', 'LIVEKIT_PUBLIC_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET')
    missing = [key for key in required if not settings.get(key)]
    if missing:
        raise ValueError('Meeting configuration missing: ' + ', '.join(missing))
    origin = meeting_public_origin(settings)
    for key, schemes in (('LIVEKIT_URL', ('ws', 'wss')), ('LIVEKIT_PUBLIC_URL', ('wss',))):
        value = urlsplit(settings.get(key))
        if value.scheme not in schemes or not value.hostname or value.username or value.password or value.fragment or value.query:
            raise ValueError(key + ' must be a WebSocket URL without credentials, query or fragment')
        value.port
    certificate = Path(settings.get('LALO_MEETING_TLS_CERT')).expanduser().resolve(strict=True)
    private_key = Path(settings.get('LALO_MEETING_TLS_KEY')).expanduser().resolve(strict=True)
    # Fail before opening the listener when the certificate/key do not match.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    ca = settings.get('LALO_MEETING_TLS_CA')
    if ca:
        ca = str(Path(ca).expanduser().resolve(strict=True))
        ssl.create_default_context(cafile=ca)
    return {'port': urlsplit(origin).port or 443, 'public_url': origin,
            'certfile': str(certificate), 'keyfile': str(private_key), 'ca': ca}
