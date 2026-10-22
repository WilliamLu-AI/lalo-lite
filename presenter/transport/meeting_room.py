"""LiveKit permissions adapter for shared presentations."""
from datetime import timedelta
import asyncio
import json
import logging
import secrets

from livekit import api, rtc
from .websocket import WebSocketAudioOutput
from ..audio import Segmenter


class RoomAudioOutput(WebSocketAudioOutput):
    async def audio(self, index, pcm):
        for offset in range(0, len(pcm), 6400):
            await super().audio(index, pcm[offset:offset + 6400])


class LiveKitMeetingTransport:
    def __init__(self, meeting, url, api_key, api_secret):
        self.meeting = meeting
        self.room_name = 'lalo-' + secrets.token_urlsafe(18)
        self.url = url
        self._key, self._secret = api_key, api_secret
        self._client = None
        self._room = None
        self._worker = None
        self._start_lock = asyncio.Lock()
        self._commands = asyncio.Queue()
        self._seen = {}
        self._sequence = 0
        self._execution = None
        self._audio_tasks = {}
        self._recordings = {}
        self._recognitions = set()
        self._host_utterances = {}

    async def start(self, observer_identity, *, providers=None, settings=None, voice_store=None):
        self.meeting.snapshot(observer_identity)
        async with self._start_lock:
            if self._room:
                return
            room = rtc.Room()
            @room.on('track_subscribed')
            def subscribed(track, publication, participant):
                if (track.kind == rtc.TrackKind.KIND_AUDIO
                        and publication.source == rtc.TrackSource.SOURCE_MICROPHONE):
                    stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1,
                                             frame_size_ms=20, capacity=100)
                    task = asyncio.create_task(self._capture(participant.identity, stream))
                    self._audio_tasks[publication.sid] = task
            @room.on('track_unsubscribed')
            def unsubscribed(track, publication, participant):
                task = self._audio_tasks.pop(publication.sid, None)
                if task:
                    task.cancel()
            @room.on('data_received')
            def received(packet):
                if (packet.topic != 'lalo-meeting-command' or not packet.participant
                        or len(packet.data) > 16000 or self._commands.qsize() >= 64):
                    return
                try:
                    message = json.loads(packet.data)
                    if isinstance(message, dict):
                        self._commands.put_nowait((packet.participant.identity, message, False))
                except (ValueError, asyncio.QueueFull):
                    pass
            @room.on('participant_connected')
            def connected(participant):
                self._commands.put_nowait((participant.identity, {'type': 'connected'}, True))
            @room.on('participant_disconnected')
            def disconnected(participant):
                self._commands.put_nowait((participant.identity, {'type': 'disconnected'}, True))
            token = api.AccessToken(self._key, self._secret).with_identity('lalo-ai').with_grants(
                api.VideoGrants(room_join=True, room=self.room_name,
                                can_publish=True, can_subscribe=True, can_publish_data=True)).to_jwt()
            try:
                await asyncio.wait_for(room.connect(self.url, token), 15)
            except BaseException:
                await room.disconnect()
                raise
            self._room = room
            try:
                if providers is not None:
                    from ..meeting_session import MeetingSession
                    self._execution = MeetingSession(self.meeting, providers, settings,
                                                     self.broadcast, audio_output_factory=RoomAudioOutput,
                                                     voice_store=voice_store)
                    await self._execution.start(observer_identity)
            except BaseException:
                await self.aclose()
                raise
            self._worker = asyncio.create_task(self._control_loop())

    async def broadcast(self, event):
        for identity in list(self._room.remote_participants):
            try:
                self.meeting.snapshot(identity)
            except (PermissionError, ValueError):
                continue
            await self._send(identity, event)
            if event.get('type') == 'error':
                await self._send(identity, {'type': 'state',
                    'snapshot': self.meeting.snapshot(identity, include_document=False)})

    async def _capture(self, identity, stream):
        segmenter = Segmenter()
        utterance = None
        try:
            async for event in stream:
                try:
                    state = self.meeting.snapshot(identity)
                except (PermissionError, ValueError):
                    break
                if state['role'] == 'host' and self._execution:
                    question = state['active_question']
                    if question and question.get('kind') == 'speech' and question['speaker'] != identity:
                        segmenter = Segmenter()
                        utterance = None
                        continue
                    pcm = bytes(event.frame.data)
                    for offset in range(0, len(pcm) - 639, 640):
                        action = segmenter.feed(pcm[offset:offset + 640])
                        if action == 'start':
                            utterance = secrets.token_urlsafe(12)
                            self._commands.put_nowait((identity, {'type':'host_voice_start', 'utterance':utterance}, True))
                        elif action in ('endpoint', 'limit'):
                            audio = segmenter.take()
                            self._commands.put_nowait((identity, {'type':'host_voice_end', 'utterance':utterance, 'pcm':audio}, True))
                            utterance = None
                    continue
                if state['floor'] != identity:
                    continue
                question_id = state['active_question']['id']
                recording = self._recordings.get(identity)
                if recording is None or recording[0] != question_id:
                    recording = (question_id, bytearray())
                    self._recordings[identity] = recording
                # Preserve an overflow sentinel so overlong speech is rejected,
                # rather than silently transcribing a truncated question.
                if len(recording[1]) <= 1920000:
                    recording[1].extend(bytes(event.frame.data))
        finally:
            if utterance:
                self._commands.put_nowait((identity, {'type':'host_voice_cancel', 'utterance':utterance}, True))
            await stream.aclose()

    async def _send(self, identity, message):
        self._sequence += 1
        await self._room.local_participant.publish_data(
            json.dumps({**message, 'sequence': self._sequence}, ensure_ascii=False), reliable=True,
            topic='lalo-meeting-events', destination_identities=[identity])

    async def _recognize(self, identity, request_id, generation, pcm):
        try:
            await self._execution.recognize_audio(identity, request_id, generation, pcm)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._send(identity, {'type': 'error', 'stage': 'recognition',
                                        'message': 'Speech recognition failed; please try again'})
        finally:
            self._commands.put_nowait((identity, {'type': 'recognition_finished'}, True))

    async def _service_error(self, identity, request_id=None):
        try:
            await self._send(identity, {'type': 'error', 'stage': 'transport',
                'request_id': request_id, 'message': 'Meeting service temporarily unavailable; synchronize again'})
        except Exception as error:
            logging.getLogger(__name__).warning('Meeting error delivery failed: %s', type(error).__name__)

    async def _control_loop(self):
        while True:
            identity, message, internal = await self._commands.get()
            if internal:
                try:
                    self.meeting.snapshot(identity)
                    if message['type'] == 'host_voice_start' and self._execution:
                        token = await self._execution.command(identity, {'type':'host_speech_start', 'speech_id':message['utterance']})
                        if token:
                            self._host_utterances[identity] = (message['utterance'], token)
                    elif message['type'] in ('host_voice_end', 'host_voice_cancel') and self._execution:
                        active = self._host_utterances.get(identity)
                        if active and active[0] == message['utterance']:
                            self._host_utterances.pop(identity, None)
                            if message['type'] == 'host_voice_cancel':
                                await self._execution.command(identity, {'type':'host_speech_cancel','speech_id':active[1]})
                            else:
                                generation = self._execution.prepare_audio(identity, active[1], message['pcm'])
                                task = asyncio.create_task(self._recognize(identity, active[1], generation, message['pcm']))
                                self._recognitions.add(task)
                                task.add_done_callback(self._recognitions.discard)
                    elif message['type'] == 'connected':
                        self.meeting.join(identity)
                    elif message['type'] == 'disconnected' and self._execution:
                        await self._execution.disconnected(identity)
                    elif message['type'] == 'disconnected':
                        self.meeting.disconnect(identity)
                    await self.sync_floor(identity)
                    for member in list(self._room.remote_participants):
                        await self._send(member, {'type': 'state', 'snapshot': self.meeting.snapshot(member, include_document=False)})
                except (PermissionError, ValueError):
                    pass
                except Exception:
                    await self._service_error(identity)
                continue
            request_id = message.get('request_id')
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
                continue
            try:
                self.meeting.snapshot(identity)
                key = identity, request_id
                signature = json.dumps(message, sort_keys=True)
                if key in self._seen:
                    if self._seen[key] != signature:
                        raise ValueError('Request ID was reused with different content')
                else:
                    if len(self._seen) >= 10000:
                        raise ValueError('Meeting command limit reached')
                    if message.get('type') == 'request_speech':
                        if self._execution:
                            await self._execution.command(identity, message)
                        else:
                            self.meeting.request_speech(identity)
                    elif message.get('type') == 'speech_end':
                        if self._execution is None:
                            raise ValueError('Meeting model execution is unavailable')
                        recording = self._recordings.pop(identity, None)
                        if recording is None:
                            raise ValueError('No microphone audio received')
                        pcm = bytes(recording[1])
                        generation = self._execution.prepare_audio(identity, recording[0], pcm)
                        task = asyncio.create_task(self._recognize(identity, recording[0], generation, pcm))
                        self._recognitions.add(task)
                        task.add_done_callback(self._recognitions.discard)
                    elif message.get('type') in ('ask', 'stop', 'resume', 'navigate', 'cancel_speech', 'sentence_played', 'playback_done'):
                        if self._execution is None:
                            raise ValueError('Meeting model execution is unavailable')
                        await self._execution.command(identity, message)
                        if message.get('type') == 'cancel_speech':
                            self._recordings.pop(identity, None)
                    elif message.get('type') == 'sync':
                        if (message.get('recovering') is True
                                and self.meeting.snapshot(identity)['role'] == 'host'):
                            if self._execution:
                                await self._execution.command(identity, {'type': 'stop'})
                            else:
                                self.meeting.stop(identity)
                    else:
                        raise ValueError('Unsupported meeting command')
                    self._seen[key] = signature
                await self.sync_floor(identity)
                for member in list(self._room.remote_participants):
                    try:
                        snapshot = self.meeting.snapshot(member, include_document=False)
                    except PermissionError:
                        continue
                    await self._send(member, {'type': 'state', 'snapshot': snapshot,
                                             'request_id': request_id if member == identity else None})
            except (PermissionError, ValueError) as error:
                await self._send(identity, {'type': 'error', 'request_id': request_id,
                                            'message': str(error)})
            except Exception:
                await self._service_error(identity, request_id)

    def token(self, identity):
        state = self.meeting.snapshot(identity)
        # Host can listen continuously; attendees require the speaking floor.
        return api.AccessToken(self._key, self._secret).with_identity(identity).with_ttl(
            timedelta(minutes=10)).with_grants(api.VideoGrants(
                room_join=True, room=self.room_name, can_subscribe=True,
                can_publish=state['role'] == 'host', can_publish_data=True,
                can_update_own_metadata=False)).to_jwt()

    async def sync_floor(self, observer_identity):
        floor = self.meeting.snapshot(observer_identity)['floor']
        if self._client is None:
            self._client = api.LiveKitAPI(self.url.replace('wss:', 'https:').replace('ws:', 'http:'),
                                         self._key, self._secret)
        participants = await self._client.room.list_participants(
            api.ListParticipantsRequest(room=self.room_name))
        # Attendees share one floor; the host keeps its continuous input track.
        for participant in sorted(participants.participants, key=lambda p: p.identity == floor):
            if participant.identity == 'lalo-ai':
                continue
            await self._client.room.update_participant(api.UpdateParticipantRequest(
                room=self.room_name, identity=participant.identity,
                permission=api.ParticipantPermission(
                    can_subscribe=True, can_publish=(participant.identity == floor or self.meeting.snapshot(participant.identity)['role'] == 'host'),
                    can_publish_data=True, can_publish_sources=[api.TrackSource.MICROPHONE])))

    async def aclose(self):
        recognition_tasks = list(self._recognitions)
        for task in recognition_tasks:
            task.cancel()
        await asyncio.gather(*recognition_tasks, return_exceptions=True)
        self._recognitions.clear()
        tasks = list(self._audio_tasks.values())
        self._audio_tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._recordings.clear()
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        if self._execution:
            await self._execution.aclose()
            self._execution = None
        if self._room:
            await self._room.disconnect()
            self._room = None
        if self._client:
            await self._client.aclose()
            self._client = None

    async def end(self):
        """Terminate the server room, disconnecting every current participant."""
        if self._room:
            for identity in list(self._room.remote_participants):
                try:
                    await self._send(identity, {'type': 'meeting_ended'})
                except Exception:
                    pass  # Deleting the SFU room remains the authoritative cleanup.
        client = self._client or api.LiveKitAPI(
            self.url.replace('wss:', 'https:').replace('ws:', 'http:'), self._key, self._secret)
        self._client = client
        try:
            await client.room.delete_room(api.DeleteRoomRequest(room=self.room_name))
        finally:
            await self.aclose()
