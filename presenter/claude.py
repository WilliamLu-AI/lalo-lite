import asyncio
import inspect
import json
import os
from async_timeout import timeout
from .providers import ProviderError
from .settings import ROOT
from .commands import claude_command

ANSWER_SCHEMA = {'type': 'object',
    'properties': {'speech': {'type': 'string'},
                   'blockId': {'type': ['string', 'null']},
                   'advanceTo': {'type': ['string', 'null']}},
    'required': ['speech', 'blockId', 'advanceTo'],
    'additionalProperties': False}


def checked_answer(value, *, legacy=False):
    """Validate before releasing any text to the speech/navigation pipeline."""
    try:
        if legacy:
            if not isinstance(value,str) or len(value)>32000: raise ValueError
            value = value.strip()
            if value.startswith('```'):
                value = '\n'.join(value.splitlines()[1:-1])
            value = json.loads(value)
        if not isinstance(value,dict): raise ValueError
        if not legacy and set(value)!=set(ANSWER_SCHEMA['required']): raise ValueError
        speech = value.get('speech')
        if not isinstance(speech,str) or not speech.strip() or len(speech)>1600: raise ValueError
        if any(value.get(k) is not None and not isinstance(value[k],str) for k in ('blockId','advanceTo')): raise ValueError
        result = {k:value.get(k) for k in ANSWER_SCHEMA['required']}
        if len(json.dumps(result,ensure_ascii=False))>32000: raise ValueError
        return result
    except (ValueError,TypeError):
        raise ProviderError('Claude Code 返回内容无法解析或不符合讲解格式，请重试', stage='model', code='invalid_answer') from None


class ClaudeContext:
    """No tools or hooks. Fork the supplied source session and preserve its history."""
    CLOSE_TIMEOUT = 3

    def __init__(self, command, source='', cwd=ROOT, on_context=None):
        self.command = claude_command(command, validate=False)
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
        text_parts = []
        tool_blocks = {}
        tool_output = None
        buffered = 0
        result = None
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
                        frame = event.get('event', {})
                        index = frame.get('index')
                        block = frame.get('content_block', {})
                        if frame.get('type') == 'content_block_start':
                            tool_blocks.pop(index, None)
                            if block.get('type')=='tool_use' and block.get('name')=='StructuredOutput':
                                tool_blocks[index] = {'input':block.get('input',{}),'raw':''}
                        delta = frame.get('delta', {})
                        piece = delta.get('text','') if delta.get('type')=='text_delta' else delta.get('partial_json','') if delta.get('type')=='input_json_delta' and index in tool_blocks else ''
                        if not isinstance(piece,str):
                            raise ProviderError('Claude Code returned an invalid stream event',stage='model')
                        buffered += len(piece)
                        if buffered>256000:
                            raise ProviderError('Claude Code response exceeded the capture limit',stage='model',code='response_limit')
                        if delta.get('type') == 'text_delta':
                            text_parts.append(piece)
                        elif delta.get('type')=='input_json_delta' and index in tool_blocks:
                            tool_blocks[index]['raw'] += piece
                        if frame.get('type')=='content_block_stop' and index in tool_blocks:
                            tool = tool_blocks.pop(index)
                            tool_output = tool['raw'] or tool['input']
                    elif event.get('type')=='assistant':
                        for block in event.get('message',{}).get('content',[]):
                            if block.get('type')=='tool_use' and block.get('name')=='StructuredOutput':
                                tool_output = block.get('input')
                    elif event.get('type') == 'result':
                        if event.get('is_error') or str(event.get('subtype','')).startswith('error'):
                            raise ProviderError('Claude Code response failed')
                        self.source = event.get('session_id') or self.source
                        completed = True
                        result = event
                await self.process.wait()
                if self.process.returncode or not completed:
                    raise ProviderError('Claude Code process failed')
                # The terminal structured result wins over ordinary assistant text.
                # Buffer until success so preambles and rejected candidates cannot play.
                if result.get('structured_output') is not None:
                    answer = checked_answer(result['structured_output'])
                else:
                    try:
                        answer = checked_answer(result.get('result'),legacy=True)
                    except ProviderError:
                        if tool_output is not None:
                            try:
                                parsed = json.loads(tool_output) if isinstance(tool_output,str) else tool_output
                            except ValueError:
                                parsed = None
                            answer = checked_answer(parsed)
                        else:
                            answer = checked_answer(''.join(text_parts),legacy=True)
                yield json.dumps(answer,ensure_ascii=False)
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
