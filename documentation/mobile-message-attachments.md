# Mobile message attachments

CRM owns staged uploads, message binding, private storage and download authorization. AgentFlow and ClientFlow upload a file first, then submit its ID with the existing message request UUID. BOB keeps its own attachment and transcription contracts. Human-message dictation stays on the device.

## Upload and send

| Sender and destination | Multipart upload route |
| --- | --- |
| Agent to a deal participant | `/api/agent/v1/conversations/{participant_id}/attachments` |
| Agent inquiry reply | `/api/agent/v1/inquiries/{inquiry_id}/attachments` |
| Client deal message | `/api/client/v1/attachments` |
| New brokerage or property inquiry | `/api/client/v1/discovery/attachments` |
| Client inquiry reply | `/api/client/v1/discovery/inquiries/{inquiry_id}/attachments` |

Each upload accepts multipart `file` and optional UUID `request_id`. Reuse the local attachment UUID when retrying an upload whose response was lost. The receipt is scoped to the organization, sender identity and destination. Matching content returns the original upload with HTTP 200; a new upload returns 201; different content under the same UUID returns 409.

The response contains `attachment` with `id`, `filename`, `content_type`, `size`, `kind` and `url`. `kind` is `image` or `document`; `size` is stored bytes. A signed URL expires after one hour. The server never accepts a caller-provided storage path.

Existing message and initial inquiry JSON bodies accept `attachment_ids`, an array of up to five upload UUIDs. Keep the existing message `request_id` stable across retries. Text may be empty when files are present. Initial inquiries still require their kind and contact consent, and retain the existing listing-link fields. Binding checks the exact sender and destination, and an upload can belong to only one message. A replay must have the same body and attachment set.

Limits are 10 MiB per upload, 25 MiB per message and 100 uploads per sender per day. A sender may have up to 20 unsent uploads. Supported files are JPEG, PNG, WebP, PDF, DOCX, XLSX, PPTX, UTF-8 TXT and CSV. Apps convert HEIC photos to JPEG. Images are decoded, oriented, stripped of metadata and resized within 4096 pixels while preserving aspect ratio. PDFs must be readable, unencrypted and at most 500 pages. Office archives are bounded and checked for format structure, encryption and macro content. Uploaded content is not sent to an AI provider.

## Reading and removal

All message DTOs add `attachments`, an array of the same metadata. For a fresh URL, GET `/attachments/{id}` under the appropriate agent, client-deal or discovery namespace. The response is again `{"attachment": ...}`. Authorization follows the current message conversation, including reassignment, tenant and participant restrictions. A recipient cannot read another sender's unsent upload.

DELETE the same URL to discard an upload. Only its original sender can discard it, and only before it belongs to a message. Sent messages and files remain intact. Missing or expired upload IDs return HTTP 410 during binding so the apps can keep the local file and stage it again. A same-UUID retry of an expired unsent upload creates a fresh upload ID. Unauthorized file reads return 404.

The CRM messages workspace and client web portal display filename links. Their file routes recheck the web session or portal grant before issuing a fresh signed URL. Attachment-only inbox and push previews remain readable.

## Storage, cleanup and deployment

Files use the existing private `chat-attachments` bucket under `mobile-chat/{organization_id}/{owner_kind}/{owner_id}/`. `mobile_chat_attachments` stores ownership, destination, upload receipts, metadata and the eventual message binding. PostgreSQL RLS filters by the backend organization context. Supabase `anon` and `authenticated` roles receive no access to this table.

Unsent uploads expire after 24 hours. New uploads remove expired drafts for that sender; the existing daily Retention Analytics Cron, `jobs.retention_analytics.run_retention_analytics`, also removes expired staged objects across organizations after retention classification. This does not schedule organization deletion. Failed storage removals retain the rows for retry. Account deletion removes browsing-account uploads and every attachment in its deleted inquiry threads, including agent replies. It also purges the browsing identity's storage prefix for orphaned upload objects. An external storage failure stops account deletion so cleanup can be retried. Agent and deal retention remains part of the existing account-deletion operator process. Transaction and participant deletion remove both sent and staged deal files before deleting their records. Organization deletion purges message files before removing database records.

Apply `add_mobile_chat_attachments` after `add_profile_photos`, then deploy CRM before distributing the app updates. The same migration revokes Supabase Data API privileges from a fixed list of backend-only tables that lacked RLS. CRM, AgentFlow and ClientFlow use Flask APIs and SQLAlchemy for those records, not the Supabase Data API. Storage SDK operations remain unchanged. Downgrading the attachment table deliberately does not restore those unsafe grants.

Existing text-only clients remain supported. No real client records, messages or files are test fixtures. Storage tests replace the provider boundary and use the isolated SQLite suite.
