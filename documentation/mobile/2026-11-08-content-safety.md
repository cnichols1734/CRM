# Native AgentFlow content safety

The iOS readiness release adds authenticated report and block endpoints. Deploy CRM before distributing the native build that uses them. ClientFlow and web CRM clients remain API compatible; this release adds AgentFlow controls, not ClientFlow safety UI.

- `PUT /api/agent/v1/conversations/{participant}/block` and `/inquiries/{inquiry}/block` accept a Boolean `blocked`. Authorization matches the native conversation. Blocks are per agent, stored in `User.notification_prefs.blocked_clients`, and preserve existing preferences. Linked contact, participant and browsing identities are blocked together, including active invitation-linked grants and eligible verified-email deal matches without changing contact ownership. Identity lookup uses three bulk queries per request or push job. Native inbox detail/preview and APNs delivery respect the block. Original CRM records remain available for business retention and investigation; blocking does not revoke another agent's access.
- `POST /api/agent/v1/content-reports` accepts `kind` (`deal`, `inquiry`, `bob`), integer `subject_id`, and `reason` (1–2,000 characters). It validates account/workspace access, limits reports to 20 per day and creates an `AuditEvent` with an AF receipt. Email to the existing support mailbox includes only the receipt, not the reported content. Email failure does not discard the report.
- Native text delivery and message notifications mask a small set of explicit threats and abusive terms. This deterministic filter does not send human conversations to an AI provider. It is an initial safeguard: it does not reliably classify all languages, evasion, context or image content. Incoming client images require a tap in the native app. Human investigation and block controls remain necessary.

## Operator procedure

The owner confirmed `chrisnichols17@gmail.com` as the report and account-deletion contact on November 8. Review incoming notices and the open queue daily; urgent threats need prompt action. A response-time commitment was not specified. Confirm that cadence and coverage before App Review.

Use an authorized backend shell with the existing database configuration. Do not paste conversation content or credentials into logs, issues or PRs.

```
python scripts/content_reports.py --organization ORG_ID
python scripts/content_reports.py --organization ORG_ID --resolve REPORT_ID --operator OPERATOR --outcome 'Investigation, action and user response recorded here'
```

The command only lists reports or records an outcome. It does not disable users or delete messages. Investigate the scoped conversation, preserve necessary evidence, remove access/content through the existing administrative processes when warranted, and respond to the reporter. Record the actual outcome, including any reason no action was taken. Do not resolve merely because email was delivered. Check reports with `notification_sent: false` directly in the queue.

Account-deletion requests continue through `scripts/account_deletion_requests.py` and the existing deletion runbook. An acknowledgement or profile deletion is not proof that all processor-held data has been purged.

## Validation

Local isolated SQLite tests cover private persistent blocks, linked threads, unblocking across thread types, forbidden cross-organization access, blanked message history/previews, report ownership/receipts/notification minimization, basic filtering and APNs suppression. The review regression suite passed 21 checks, including invitation links, constant-query identity resolution filtering before preview truncation, verified-email eligibility and revocation, sent/replayed response filtering, concurrent report resolution, and unblocking after an invitation is revoked. Block responses include all affected conversation IDs visible to the agent so native rows update even when the next inbox refresh fails. Block groups retain the keys added by an action so unlinking identities cannot strand a block. Patched Authlib 1.9.1 and Pillow 13.0.0 passed 113 API, attachment, photo, OAuth and account checks. DOMPurify 3.4.17 and postcss-selector-parser 6.1.5 passed the frontend build and npm audit reported zero vulnerabilities. The served DOMPurify CDN script is pinned to the patched version too. No production messages or reports were sent during tests. A real device rehearsal with synthetic accounts remains a release check.
