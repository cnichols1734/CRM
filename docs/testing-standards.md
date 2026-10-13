# Testing standards

Read this alongside the root [AGENTS.md](../AGENTS.md) before creating or substantially modifying tests. Choose coverage by the regression it detects, not the number of tests it adds.

## Findings from the suite cleanup

- Following a redirect and asserting HTTP 200 can hide a failed write. The old contact and transaction creation tests omitted required input, and the transaction request returned the new-transaction form. `tests/test_contacts.py` and `tests/test_transactions.py` now check saved records, ownership, associations, and subsequent edits. Task creation also checks the stored contact and due time.
- The old to-do tests sent `lists`, which `/api/user_todos/save` ignores. `tests/test_user_todo.py` now uses `active` and `completed` and reads back ordering, completion, replacement, clearing, and isolation between users. A successful response alone cannot establish that a payload was accepted.
- Task filters previously checked only HTTP 200. `tests/test_tasks.py` now asserts matching tasks are present and other statuses, assignees, and tenants are absent. The old priority-filter test exercised an unsupported query parameter and was removed. Contact filters use the same inclusion/exclusion pattern.
- Free-tier tests accepted success, redirects, or denial. `tests/test_feature_flags.py` now requires the actual denial response or redirect destination for each route. Do not accept both allowed and denied outcomes in a permission test.
- Repeated page-load checks in `tests/test_navigation.py` duplicated domain tests. Keep route rendering with the domain's content or behavior checks. The shared sidebar needs one representative render, while different route permissions still need their own cases.
- Global sitemap, blocked SEO routes, and `llms.txt` membership belong in `tests/test_free_real_estate_crm.py`. Comparison-page suites retain their own metadata and copy checks. `tests/test_public_footer_comparison_links.py` checks rendered links across public pages; duplicate template-source checks were removed. Keep approved product claims and distinct noindex/canonical-host checks.
- `tests/test_auth_family_ui.py` previously inspected another test file's assertions. Those tests were removed. Rendered auth pages protect the link counts and copy themselves. Existing browser contrast checks cover actual computed styles; source checks cannot prove visual contrast.
- `tests/test_transitions_hooks.py` had an arithmetic example that never ran production code, plus a source-statement ordering check for the same race. The retained JavaScript test closes and reopens the real sheet and verifies it stays visible without running the stale callback.
- Lead-source choices share `ContactLeadSourceForm`. `tests/test_contact_lead_source.py` checks every choice through that validator, and ordinary/Other values through both persistence routes. Null, omitted-field, invalid-input, escaping, tenant, and migration regressions retain integration coverage. Avoid multiplying database setup by a catalog when entries share one behavior.
- `DocumentLoader` already rejects duplicate YAML slugs while loading. Comparing its dictionary keys with a set could never detect duplicates. `tests/test_document_definitions.py` now checks that all shipped definition files load into the catalog.

- The VTC evaluation scaffold had skipped placeholders and claimed prompt/IDOR coverage without executing either. `tests/evals/` now collects only applicable registry and sanitizer cases, and its README states that scope. Do not label deterministic fixture checks as live model evaluations.

## Workflows and boundaries worth protecting

| Risk | Coverage to inspect and extend | Why this boundary matters |
|---|---|---|
| Wrong task contact or leaked contacts | `tests/frontend/contact_picker.test.mjs`, `tests/test_tasks.py` | Controller tests cover duplicate names, invalidated selections, and late responses. Flask/database tests cover search scope, permissions, and persisted assignment. |
| Cross-tenant task or transaction access | `tests/test_idor_protection.py` | This suite owns the overlapping transaction/task route denial cases removed from general CRUD suites. CI runs it separately. Keep mixed parent/document IDs and both own-data access and foreign-data denial. |
| Photo import creates the email sender | `tests/test_magic_inbox.py` | Multipart/raw MIME, image normalization, extraction, and persistence interact. The photo test checks the saved name, email, phone, and tenant; unreadable-photo cases reject sender fallback. |
| Imported contacts remain invisible or the report disappears | `tests/frontend/contact_import_refresh.test.mjs` | Check the controller's refresh decision and one-time report recovery. Failed and duplicate-only imports must not trigger a success refresh. |
| Async messaging overwrites drafts or displays an old conversation | `tests/frontend/client_messages.test.mjs` | Deferred responses expose ordering mistakes; checking a network stub alone does not. |
| Organization changes or shared resources affect the wrong users | `tests/test_organization.py`, `tests/test_admin.py`, `tests/test_company_updates.py` | Check saved settings, roles, invitations, resource readback, reactions, and comments along with role and tenant boundaries. |
| A new user cannot create a contact and task or loses data after signing in again | `tests/run_onboarding_e2e.py` | Browser journeys protect routing, controls, sessions, and persistence together. The contact search step is mandatory, and searches after an edit use the updated email. |

Retain the focused contract, offer, deadline, marketing-consent, webhook, and client-access regression suites. Many cases in those areas represent different business rules. A large file or parameter list alone does not justify deletion.

Mock paid or external services at their boundary, not the operation under test. The Magic Inbox photo test stubs OpenAI while running normalization and persistence; it does not prove live OCR accuracy. Contact-form and invitation tests stub mail delivery while checking the submitted content and application outcome. Small DOM doubles in Node tests do not prove Stimulus wiring or browser accessibility. Use existing browser journeys for those risks when relevant and authorized.

## Fixtures and execution

- Search by route, behavior, and model in `tests/` before adding coverage. The CI job named `unit` also runs Flask/database integration tests; the label does not make them isolated units.
- Reuse `owner_a_client`, `agent_a_client`, and the two-organization seed in `tests/conftest.py` for permission boundaries. Create only the scenario's extra records, then remove them. Restore shared fields directly in `finally` rather than relying on the endpoint under test to undo its own work.
- Shared seed values are mutable. Earlier contact edits changed Jane's phone/name and made later search tests order-dependent. Task search, contact filtering, and transaction address search now own their records. Do not assume a session seed still has its initial field values.
- Clean up dependent rows too. Logging contact activity creates `Interaction` rows; activating a seller transaction can create an automatic check-in `Task` that still references the contact after transaction deletion. Fixture teardown must account for those effects.
- The shared Flask fixture deletes `/tmp/test_integration.db` during setup and teardown. Never run separate pytest processes against it concurrently. Preserve its `DATABASE_URL` override and blank mail credentials before app import.
- Check persisted business fields or independent readback for mutations. For denied writes, verify unchanged data. For filters and isolation, include positive matches so an empty response cannot masquerade as success.
- For tier-gated features, tenant-isolation tests must give the foreign organization feature access. `tests/test_company_updates.py` uses a paid foreign organization so a subscription denial cannot hide a broken tenant filter. Separate free-account checks cover the upgrade page and blocked writes.
- Parameterize inputs with the same contract. Prefer deferred promises and condition-based browser waits over arbitrary delays. Avoid tests that pin helper names, statement order, or equivalent syntax when observable behavior is already protected.
- Run focused checks first, then the required CI suites. Check test-order effects when changing shared fixtures. Record measured before/after runtime with the same command and environment; one run is not a performance guarantee. Follow AGENTS.md's UI-test execution rule.

## Keep the guidance current

Update the relevant paragraph when work reveals a recurring anti-pattern, important regression risk, or established fixture convention. Change the root policy only when the general rules change. Do not create per-test assessment documents, duplicate the policy in other agent files, or use these notes to expand a small feature into unrelated suite cleanup.
