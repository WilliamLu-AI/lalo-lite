"""HTTP meeting lifecycle; media and ongoing commands use separate transports."""
import ipaddress
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .meeting import MeetingRegistry
from .meeting_access import meeting_public_origin


class DocumentBlock(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(max_length=12000)
    heading: bool = False
    image: str = Field(default='', max_length=220000)
    imageIndex: int | None = Field(default=None, ge=1, le=2000)


class SharedDocument(BaseModel):
    name: str = Field(max_length=300)
    html: str = Field(max_length=3000000)


class VoiceSelection(BaseModel):
    asr: str = Field(min_length=1, max_length=100)
    tts: str = Field(min_length=1, max_length=100)
    llm: str = Field(min_length=1, max_length=100)


class ExecutionConfiguration(BaseModel):
    model_config = {'extra': 'forbid'}
    mode: Literal['cloud', 'codex', 'claude', 'tiered'] = 'cloud'
    source: str = Field(default='', max_length=100)
    host: Literal['', 'codex', 'claude'] = ''
    background: str = Field(default='', max_length=50000)
    cwd: str = Field(default='', max_length=2048)
    codexModel: Literal['', 'gpt-6.1-sol', 'gpt-6-astra'] = ''
    voiceProfiles: VoiceSelection | None = None


class CreateMeeting(BaseModel):
    blocks: list[DocumentBlock] = Field(min_length=1, max_length=2000)
    configuration: ExecutionConfiguration = Field(default_factory=ExecutionConfiguration)
    document: SharedDocument | None = None


class JoinMeeting(BaseModel):
    invitation: str = Field(min_length=1, max_length=100)


def meeting_router(authorized, same_origin, settings):
    transports = {}
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            for transport in transports.values():
                await transport.aclose()
            transports.clear()
    router = APIRouter(prefix='/api/meetings', lifespan=lifespan)
    registry = MeetingRegistry()

    def result(operation, status=200):
        try:
            return JSONResponse(operation(), status_code=status,
                                headers={'Cache-Control': 'no-store'})
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except ValueError as error:
            raise HTTPException(410, str(error)) from None

    @router.post('')
    async def create(data: CreateMeeting, request: Request):
        same_origin(request)
        try:
            local = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
        except ValueError:
            local = False
        if not local or not authorized(request):
            raise HTTPException(403, 'Create meetings from the host application')
        blocks = [block.model_dump(exclude_none=True) for block in data.blocks]
        if len({block['id'] for block in blocks}) != len(blocks):
            raise HTTPException(422, 'Duplicate document block IDs')
        if sum(len(block['text']) for block in blocks) > 100000:
            raise HTTPException(413, 'Document exceeds 100000 characters')
        if sum(len(block.get('image', '')) for block in blocks) > 2000000:
            raise HTTPException(413, 'Document images exceed 2 MB limit')
        def create_result():
            created = registry.create(blocks,
                configuration=data.configuration.model_dump(exclude_none=True),
                document=data.document.model_dump() if data.document else None)
            origin = meeting_public_origin(settings)
            if origin:
                created['invitation_url'] = origin + '/#meeting=' + created['invitation']
            return created
        return result(create_result, 201)

    @router.post('/join')
    async def join(data: JoinMeeting, request: Request):
        same_origin(request)
        return result(lambda: registry.join(data.invitation))

    @router.post('/end')
    async def end(request: Request):
        same_origin(request)
        credential = request.headers.get('authorization', '').removeprefix('Bearer ')
        try:
            meeting = registry.end(credential)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except ValueError as error:
            raise HTTPException(410, str(error)) from None
        transport = transports.get(meeting)
        if transport:
            try:
                await transport.end()
            except Exception:
                raise HTTPException(503, 'Meeting ended; Room cleanup failed. Retry ending the meeting.') from None
            transports.pop(meeting, None)
        return JSONResponse({'ended': True}, headers={'Cache-Control': 'no-store'})

    @router.post('/room')
    async def room(request: Request):
        same_origin(request)
        credential = request.headers.get('authorization', '').removeprefix('Bearer ')
        try:
            meeting, identity = registry.resolve(credential)
        except PermissionError as error:
            raise HTTPException(403, str(error)) from None
        except ValueError as error:
            raise HTTPException(410, str(error)) from None
        values = [settings.get(name) for name in ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET')]
        if not all(values):
            raise HTTPException(503, 'LiveKit Room configuration is incomplete')
        if meeting not in transports:
            from .transport.meeting_room import LiveKitMeetingTransport
            transports[meeting] = LiveKitMeetingTransport(meeting, *values)
        transport = transports[meeting]
        try:
            await transport.start(identity, providers=getattr(request.app.state, 'providers', None),
                                  settings=settings,
                                  voice_store=getattr(request.app.state, 'voice_store', None))
        except Exception:
            raise HTTPException(503, 'Could not connect the meeting service to LiveKit') from None
        return JSONResponse({'url': settings.get('LIVEKIT_PUBLIC_URL', transport.url), 'token': transport.token(identity),
                             'identity': identity, 'room': transport.room_name},
                            headers={'Cache-Control': 'no-store'})

    return router
