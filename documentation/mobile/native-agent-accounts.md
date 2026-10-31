# Native AgentFlow accounts

The AgentFlow Agent app uses these public JSON endpoints under `/api/agent/v1`. They do not accept a browser session or redirect to an HTML form.

| Endpoint | Request | Response |
| --- | --- | --- |
| `POST /accounts` | String fields `first_name`, `last_name`, `company_name`, `email`, `password`, `confirm_password`, `accepted_terms` equal to `true` | 201 with the existing agent session payload |
| `POST /password-reset` | `email` | An opaque encrypted `challenge` and `expires_in` of 900 seconds |
| `POST /password-reset/complete` | `challenge`, `code`, `password`, `confirm_password` | `ok: true` |

Registration uses the same free organization settings and default groups, task types, transaction types and marketing templates as web signup. All mandatory records commit together. Inbox provisioning and welcome email run after that commit. Email matching is case insensitive. Passwords must have 8 to 128 characters. The server enforces matching confirmation and acceptance of the linked terms/privacy document.

Recovery sends an 8-digit code through the existing email service, using inline SendGrid content with Gmail fallback. No hosted template update is required. Unknown emails and delivery failures return the same response shape and encrypted challenge size. The challenge binds the code to the user, password state and session version. A password change or successful reset invalidates prior challenges. An atomic password comparison prevents concurrent reuse. Recovery also bumps the agent session version and revokes MCP grants.

Request and verification limits use the existing database-backed `ClientBrowseRateLimit` counters through `client_discovery.limited`. No migration is needed. IP and email identities are keyed hashes. Registration allows five requests per hour per IP. Reset requests allow ten per hour per IP and three per fifteen minutes per email. Verification allows thirty per fifteen minutes per IP and five per challenge per thirty-minute window. The challenge expires after fifteen minutes. The existing Railway edge IP handling applies.

The client must keep the challenge in memory, accept the emailed code, and return the user to sign-in after success. It must not log the challenge, code or password. Validation, duplicate-email and rate-limit failures return JSON `error` strings with 400, 409 or 429 status codes.

## Validation and release

Local SQLite tests passed for workspace creation, session login, duplicate emails, mandatory seed rollback, recovery, expiration, tampering, replay rejection, session revocation, unknown emails, email delivery failure, malformed bodies and rate limits. The existing agent API and web authentication suites also passed. Real delivery and production PostgreSQL behavior remain unverified. Deploy this CRM change before releasing the companion native app branch `codex/agent-auth-polish`.
