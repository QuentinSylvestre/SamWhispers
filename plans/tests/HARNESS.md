# Test Harness
last_run: 2026-07-06T10:05:00Z

## Resources
| Name | Type | Availability | Constraints | last_verified |
|---|---|---|---|---|
| web-ui | environment | always (localhost:7891) | single browser session; loopback only | 2026-07-06 |
| config.toml | file | always | shared with running instance; save triggers restart | 2026-07-06 |
| whisper-models | files | always | base.en (active), base, medium, large-v3-turbo on disk | 2026-07-06 |
| vad-model | file | always | silero-v6.2.0 on disk | 2026-07-06 |
| faster-whisper | package | always | installed in venv | 2026-07-06 |
| api-keys | credential | none configured | cleanup/translation features limited without keys | 2026-07-06 |
| internet | network | assumed available | needed only for model discovery/download tests | 2026-07-06 |
