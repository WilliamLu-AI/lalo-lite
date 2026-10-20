---
name: present-plan
description: Use Lalo to explain or discuss a Markdown, HTML, or text document from the current Codex or Claude Code conversation.
---

# Present a document with Lalo

1. Identify the user's document and project directory as absolute paths.
2. Locate the app using an explicitly configured LALO_BINARY, otherwise app-path.txt beside this SKILL.md (created by the installer). For the repository copy, use ../../app/windows-x64/lalo.exe relative to this skill directory. Check the executable exists. If missing, direct the user to install-skill.ps1 in the public repository root.
3. Identify the current host and source session. For Codex, use CODEX_THREAD_ID from the tool environment or trusted current-thread metadata. For Claude Code, use the skill-expanded current session value: ${CLAUDE_SESSION_ID}. This is Claude's skill substitution, not a guaranteed shell environment variable. If the value still contains a dollar sign or braces, or no trusted ID exists, explain that the current conversation cannot be bound. Never guess from recent history.
4. Run the app from the current conversation, passing the session explicitly. Do not delegate the launch to another conversation.

```powershell
& '<absolute app path>' open --mode codex --session '<current session ID>' --project '<absolute project path>' --document '<absolute document path>' --no-open
```

For Claude Code, replace --mode codex with --mode claude. Pass paths and IDs as separate arguments. Direct EXE invocation requires no Python; scripts/launch.py is an optional Python adapter.

5. Check that the returned source equals the current session ID. Open the complete returned url in the local browser or browser panel. Without a panel tool, omit --no-open to let the app open the browser. Launch URLs contain private tokens; do not publish them.

The explanation uses a fork of the source conversation and does not write voice questions back into it. The user chooses when to enable the microphone and start model calls. Report startup errors directly; do not read credentials or conversation logs to infer configuration.
