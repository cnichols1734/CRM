# Testing standards

Read this alongside the root [AGENTS.md](../AGENTS.md) before creating or substantially modifying tests. Choose coverage by the regression it detects, not the number of tests it adds. These notes describe inspected code and lessons from the contact, Magic Inbox, import, and task changes. They are not a claim that the whole suite has been audited or repaired.

## Findings and stronger checks

- In `tests/test_tasks.py`, `test_tasks_filter_status`, `test_tasks_filter_completed`, and `test_tasks_filter_priority` assert only HTTP 200. Those checks cannot detect ignored filters. When changing filtering, improve these tests with matching and excluded tasks and assert the returned results. Do not add another page-load test for each filter.
- In the same file, `test_create_task_success` and `test_create_task_missing_fields` both follow redirects and assert HTTP 200. An error redirect can satisfy the success test. Check the saved task and contact association for success; check that invalid input creates no task. These remain improvement opportunities, not completed replacements.
- `test_tasks_page_loads` accepts either the task subject or the generic word `Task`. The generic alternative weakens the task-specific assertion. A smoke check can establish that a page renders, but it must not be described as proof of filtering, persistence, or authorization.
- The contact-search change replaced conditional dropdown selection in `tests/run_tests.py`, `tests/run_scheduled_tests.py`, and `tests/run_onboarding_e2e.py`. Previously, the contact step could be skipped when the selector disappeared. These journeys now require a search result to be selected and subsequently check that the task appears. Keep this protection in the existing journeys instead of copying the full journey into another suite.
- The browser task journey edits the contact before creating a task. Its search must use the updated email. In the Python suite, another test changed the session seed contact's phone; the new phone-search test passed alone and failed in the full suite. The `search_contacts` fixture in `tests/test_task_contact_search.py` now creates and cleans up records for its matching tests. Do not depend on mutable seed values remaining unchanged across tests.

## Workflows and boundaries worth protecting

| Risk | Existing coverage to inspect and extend | Why this boundary matters |
|---|---|---|
| A task is attached to the wrong contact, or search exposes another tenant's contacts | `tests/frontend/contact_picker.test.mjs`, `tests/test_task_contact_search.py`, `tests/test_tasks.py` | Controller tests cover duplicate names, invalidated selections, and late responses. Flask plus database tests cover actual search scope, permissions, and rejected writes leaving stored data unchanged. |
| A photo import creates the email sender instead of the person in the image | `tests/test_magic_inbox.py`, especially `test_photo_reaches_vision_and_saves_extracted_contact` and the unreadable-photo cases | Multipart and raw MIME inputs must pass through normalization, extraction, and persistence. The tests inspect image delivery and the saved name, email, phone, and tenant, and reject sender fallback. |
| Imported contacts remain invisible or the result report disappears | `tests/frontend/contact_import_refresh.test.mjs` | Exercise the real controller's refresh decision and one-time report recovery. Failed and duplicate-only imports must not trigger a success refresh. |
| Contact edits lose an existing lead source or accept invalid Other text | `tests/test_contact_lead_source.py` | Form/JSON input, validation, database persistence, and tenant permissions interact. Preserve coverage for omitted fields and legacy null values. |
| Async messaging overwrites drafts or shows a response from a previous conversation | `tests/frontend/client_messages.test.mjs` | Control response ordering with deferred promises and check the resulting draft and selected conversation. A network stub alone proves nothing. |
| A new user cannot create a contact and task, or loses contact and activation follow-up data after signing in again | `tests/run_onboarding_e2e.py` | A small number of browser journeys protects routing, rendered controls, session handling, and persistence together. |

Mock paid or external services at their boundary, not the application operation being tested. The Magic Inbox photo test stubs the OpenAI response while running the webhook, image normalization, extraction handling, and database write. It protects that pipeline; it does not prove live OCR accuracy. Likewise, small DOM doubles in the Node controller tests do not prove Stimulus event wiring or browser accessibility. Use existing browser journeys for those integration risks when relevant and authorized.

## Fixtures and execution

- Search by route, behavior, and model in `tests/` before adding coverage. The CI job named `unit` in `.github/workflows/test.yml` includes Flask/database integration tests; the job label does not make those tests isolated units.
- Reuse `owner_a_client`, `agent_a_client`, and the two-organization seed in `tests/conftest.py` for permission boundaries. Seed only the extra records needed for the scenario. Use function-scoped records or restore changed values so test order cannot determine results.
- The shared Flask fixture uses `/tmp/test_integration.db` and deletes it during setup and teardown. Do not run separate pytest processes against it concurrently. The fixture also overrides `DATABASE_URL` and blanks mail credentials before importing the app; preserve those safeguards.
- For mutations, inspect persisted business fields or an independent read-back, not only a response code. For denied writes, check that the target data remains unchanged. For filters, verify inclusion and exclusion so an empty result cannot pass as successful isolation.
- Use parameterization for inputs with the same business contract, such as name/email/phone searches or multipart/raw MIME delivery. Avoid combinations with no distinct regression risk. Prefer deferred promises and condition-based browser waits over arbitrary delays.
- Run focused checks first. Run the wider suite when shared fixtures, permissions, or integration changes create a reason to do so, and complete required CI checks. Do not add a permanent test just to verify a documentation edit. Follow AGENTS.md's UI-test execution rule.

## Keep the guidance current

When work reveals a recurring anti-pattern, important regression risk, or established fixture convention, update the relevant paragraph here. Keep examples tied to current files and distinguish observed gaps from completed fixes. Change the root policy only when the general rules need to change. Do not create per-test assessment documents, duplicate this policy in other agent files, or use these notes as a reason for unrelated suite cleanup.
