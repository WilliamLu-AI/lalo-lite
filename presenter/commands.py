"""Shell-free CLI command parsing shared by launch, sessions and diagnostics."""
import json
import shutil
from pathlib import Path


def claude_command(value, *, validate=True):
    try:
        if not isinstance(value,str) or not value.strip(): raise ValueError
        value = value.strip()
        command = json.loads(value) if value.startswith(('[','"','{')) else [value]
        if not isinstance(command,list) or not command or not all(isinstance(p,str) and p for p in command): raise ValueError
        if validate and not (Path(command[0]).is_file() or shutil.which(command[0])): raise ValueError
        return command
    except (ValueError,TypeError,OSError):
        raise ValueError('CLAUDE_COMMAND 未配置、格式错误或命令不存在：填写可执行文件路径；带参数时使用 JSON 数组，如 ["C:/path/claude.exe", "参数"]') from None
