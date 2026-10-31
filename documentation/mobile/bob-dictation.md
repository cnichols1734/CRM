# Native BOB dictation

October 31, 2026. Companion app work is in [AgentFlow PR #51](https://github.com/cnichols1734/agentflow/pull/51).

`POST /api/agent/v1/bob/transcriptions` accepts multipart `file` containing an M4A recording, up to 8 MiB. It returns `{"text": "..."}`. Agent bearer authentication and the BOB plan gate apply. Existing daily BOB checks and a three-second per-user Redis cooldown protect provider calls. Invalid audio, provider failures and unavailable rate-limit storage fail without creating a conversation or executing actions.

The route uses `gpt-transcribe` with the existing server-side `OPENAI_API_KEY`, a 60-second provider timeout and no automatic retries. Audio is passed directly to OpenAI without storage in CRM. The endpoint does not accept a model or provider key from the app. Contact memo transcription and full voice chat are unchanged. See [OpenAI file transcription](https://developers.openai.com/api/docs/guides/speech-to-text) for the current recommended model.

Deploy this additive CRM endpoint before installing the app update. Existing apps and ClientFlow remain compatible. There are no migrations. The native app records bounded audio, displays a waveform, and offers Cancel, Stop to review, or Send to transcribe and submit. Per-user OpenAI consent is checked before upload and after the response. The phone never receives the provider key.

Validation: 19 tests in `tests/test_agent_bob.py` passed, including authenticated multipart transcription, model selection, no conversation/message/action writes, plan denial, missing bearer, invalid and oversized uploads, cooldown and redacted provider errors. Provider calls are mocked at the OpenAI boundary. Live model access, deployment and phone audio acceptance are separate checks.
