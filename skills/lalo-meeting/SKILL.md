---
name: lalo-meeting
description: Start or reopen a Lalo multi-person document explanation meeting using LiveKit Room, prepare the host reader and attendee invitation, and diagnose joining problems. Use for shared Lalo meetings rather than single-reader presentation or generic meeting software.
---

# Lalo meeting

Use the formal Lalo reader: the host operates AI and meeting controls in one window; attendees open the invitation on their devices. Keep the existing document layout. This skill operates the product, not the experimental Room demo.

## Locate and prepare

- Resolve the requested document and project to existing absolute paths. Use the current conversation's document when unambiguous; ask for the document only if it is missing. Supported inputs are Markdown, static HTML and text.
- Locate the Lalo source checkout from the current project, explicit user configuration, or a trusted installed launcher. The repository copy of this skill is under `skills/lalo-meeting`; its checkout is two directories above. An installed copy resolves `source-root.txt` beside this SKILL.md, written by `install-skill.ps1 -SkillName lalo-meeting`; verify that directory contains `lalo.py`. If the pointer is missing or stale, ask for the checkout location or rerun its installer. Do not rely on the retired binary-package layout.
- Run from that checkout with its existing `.venv/Scripts/python.exe` on Windows, or `uv run --locked python`. Read `lalo.py --help`, `serve --help` and `open --help` when the installed version differs. Use the same explicit `--home` for every command when a data directory has been selected.
- Inspect `doctor` readiness and the configured meeting listener. Report missing configuration names without exposing values. Doctor alone does not prove that remote browsers can connect or hear audio. If the frontend is absent, use the checkout's documented dependency/build commands.
- For a first setup or connection failure, read [references/connectivity.md](references/connectivity.md). Reuse working local settings, certificate trust and LiveKit infrastructure. Do not copy experimental credentials into the skill.

## Open the host reader

1. Check the selected data directory's live service before launching. In source installations, `presenter.cli.running_server()` validates it; inspect only its scheme and meeting public origin, never print its token. A compatible HTTPS meeting service can be reused. Starting another server on the same port is not a recovery strategy.
2. If none is running, start `lalo.py --home HOME serve --meeting` as a managed background process and save its output locally. On Windows use a hidden process window. Wait for the health check; if it fails, report the concrete startup error. **Start this listener before `open`**: `open` alone can auto-start an ordinary local-only service. Replacing a running service ends in-memory meetings; establish that this is within the user's request before stopping it.
3. Preserve the user's model mode. For conversation inheritance, pass the trusted current Codex thread ID or Claude session ID explicitly to `--session`; never infer it from recent history. If no trusted ID is available, explain the missing context binding and ask whether document-only cloud mode is acceptable. Cloud mode needs no source session.

Example command shape (substitute actual paths and session, pass each argument separately):

```powershell
& $python $lalo --home $dataHome open --document $document --project $project --mode codex --session $sessionId --no-open
```

4. Check the returned source matches the selected session, then open the complete returned URL locally. The host launch URL is private and must not be used as the attendee invitation.
5. For a request to start a meeting, use the formal page's start-meeting control and wait for Room connection. For a request only to prepare a page, leave that action to the user. Opening/starting a meeting can request the host microphone; describe its continuous-listening behavior. Obtain the attendee link from the meeting invitation control; never construct one from a host token.

## Participants and completion

- Host: continuous microphone input after permission; the microphone button mutes/unmutes. Speech is submitted after a pause. Tentative speech briefly lowers playback volume; confirmed questions interrupt, while common fillers usually do not. Explicit stop commands pause explanation.
- Attendee: open the invitation, enable audio if requested, request a turn, then hold to speak and release to submit. A device without a microphone can still read, listen and type. A missing microphone is not evidence that Room joining failed.
- The host controls shared position, continuation and meeting end. Independent attendee browsing is allowed. Document synchronization is not desktop screen sharing. Returning from a disconnection can require manually resuming or reopening the microphone.
- If the user asks to open a known remote test device, use its already configured access and a fresh attendee invitation. Do not treat one user's historical IP, account or SSH key as a portable default. Sending invitations to other people requires an explicit sending request.
- Verify host Room connection and visible controls. If a remote participant is in scope, verify that device joined the same meeting and can see the document. Participant count alone proves neither audio playback nor microphone capture. Report separately what is ready and what was actually tested.
- For ending a meeting, prefer the host's end-meeting control. Stopping the whole service ends every in-memory meeting and invalidates invitations. Keep launch URLs, credentials, recordings and raw diagnostics in local ignored storage.

When done, state which host page is open, how attendees join, and any concrete blocker. Do not claim coworker-speech filtering, physical echo cancellation, public-network reachability or real desktop sharing without corresponding implementation and tests.
