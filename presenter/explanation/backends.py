"""Business backend selection and source-conversation lifecycle."""
from dataclasses import dataclass
from pathlib import Path
from ..tiered import TieredBackend


@dataclass(frozen=True)
class BackendBinding:
    mode: str
    host: str
    source: str
    cwd: Path
    model: str = ''


class BackendRouter:
    def __init__(self, settings, notify, *, codex_factory, claude_factory, audit=None):
        self.settings, self.notify = settings, notify
        self.codex_factory, self.claude_factory = codex_factory, claude_factory
        self.current = None
        self.audit = audit or (lambda kind, **fields: None)

    async def resolve(self, binding, providers, *, notify=None):
        emit = notify or self.notify
        async def deep_backend():
            if not self.current:
                if binding.mode == 'claude' or (binding.mode == 'tiered' and binding.host == 'claude'):
                    async def claude_context(session_id, model):
                        await emit('context_ready', mode=binding.mode, source=binding.source, host=binding.host, thread=session_id, model=model)
                        self.audit('claude_ready', session=session_id, model=model, effort='low')
                    await emit('status', message='正在分叉 Claude Code 源会话并继承上下文' if binding.source else '正在创建 Claude Code 新会话（未继承聊天历史）')
                    self.current = self.claude_factory(self.settings.get('CLAUDE_COMMAND'), binding.source, binding.cwd, on_context=claude_context)
                    await emit('status', message='Claude Code 正在生成回答')
                else:
                    async def codex_status(message):
                        await emit('status', message=message)
                    await emit('status', message='正在分叉源会话并继承上下文' if binding.source else '正在创建 Codex 新会话（未继承聊天历史）')
                    self.current = self.codex_factory(self.settings.get('CODEX_BINARY'), binding.cwd,
                        proxy=self.settings.get('CODEX_PROXY'), on_status=codex_status, model=binding.model)
                    await self.current.start(binding.source)
                    await emit('context_ready', mode=binding.mode, source=binding.source, host=binding.host, thread=self.current.thread, model=getattr(self.current, 'model', None))
                    self.audit('codex_ready', thread=self.current.thread, model=getattr(self.current, 'model', None), effort='low')
                    await emit('status', message='Codex 正在生成回答')
            return self.current
        if binding.mode == 'tiered':
            async def route_status(route, reason):
                await emit('routing', route=route, reason=reason)
                if route == 'deep':
                    await emit('status', message='正在调用 Codex 深入分析' if binding.host == 'codex' else '正在调用 Claude Code 深入分析')
            backend = TieredBackend(providers, deep_backend, route_status)
        elif binding.mode in ('codex','claude'):
            backend = await deep_backend()
        else:
            backend = providers
        return backend

    async def cancel(self, reason):
        if self.current and (reason in ('configuration', 'closed') or getattr(self.current, 'turn', None)):
            await self.close()

    async def close(self):
        if self.current:
            current, self.current = self.current, None
            await current.close()
