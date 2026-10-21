import asyncio
import json
import os
from async_timeout import timeout
from .settings import ROOT
from .providers import ProviderError


class CodexContext:
    """Private ephemeral fork; never resume or write to the original thread."""
    def __init__(self, binary, cwd=ROOT, proxy='', on_status=None, model=''):
        self.requested_model = model
        self.binary = binary
        self.cwd = cwd
        self.proxy = proxy
        self.on_status = on_status
        self.process = None
        self.seq = 0
        self.thread = None
        self.turn = None
        self.pending = []

    async def write(self, value):
        self.process.stdin.write((json.dumps(value) + '\n').encode())
        await self.process.stdin.drain()

    async def read(self):
        while True:
            raw = await self.process.stdout.readline()
            if not raw:
                raise ProviderError('Codex process disconnected')
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if 'method' in data and 'id' in data:
                await self.write({'id': data['id'], 'error': {'code': -32601,
                    'message': 'Presenter does not execute tools or approve actions'}})
                continue
            if data.get('method') == 'error':
                if data.get('params', {}).get('willRetry') and self.on_status:
                    await self.on_status('Codex 连接异常，正在重试，请稍候')
                elif not data.get('params', {}).get('willRetry'):
                    raise ProviderError('Codex 请求失败，请检查登录状态与网络连接')
            return data

    async def request(self, method, params):
        self.seq += 1
        request_id = self.seq
        await self.write({'id': request_id, 'method': method, 'params': params})
        async with timeout(45):
            while True:
                result = await self.read()
                if result.get('id') == request_id:
                    if 'error' in result:
                        if method == 'thread/fork':
                            raise ProviderError('无法分叉源会话：请从原 Codex 聊天重新运行讲解 Skill，并确认该会话在本机可用。未切换为空白会话。')
                        raise ProviderError('Codex rejected ' + method)
                    return result['result']
                if result.get('method') in ('item/agentMessage/delta', 'turn/completed'):
                    self.pending.append(result)

    async def start(self, source):
        self.turn = 'starting'
        env = dict(os.environ)
        if self.proxy:
            for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'WS_PROXY', 'WSS_PROXY'):
                env[key] = self.proxy
                env.pop(key.lower(), None)
        self.process = await asyncio.create_subprocess_exec(self.binary, 'app-server',
            cwd=self.cwd, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=8 * 1024 * 1024)
        await self.request('initialize', {'clientInfo': {'name': 'ai_presenter', 'version': '0.1'},
                                         'capabilities': {'experimentalApi': True}})
        await self.write({'method': 'initialized', 'params': {}})
        params = {'ephemeral': True, 'cwd': str(self.cwd), 'approvalPolicy': 'never',
                  'sandbox': 'read-only'}
        if self.requested_model:params['model']=self.requested_model
        if source:
            params.update({'threadId': source, 'excludeTurns': True})
        result = await self.request('thread/fork' if source else 'thread/start', params)
        self.thread = result['thread']['id']
        self.model = result.get('model') or result['thread'].get('model')
        self.turn = None

    async def answer(self, messages):
        text = ''.join([delta async for delta in self.answer_stream(messages)]).strip()
        if text.startswith('```'):
            text = '\n'.join(text.splitlines()[1:-1])
        return json.loads(text)

    async def answer_stream(self, messages):
        image_inputs=[]
        safe_messages=[]
        for message in messages:
            content=message.get('content')
            if isinstance(content,list):
                for part in content:
                    if part.get('type')=='image_url':image_inputs.append({'type':'image','url':part['image_url']['url']})
                safe_messages.append({**message,'content':'\n'.join(p.get('text','') for p in content if p.get('type')=='text')})
            else:safe_messages.append(message)
        prompt = ('Do not use tools, execute commands, edit files or delegate. Answer from inherited '
                  'conversation and supplied document. Return ONLY the requested JSON object.\n' +
                  json.dumps(safe_messages, ensure_ascii=False))
        self.turn = 'pending'  # Busy even before the RPC returns its server turn ID.
        result = await self.request('turn/start', {'threadId': self.thread, 'effort': 'low',
            'input': [{'type': 'text', 'text': prompt}, *image_inputs]})
        self.turn = result.get('turn', {}).get('id')
        async with timeout(240):
            while True:
                event = self.pending.pop(0) if self.pending else await self.read()
                params = event.get('params', {})
                event_turn = params.get('turnId') or params.get('turn', {}).get('id')
                if event_turn and event_turn != self.turn:
                    continue
                if event.get('method') == 'item/agentMessage/delta':
                    yield event['params'].get('delta', '')
                elif event.get('method') == 'turn/completed':
                    if event['params']['turn']['status'] != 'completed':
                        raise ProviderError('Codex turn did not complete')
                    break
        self.turn = None

    CLOSE_TIMEOUT = 3

    async def close(self):
        process = self.process
        if not process:
            return
        self.process = None
        self.turn = None
        self.pending.clear()
        # All shutdown branches must be bounded: descendants can keep pipe handles open.
        try:
            if process.returncode is None:
                process.stdin.close()
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
