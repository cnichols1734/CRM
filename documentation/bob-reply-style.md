# BOB replies without sign-offs

October 31, 2026. The owner requested ordinary chat replies without an automatic `--BOB` footer across BOB surfaces.

The shared web and native chat prompt now tells BOB not to add a name or sign-off. The Daily Briefing follow-up prompt, Telegram prompt and live voice instructions carry the same rule. Canned errors, usage-limit messages, onboarding/help messages, task reminders, daily briefings and document notifications no longer append the footer. Telegram reply cleanup removes a legacy trailing footer before adding record links, while preserving ordinary BOB mentions and agent email signatures.

This changes new responses and notifications. Stored conversation history is not rewritten. ClientFlow has no BOB consumer. API contracts, models, action approval, recipient selection and notification preferences are unchanged. The native waveform and history-swipe changes are separate UI work in [AgentFlow PR #51](https://github.com/cnichols1734/agentflow/pull/51); this backend release needs no app update.

Validation: 227 focused tests passed across native BOB, Telegram, tools, notifications, PDF intake, document review notifications and contract bootstrap acceptance. Regression assertions cover removing generated legacy footers, preserving record links and agent signatures, reminder/briefing text and the tool-loop fallback. No production messages were sent during validation.
