# Mobile profile photos and message retries

CRM owns the photo records, private storage and message receipts. AgentFlow and ClientFlow consume these additive fields. Deploy CRM after applying `add_profile_photos`, then distribute the apps. Older app versions can keep using every existing endpoint without the new fields.

## Photos

Authenticated `POST` and `DELETE` routes return `{"avatar_url": "signed URL or null"}`:

| Route | Identity |
| --- | --- |
| `/api/agent/v1/me/photo` | Agent bearer user |
| `/api/client/v1/discovery/account/photo` | Browsing account bearer |
| `/api/client/v1/profile/photo` | Deal bearer participant |

Uploads use multipart field `file`. JPEG, PNG and WebP files may be at most 5 MiB and 25 megapixels. CRM checks the decoded image, applies its orientation, strips metadata and stores a centered 512-by-512 JPEG in the private `contact-files` bucket. Signed URLs expire after one hour; refresh the owning API response to get another. Failed URL signing returns a null photo so the app can use initials.

Agent user, browsing account and agent profile payloads add `avatar_url`. The deal response adds the participant's `avatar_url` and `agent.avatar_url`. Agent deal conversation rows add the participant's `avatar_url`. Inquiry rows add `client_avatar_url` and `agent_avatar_url`. Message rows add `sender_avatar_url`.

Browsing accounts and deal participants keep separate photos. Linking a deal grant does not prove that the browsing account is the named participant. The server does not copy photos between those identities or infer ownership from an email address. Uploads mutate only the authenticated identity. See the [deletion runbook](privacy/account-deletion.md) for storage cleanup.

## Message retries

Deal message POST bodies may include a UUID `request_id`. Existing callers can omit it. The server scopes the receipt to the participant and sender, and checks the author for agent replies. A matching retry returns the original message with HTTP 200. A new message returns HTTP 201. Reusing an ID with a different body or agent returns HTTP 409. Push and email notifications run only for new messages.

Inquiry messages already require a UUID `request_id`. Both inquiry and deal message responses now echo that ID so an app can reconcile an optimistic row with a refresh. Keep the same ID when retrying a send whose outcome is unknown.

## BOB titles

`POST /api/agent/v1/bob/conversations/{id}/title` accepts `{"message": "first spoken utterance"}` and returns the existing conversation shape. It uses the agent bearer session and BOB feature access checks. It names only untitled conversations or known generic voice placeholders, including the old voice-context prefix. Meaningful existing titles remain unchanged. A supplied utterance updates `updated_at`; this endpoint does not save transcript messages.

The first ordinary native message uses the same short-title generator as CRM web chat. Conversation responses already include UTC `created_at` and `updated_at` strings without a timezone suffix.
