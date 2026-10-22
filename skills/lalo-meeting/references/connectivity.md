# Connection setup and diagnosis

Read the installed checkout's README section for LiveKit Room meetings and `presenter/meeting_launch.py` for current validation. These are the configuration authority; use the existing data directory reported by `doctor` unless the user selected a different one.

The current listener requires:

| Setting | Purpose |
| --- | --- |
| LALO_MEETING_PUBLIC_URL | HTTPS origin reachable from attendee devices |
| LALO_MEETING_TLS_CERT / LALO_MEETING_TLS_KEY | Matching server certificate and private key |
| LALO_MEETING_TLS_CA | Optional private CA for local CLI certificate verification |
| LIVEKIT_URL | Backend WebSocket signaling URL |
| LIVEKIT_PUBLIC_URL | Browser WSS signaling URL for the same LiveKit service |
| LIVEKIT_API_KEY / LIVEKIT_API_SECRET | Server-only LiveKit credentials |

`serve --meeting` starts Lalo's HTTPS listener, not LiveKit. Verify the existing LiveKit process or service separately. An HTTPS browser needs secure WSS signaling, and the selected LiveKit deployment's media ports must also be reachable. Do not invent a fixed UDP port range independent of its configuration.

For LAN use, the Lalo certificate must cover its advertised hostname/address and the loopback address used by the local CLI. Trust is separate on each device. An established private CA normally needs installation once, not before every meeting. Keep certificate verification enabled; use existing trusted certificates instead of globally disabling browser security.

Diagnose in order of observed failure:

- Page unreachable: verify advertised address, Lalo listener and relevant firewall rule.
- Certificate error: check names, validity and device trust; setting a CA path for the CLI does not install browser trust.
- Page loads but Room fails: inspect sanitized signaling errors, WSS reachability and matching service credentials.
- Joined but no sound: check browser audio activation, local mute and media connectivity; distinguish AI playback from peer microphone tracks.
- Microphone unavailable: inspect permission and actual input devices. Do not replace missing hardware with a claim of successful real-microphone validation.

Use narrowly scoped configuration changes. Preserve a working service while diagnosing; ask for the specific missing infrastructure information when it cannot be discovered locally. If blocked, report the failing stage and required action rather than silently falling back to a local-only single-user page.
