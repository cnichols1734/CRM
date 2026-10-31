# Account deletion operations

ASR-001, October 31, 2026. Christopher Nichols agreed to handle deletion requests. This branch adds initiation and a durable queue. It does not automatically erase an agent's shared CRM workspace. Do not mark ASR-001 resolved until a synthetic request has been fulfilled through the deployed service and signed app.

## Daily queue review

Requests send a notice to chrisnichols17@gmail.com through the existing email service. The database queue is authoritative even if email fails. Review every organization's queue daily, including entries with `notification_sent: false`. No scheduler is installed by this change.

Run with the intended backend environment and organization ID:

```
python scripts/account_deletion_requests.py --organization ORGANIZATION_ID
```

Requests have a fixed 30-day deadline. Repeated submissions return the original receipt. The app remains usable during processing. Never treat sign-out, disabled access or a received request as completed deletion.

## Fulfillment

1. Locate the user and organization from the queue. Verify the request identity and arrange any required workspace ownership transfer. Do not delete an organization, another user's contacts, or another participant's deal records as a shortcut.
2. Inventory personal data in the user profile, private contacts/tasks, BOB chats and attachments, messages, notification tokens, email/calendar integrations, OAuth grants, inbox routing, documents, storage providers, logs and backups. Review the actual foreign keys and object ownership before choosing deletions or reassignment. Shared records require a documented retention decision; sharing alone is not a legal basis to retain all personal data.
3. Remove the account and personal data that need not be retained. Revoke sessions, reset tokens, device registrations and provider credentials. Remove external objects as well as database references. Confirm that web, agent and any linked credentials cannot regain the deleted identity. This is operator work; the queue command does not perform it.
4. Record each retained category, legal basis, responsible owner and expiry date. Arrange deletion at expiry, including backup restoration handling. If nothing is retained, record that explicitly. Do not invent a universal real-estate retention period.
5. Send completion confirmation to the requester with the retained categories and periods. Keep the minimum audit evidence, not copies of deleted documents or credentials.
6. Save a JSON evidence file with nonempty string fields `operator`, `deleted_data`, `external_services`, `retained_records`, `retention_basis_and_expiry`, and `confirmation_sent_at`. Record completion only after the preceding work:

```
python scripts/account_deletion_requests.py --organization ORGANIZATION_ID --complete REQUEST_ID --evidence /secure/path/evidence.json
```

The command refuses completion while the user row still exists. Evidence records require operator review; the command cannot prove external storage erasure or the validity of a legal retention basis. The audit queue deliberately has no cascading user or organization foreign key, so deletion does not erase the request history.

## ClientFlow

The existing DELETE `/api/client/v1/discovery/account` confirms the password, removes the browsing account, saved homes, inquiries and their messages. Account-linked deal JWTs become invalid because authentication checks that account still exists. Shared CRM contacts, participants, transactions and documents remain. A separate invitation grant is not the same identity and must not be revoked indiscriminately. Review personal data retained in shared records using the same retention process above.

## Deployment and acceptance

Apply `add_account_deletion_requests` after `add_marketing_monthly_usage`, then deploy CRM before distributing the agent app. Existing apps remain compatible. Test notification failure, repeated submissions, wrong passwords, organization isolation and completion evidence. Rehearse fulfillment on synthetic records, verify actual provider deletions and confirm other participants retain their records. PostgreSQL RLS and production mail delivery require deployment-environment verification. The owner authorized merging and releasing this work on October 31. Do not use real customer records for deletion rehearsals.
