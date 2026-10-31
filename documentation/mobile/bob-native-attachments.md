# Native BOB attachments

POST /api/agent/v1/bob/attachments accepts one multipart `file`, using the same private upload service as web BOB. The caller must have a bearer session and AI_CHAT access. Files are limited to 10 MB. The response includes an account-bound, signed `attachment_ref`.

POST /api/agent/v1/bob/conversations/<id>/messages accepts that reference alongside `message` and `request_id`. It verifies ownership, size and digest, parses the attachment, and includes high-detail images or complete PDFs in the Responses model input. The request receipt includes the attachment reference to reject request-ID reuse with different content. File metadata is persisted with the message for history and later handoffs. No database migration is required.

Later messages and voice handoffs can inspect the latest attachment in the last 20 stored messages. References for that persisted file are reconstructed only from the authenticated user's owned conversation and storage prefix. Internal voice context grants creation intent only from its latest user utterance.

Native attachment-derived create_contact calls route through import_contacts, including one-person photos. That preserves the extracted candidate snapshot for approval. Read-only photo questions cannot create a contact through this path. Existing tool permissions, duplicate checks, approval, undo and account boundaries remain in force.

Validation: 177 tests passed across tests/test_agent_bob.py, tests/test_bob_attachments.py and tests/test_bob_tools.py. The new cases exercise real route, parsing, Responses input conversion, pending action and approval behavior with storage/model boundaries mocked. A separate live gpt-6.1-sol image request read synthetic name, email and phone values exactly; it performed no CRM action. Real camera quality and production mobile-to-storage-to-model acceptance remain to be checked after deployment.

Companion iOS work is in [agentflow PR #43](https://github.com/cnichols1734/agentflow/pull/43). Deploy this CRM change before using attachments on the phone. The Responses image block format is documented at https://developers.openai.com/api/docs/guides/images-vision.
