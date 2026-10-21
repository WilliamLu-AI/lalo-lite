import asyncio
import inspect
import json
import os
from async_timeout import timeout
from .providers import ProviderError
from .settings import ROOT

ANSWER_SCHEMA = {'type': 'object',
    'properties': {'speech': {'type': 'string'},
                   'blockId': {'type': ['string', 'null']},
                   'advanceTo': {'type': ['string', 'null']}},
    'required': ['speech', 'blockId', 'advanceTo'],
    'additionalProperties': False}


class ClaudeContext:
    """No tools or hooks. Fork the supplied source session and preserve its history."""
    CLOSE_TIMEOUT = 3

    def __init__(self, command, source='', cwd=ROOT, on_context=None):
        self.command = json.loads(command)
        self.source = source
        self.cwd = cwd
        self.on_context = on_context
        self.process = None
        self.turn = None
        self._reported = False

    async def answer(self, messages):
        content = ''.join([part async for part in self.answer_stream(messages)]).strip()
        if content.startswith('```'):
            content = '\n'.join(content.splitlines()[1:-1])
        try:
            return json.loads(content)
        except ValueError:
            raise ProviderError('Claude Code 返回内容无法解析，请重试') from None

    async def answer_stream(self, messages):
        args = [*self.command, '--safe-mode', '--tools', '', '--strict-mcp-config',
                '--effort', 'low', '--output-format', 'stream-json', '--verbose',
                '--include-partial-messages', '--json-schema', json.dumps(ANSWER_SCHEMA)]
        if self.source:
            args += ['--resume', self.source, '--fork-session']
        prompt = ('Use inherited conversation and supplied materials. Return only the requested JSON object.\n'
                  + json.dumps(messages, ensure_ascii=False))
        args += ['-p']
        self.turn = 'pending'  # Busy before the CLI even starts, so cancel() can interrupt.
        self.process = await asyncio.create_subprocess_exec(*args, cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=8 * 1024 * 1024)
        emitted = False
        completed = False
        try:
            async with timeout(240):
                self.process.stdin.write(prompt.encode('utf-8'))
                await self.process.stdin.drain()
                pending = self.process.stdin.close()
                if inspect.isawaitable(pending):
                    await pending
                while True:
                    raw = await self.process.stdout.readline()
                    if not raw:
                        break
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    session_id = event.get('session_id')
                    if session_id and session_id != self.source and not self._reported:
                        self.source = session_id
                        self._reported = True
                        if self.on_context:
                            await self.on_context(session_id, event.get('model'))
                    if event.get('type') == 'stream_event':
                        delta = event.get('event', {}).get('delta', {})
                        if delta.get('type') == 'text_delta':
                            emitted = True
                            yield delta.get('text', '')
                    elif event.get('type') == 'result':
                        if event.get('is_error'):
                            raise ProviderError('Claude Code response failed')
                        self.source = event.get('session_id') or self.source
                        completed = True
                        if not emitted:
                            yield event.get('result', '')
                await self.process.wait()
                if self.process.returncode or not completed:
                    raise ProviderError('Claude Code process failed')
        finally:
            self.turn = None
            await self.close()

    async def close(self):
        process = self.process
        if not process:
            return
        self.process = None
        # All shutdown branches must be bounded: descendants can keep pipe handles open.
        try:
            if process.returncode is None:
                pending = process.stdin.close()
                if inspect.isawaitable(pending):
                    await pending
                try:
                    await asyncio.wait_for(process.communicate(), self.CLOSE_TIMEOUT)
                    return
                except asyncio.TimeoutError:
                    if os.name == 'nt':
                        killer = await asyncio.create_subprocess_exec(
                            'taskkill', '/PID', str(process.pid), '/T', '/F',
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                            creationflags=0x08000000)
                        try:
                            await asyncio.wait_for(killer.wait(), self.CLOSE_TIMEOUT)
                        except asyncio.TimeoutError:
                            killer.kill()
                    elif process.returncode is None:
                        process.kill()
            try:
                await asyncio.wait_for(process.communicate(), self.CLOSE_TIMEOUT)
            except asyncio.TimeoutError:
                pass
        finally:
            # Python 3.10 on Windows may retain inherited stdout after process exit.
            transport = getattr(process, '_transport', None)
            if transport:
                transport.close()
