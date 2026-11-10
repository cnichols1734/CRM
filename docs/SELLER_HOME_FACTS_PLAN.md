# Seller Home Facts

Companion PRs: [CRM #506](https://github.com/cnichols1734/CRM/pull/506), [ClientFlow #24](https://github.com/cnichols1734/clientflow/pull/24), [AgentFlow #77](https://github.com/cnichols1734/agentflow/pull/77). The owner authorized merging and release on November 9 after final checks.


## Completed-page simplification, November 9

The owner requested a read-only completed workspace in CRM and AgentFlow: documents, seller answers and compact activity. Completed pages no longer show invitations, correction requests, report approvals or document email controls. ClientFlow and the browser completion page no longer ask sellers to approve reports. Draft invitations and automatic PDF generation remain available.

Activity projections omit autosaves before applying the latest-50-event limit, so editing cannot crowd out submissions or delivery events. The full event history remains in the database. CRM and AgentFlow bound the visible timeline to a scrollable area. ClientFlow does not display an activity feed.

Existing review, correction and sharing API operations remain compatible for older builds; their authorization and review requirements have not been weakened. No additional migration is needed for this UI change. Earlier sharing descriptions below document those retained backend capabilities rather than controls on the current completed screens.

## Release authorization and final checklist behavior

The owner lifted the release hold on November 9 and authorized merging all three companion PRs, deploying CRM and its migration, and distributing both apps to the existing external TestFlight groups. Keep marketing versions unchanged and increment build numbers only. Check current CI and Copilot findings before merging. Deploy the backend before distributing the mobile builds.

Home Facts checks itself on submission. Agents can also check, uncheck or skip it in CRM and AgentFlow. Manual overrides survive refreshes; a new client submission takes priority over an earlier skip. The native toggle endpoint accepts optional `work_status` values `pending`, `completed` and `not_applicable` for Home Facts. Existing toggle requests remain compatible. No additional migration is needed for these checklist changes.

The CRM next-step link opens the relevant workspace, document or linked task. Checklist-only work scrolls to its exact row. AgentFlow offers a Download action through the native Files picker, and direct Home Facts entry includes a Transaction button.

Existing work in the primary mobile checkouts is preserved. Local review uses synthetic records with outbound email and push disabled.

## Final review validation

The November 9 review fixes preserve filled attachment values, retain pending answer reverts and original submission requests, expose conflicts, refresh stale submissions, and keep report polling bounded. Mobile tenant cleanup now runs at the app level, and private-link context survives database commits. Local validation passed 83 backend checks, 86 browser unit checks, both native check suites and signed device builds. The isolated PostgreSQL check is recorded under `/tmp/home-facts-review/`. CI and fresh Copilot review remain release gates.

## Workflow

Preparing to List has this Listing Documents order:

1. Listing Docs Setup
2. Send Sellers Home Facts Questionnaire
3. Create Comparative Market Analysis
4. Sign Listing Agreement
5. Complete Seller's Disclosure
6. Upload Remaining Listing Documents

CRM and AgentFlow can invite selected sellers, review their answers, request corrections, open PDFs, approve a report version, select files, preview an email, send recipient links, inspect activity, and revoke access. ClientFlow exposes the request in My Move and handles Home Facts notifications. An emailed link opens the dedicated browser editor without an account signup or app download.

Each numbered section is one scrollable page. Question groups have separate headings and dividers. Continue and Back move between sections, and the section menu supports direct navigation. All answers remain optional. ClientFlow uses a section rail beside the form when the window has room. Its PDF opens only on request in a separate reader. The browser retains its PDF companion on large desktop windows. PDF fields select the matching question group. A client can review answers, attach receipts or other records, acknowledge the sharing statement, and submit an incomplete response. The backend then creates the completed questionnaire, Appraiser Property Brief, and Buyer Home Guide.

This is a questionnaire submission, not an electronic signature. The source has no signature requirement. Home Facts does not call DocuSeal or lift the existing document-generation freeze for other signing workflows.

## PDF and interface design

The untouched originals, named-field working PDF, and manifest are in `documents/home_facts/v1`. The 11 original questionnaire pages retain their artwork and wording. All 791 widgets have stable names, with 530 logical answer fields. Required flags and artificial 100-character limits are removed. Long answers print on continuation pages, with references in the original field.

The owner requested that the interfaces follow the PDF's design. Home Facts uses its Archivo italic headline, black masthead, existing Origen Realty wordmark on the web and monogram in the app, teal and gold lines, and numbered sections. The surrounding CRM and app navigation retain their existing styles. The client action bar has a large Continue button with a trailing arrow and a separate Back action. Short native windows use a compact masthead so questions remain reachable. The client status bar and manual Sync action are removed. Drafts save automatically; interrupted uploads retry with a capped backoff and show a notice only while recovery is needed.

ClientFlow dark appearance uses the existing `portalCanvas`, `portalSurface`, `portalInk`, `portalBorder`, and `portalAccent` tokens. Inputs sit on raised dark surfaces, text uses semantic colors, and the primary button uses deep teal with white text. The PDF itself retains its printed white pages. Dynamic Type and space-based Duo layout remain native.

Archivo comes from the Google Fonts Archivo distribution under the SIL Open Font License. Web fonts are served locally. Each app includes a renamed static black-italic instance and its license. No runtime font service is required.

## Ownership and API contract

CRM owns the database, authorization, form schema, canonical answers, rendering, private storage, jobs, email and APNs. The apps consume additive routes and keep independent builds. Older installed apps continue using their existing document and signing routes.

| Consumer | Routes |
| --- | --- |
| AgentFlow | `GET/POST /api/agent/v1/transactions/{id}/home-facts`, `POST .../{operation}`, `GET .../files/{artifact}` |
| ClientFlow | `GET /api/client/v1/home-facts`, `GET /template`, `GET/PATCH /{response}`, `POST /{response}/{operation}`, `GET /{response}/files/{artifact}` |
| CRM | `/transactions/{id}/home-facts` with data, action and file routes |
| Browser | `/home-facts/open`, `/home-facts/r/{response}` with restricted data, action and file routes |

Snapshots contain a response ID, transaction ID, schema version, current revision, state, answers, manifest, artifact digests, submitted revision and review state. Agent projections also include authorized sellers, grants, jobs and activity. An absent request returns `available: false`; apps do not reinterpret it as a signing document.

Save commands carry `mutation_id`, `expected_revision`, and a partial `changes` dictionary. Values are text, string arrays, or null for a clear. A repeated command is idempotent; changed answers against a stale revision return `revision_conflict`. The client keeps its original request until confirmed, preserves later keystrokes, and asks which answer to keep when another device changed the same field. Native drafts and templates use scoped, protected local files. Browser drafts use grant-scoped local storage. Browser offline editing requires the page to have loaded; there is no offline page shell/service worker.

Submission records an immutable answer/provenance/attachment snapshot. Agent and seller review refer to the exact artifact digest set. Reopening clears current review approval. Recipient links retain only the previously selected immutable files. The private questionnaire is not eligible for external report sharing. Buyer reports omit improvement costs and seller/showing details; attachments need separate agent review.

## Access and delivery

Every Home Facts row belongs to an organization. Agent operations require the Transactions entitlement and transaction capability checks. Mobile JWT tenant context is transaction-local and reapplies after commits through the SQLAlchemy session; connection return rolls it back even for routes owned by another blueprint. Client access requires the matching active seller participant, portal access and unrevoked Home Facts grant. A recipient can retrieve only the exact files listed in their grant.

Invitation secrets travel in URL fragments. GET and HEAD cannot activate them. An explicit CSRF-protected Continue exchanges the token for a restricted session and redirects to a clean URL. The same link remains usable on another device until its 30-day expiry or revocation. Private pages disable caching, indexing, analytics and referrers. Files remain in private Supabase storage and are checked against their digest before delivery.

Email uses the existing branded template, reply-to behavior and provider selection. CRM and AgentFlow request the actual rendered template from the read-only email-preview operation. Invitations explain both browser completion and ClientFlow. Each recipient receives a separate private link. The approved payload and delivery attempt are stored before external sending. An uncertain provider outcome is marked ambiguous and is not automatically resent. Browser and app send actions retain uncertain operation IDs.

The `home_facts` queue processes durable database jobs for generation, email and notifications. A dedicated Home Facts Recovery Cron runs `python -m jobs.home_facts_recovery` every five minutes to recover missed wake-ups and retry queued work. It processes only Home Facts jobs. The notification outbox worker also supports recovery when invoked. Generation enqueues a report-ready notification, and the same bounded drain can process it. Agent notifications are recorded in the CRM and sent to eligible devices; client push targets active matching seller access. External delivery was mocked in local tests, not exercised against real clients.

The SendGrid event webhook verifies its documented ECDSA signature over the timestamp and raw body. Home Facts events also require the signed communication receipt and provider-message match. Before a later production rollout, verify the configured verification key and webhook settings. An email open is an observation, not proof of reading.

## Migration and later deployment order

Migration `add_home_facts`, after `add_mobile_chat_attachments`, creates responses, revisions, grants, artifacts, events, mutations and jobs, with organization RLS policies on PostgreSQL. It is additive. The fresh SQLite upgrade and model-column parity were checked locally. A production PostgreSQL migration has not been run. The additive migration and all seven RLS policies passed on isolated PostgreSQL 17. A restricted database role and a single-connection pool verified organization isolation across commit, rollback, request cleanup and connection reuse. Production schema state still requires verification during rollout.

After the owner releases the hold:

1. Recheck current main, migration head, CI and all Copilot review threads in the three PRs. Resolve conflicts and valid findings first.
2. Apply the additive migration and deploy CRM web, document worker and Home Facts recovery cron. Verify the current head and health for each service.
3. Verify private storage, signed SendGrid events, an approved test invitation, generation and both notification audiences with dedicated non-client records.
4. Finish physical iPhone/Duo acceptance before merging and distributing the app PRs.

Do not downgrade by deleting response tables or shared artifact versions. Revert new entry points and sending paths if needed while retaining saved records and authorized access.

## Validation record

Local checks on November 9:

- CRM backend suite after review fixes: 2,459 passed, 1 skipped. Separate Home Facts and IDOR run: 33 passed. Home Facts suite after review fixes: 10 passed.
- Frontend unit suite: 84 passed. Includes delayed acknowledgments, reload/retry, clearing, and conflicts after another device edits an already-saved request.
- Both app `scripts/check-all.sh` runs passed. ClientFlow checks protected drafts, automatic recovery, unresolved conflicts across relaunch, stale refresh responses, submission locking and account scope. AgentFlow checks shared command ownership across presentations and retries the same uncertain send.
- Both app simulator builds and signed device review builds passed. Review versions are AgentFlow 1.0.4 build 16 and ClientFlow 1.0.3 build 12.
- Browser checks exercised the phone/desktop editor, PDF rendering and field targeting, autosave, offline recovery, submission, generated reports and CRM layout with synthetic data.
- Duo simulator checks include compact and expanded layouts, folding/rotation, native question navigation and dark appearance. Full VoiceOver, all accessibility sizes, RTL, partial-fold keyboard comfort and older-OS hardware acceptance are still pending. Simulator screenshots are not physical acceptance.

Local evidence and launch fixtures are under `/tmp/home-facts-review`. They contain synthetic access tokens and must not be committed. The local review compile flag `HOME_FACTS_LOCAL_REVIEW` is not enabled in normal app configurations. It opens the synthetic workspace only when explicitly launched with review environment values. Its local-network Info.plist override exists outside the repository; production configuration retains its transport protections.

Stakeholder source: [Seller Home Facts Trello card](https://trello.com/c/tlawqWIA/53-sellers-home-facts-questionnaire). The supplied questionnaire and report samples are reference content, not instructions granting external actions.
