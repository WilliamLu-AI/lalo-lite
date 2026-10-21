---
name: present-plan
description: Use Lalo to explain or discuss a Markdown, HTML, or text document from the current Codex or Claude Code conversation.
---

# Present a document with Lalo

1. Identify the user's document and project directory as absolute paths.
2. Use scripts/launch.py beside this Skill. It resolves the installed source-root.txt pointer or the repository checkout, using that checkout's Python environment. An explicit --app or LALO_BINARY selects an installed executable instead. If setup is missing, follow the repository README and install-skill.ps1.
3. Identify the current host and source session. For Codex, use CODEX_THREAD_ID from the tool environment or trusted current-thread metadata. For Claude Code, use the skill-expanded current session value: ${CLAUDE_SESSION_ID}. This is Claude's skill substitution, not a guaranteed shell environment variable. If the value still contains a dollar sign or braces, or no trusted ID exists, explain that the current conversation cannot be bound. Never guess from recent history.
4. Run the app from the current conversation, passing the session explicitly. Do not delegate the launch to another conversation.

```powershell
python '<absolute Skill path>/scripts/launch.py' --mode codex --session '<current session ID>' --project '<absolute project path>' --document '<absolute document path>' --no-open
```

For Claude Code, replace --mode codex with --mode claude. Tiered mode uses --mode tiered; its deep backend follows the launching tool (Claude Code → Flash + Claude Code, Codex → Flash + Codex). Pass paths and IDs as separate arguments. The source checkout requires its configured Python environment.

5. Check that the returned source equals the current session ID. Open the complete returned url in the local browser or browser panel. Without a panel tool, omit --no-open to let the app open the browser. Launch URLs contain private tokens; do not publish them.

The explanation uses a fork of the source conversation and does not write voice questions back into it. The user chooses when to enable the microphone and start model calls. Report startup errors directly; do not read credentials or conversation logs to infer configuration.
