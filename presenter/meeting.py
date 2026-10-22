"""Shared presentation state, independent of media and model providers.

Participant identities must come from the authenticated transport, never from
an untrusted command payload. Invitation validation belongs at that boundary.
"""
from copy import deepcopy
import secrets


class Meeting:
    def __init__(self, host_identity, blocks, *, configuration=None, document=None):
        self._host = host_identity
        self._members = {host_identity}
        self._blocks = deepcopy(blocks)
        self._configuration = deepcopy(configuration or {})
        self._document = deepcopy(document)
        self._current = blocks[0]['id'] if blocks else None
        self._ended = False
        self._phase = 'paused'
        self._active_question = None
        self._queue = []
        self._host_online = True
        self._held = False
        self._recognition = 0

    def disconnect(self, identity):
        self._require_open()
        if identity == self._host:
            self._recognition += 1
            self._host_online = False
            self._held = True
            self._phase = 'paused'
        else:
            self._queue = [q for q in self._queue
                           if not (q['speaker'] == identity and q.get('kind') == 'speech')]
            question = self._active_question
            if question and question['speaker'] == identity and question.get('kind') == 'speech':
                self._recognition += 1
                self._active_question = self._queue.pop(0) if self._queue else None
                if self._held or not self._active_question:
                    self._phase = 'paused'
                else:
                    self._phase = 'listening' if self._active_question.get('kind') == 'speech' else 'answering'

    def stop(self, identity):
        self._require_open()
        if identity != self._host:
            raise PermissionError('Only the host can stop the meeting')
        self._held = True
        self._phase = 'paused'
        self._recognition += 1

    def execution_failed(self):
        """Trusted execution failure: retain the question for explicit host retry."""
        self._require_open()
        self._held = True
        self._phase = 'paused'
        self._recognition += 1

    def resume(self, identity):
        self._require_open()
        if identity != self._host:
            raise PermissionError('Only the host can resume')
        if not self._host_online:
            raise ValueError('Host is offline')
        if self._active_question and not self._held:
            raise ValueError('An answer is still in progress')
        self._held = False
        self._phase = ('listening' if self._active_question.get('kind') == 'speech' else 'answering') if self._active_question else 'presenting'

    def host_speech_start(self, identity):
        self._require_open()
        if identity != self._host:
            raise PermissionError('Only the host can use continuous listening')
        question = self._active_question
        if question and question.get('kind') == 'speech' and question['speaker'] != identity:
            return False  # Do not steal an attendee's granted or recognizing turn.
        self._recognition += 1
        self._held = False
        self._active_question = {'id': secrets.token_urlsafe(18), 'speaker': identity,
                                 'text': None, 'kind': 'speech'}
        self._phase = 'listening'
        return True

    def request_speech(self, identity):
        self._require_open()
        if identity not in self._members:
            raise PermissionError('Participant has not joined')
        if len(self._queue) >= 32:
            raise ValueError('Question queue is full')
        question = {'id': secrets.token_urlsafe(18), 'speaker': identity,
                    'text': None, 'kind': 'speech'}
        if self._active_question is None:
            self._active_question = question
            self._phase = 'paused' if self._held else 'listening'
        else:
            self._queue.append(question)
        return question['id']

    def submit_speech(self, identity, request_id, text):
        self._require_open()
        question = self._active_question
        if (self._phase != 'listening' or not question or
                question['id'] != request_id or question['speaker'] != identity):
            raise PermissionError('Participant does not hold the speaking floor')
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Question must contain 1 to 4000 characters')
        question.update(text=text.strip(), kind='text')
        self._phase = 'answering'

    def cancel_speech(self, identity, request_id):
        self._require_open()
        if identity not in self._members:
            raise PermissionError('Participant has not joined')
        questions = ([self._active_question] if self._active_question else []) + self._queue
        question = next((q for q in questions if q['id'] == request_id), None)
        if question is None:
            return
        if question['speaker'] != identity or question.get('kind') != 'speech':
            raise PermissionError('Only the speaker can cancel an unsubmitted speaking request')
        if question is self._active_question:
            self._recognition += 1
            self._active_question = self._queue.pop(0) if self._queue else None
            self._phase = ('paused' if self._held or not self._active_question else
                           'listening' if self._active_question.get('kind') == 'speech' else 'answering')
        else:
            self._queue.remove(question)

    def begin_recognition(self, identity, request_id):
        state = self.snapshot(identity)
        if state['floor'] != identity or state['active_question']['id'] != request_id:
            raise PermissionError('Participant does not hold the speaking floor')
        self._recognition += 1
        self._phase = 'recognizing'
        return self._recognition

    def finish_recognition(self, identity, request_id, generation, text):
        self._require_open()
        if (generation != self._recognition or self._phase != 'recognizing'
                or not self._active_question or self._active_question['id'] != request_id
                or self._active_question['speaker'] != identity):
            return False
        self._phase = 'listening'
        if text is not None:
            self.submit_speech(identity, request_id, text)
        return True

    def ask(self, identity, text):
        self._require_open()
        if identity not in self._members:
            raise PermissionError('Participant has not joined')
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Question must contain 1 to 4000 characters')
        if len(self._queue) >= 32:
            raise ValueError('Question queue is full')
        question = {'id': secrets.token_urlsafe(18), 'speaker': identity, 'text': text.strip()}
        if self._active_question is None:
            self._active_question = question
            self._phase = 'paused' if self._held else 'answering'
        else:
            self._queue.append(question)
        return question['id']

    def answer_finished(self, question_id):
        """Called by the execution adapter after acknowledged answer playback."""
        self._require_open()
        if self._held or not self._active_question or self._active_question['id'] != question_id:
            return
        self._active_question = self._queue.pop(0) if self._queue else None
        self._phase = ('listening' if self._active_question.get('kind') == 'speech' else 'answering') if self._active_question else 'paused'

    def _require_open(self):
        if self._ended:
            raise ValueError('Meeting has ended')

    def end(self, identity):
        if identity != self._host:
            raise PermissionError('Only the host can end the meeting')
        self._ended = True
        self._blocks.clear()
        self._configuration.clear()
        self._document = None
        self._queue.clear()
        self._active_question = None

    def join(self, identity):
        self._require_open()
        self._members.add(identity)
        if identity == self._host:
            self._host_online = True
        return self.snapshot(identity)

    def navigate(self, identity, block_id):
        self._require_open()
        if identity != self._host:
            raise PermissionError('Only the host can navigate the meeting')
        if block_id not in {block['id'] for block in self._blocks}:
            raise ValueError('Unknown document block')
        self._current = block_id

    def explanation_position(self, block_id):
        """Apply a validated position emitted by the trusted explanation session."""
        self._require_open()
        if block_id in {block['id'] for block in self._blocks}:
            self._current = block_id

    def snapshot(self, identity, *, include_document=True):
        self._require_open()
        if identity not in self._members:
            raise PermissionError('Participant has not joined')
        def question_view(question):
            if question is None:
                return None
            result = deepcopy(question)
            if not include_document and isinstance(result.get('text'), str):
                result['text_truncated'] = len(result['text']) > 40
                result['text'] = result['text'][:40]
            return result
        return {'role': 'host' if identity == self._host else 'attendee',
                **({'blocks': deepcopy(self._blocks), 'document': deepcopy(self._document)} if include_document else {}), 'current': self._current,
                'phase': self._phase, 'active_question': question_view(self._active_question),
                'host_online': self._host_online,
                'floor': self._active_question['speaker'] if self._phase == 'listening' else None,
                'queue': [question_view(question) for question in self._queue]}

    def execution_configuration(self):
        """Trusted server execution settings; never part of participant snapshots."""
        self._require_open()
        return deepcopy(self._configuration)


class MeetingRegistry:
    """Separate invitation capabilities from authenticated member credentials."""
    def __init__(self):
        self._invitations = {}
        self._credentials = {}

    def create(self, blocks, *, configuration=None, document=None):
        identity = secrets.token_urlsafe(24)
        meeting = Meeting(identity, blocks, configuration=configuration, document=document)
        invitation, credential = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self._invitations[invitation] = meeting
        self._credentials[credential] = meeting, identity
        return {'credential': credential, 'invitation': invitation,
                'snapshot': meeting.snapshot(identity)}

    def join(self, invitation):
        meeting = self._invitations.get(invitation)
        if meeting is None:
            raise PermissionError('Invalid invitation')
        identity, credential = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        snapshot = meeting.join(identity)
        self._credentials[credential] = meeting, identity
        return {'credential': credential, 'snapshot': snapshot}

    def resolve(self, credential):
        entry = self._credentials.get(credential)
        if entry is None:
            raise PermissionError('Invalid participant credential')
        meeting, identity = entry
        meeting.snapshot(identity)
        return meeting, identity

    def end(self, credential):
        entry = self._credentials.get(credential)
        if entry is None:
            raise PermissionError('Invalid participant credential')
        meeting, identity = entry
        meeting.end(identity)
        return meeting
