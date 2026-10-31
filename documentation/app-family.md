# AgentFlow product family

AgentFlow, ClientFlow and CRM are one product family with three repositories. Separate repositories let the apps evolve independently. They still use the same CRM business data and backend services. Read this guide before changing an app/backend contract. Check current source and deployment state; these notes are not proof that a PR has merged or that an endpoint is deployed.

## Repository ownership

| Product | Repository | Primary local checkout | Owns |
| --- | --- | --- | --- |
| AgentFlow iOS | [cnichols1734/agentflow](https://github.com/cnichols1734/agentflow) | `~/agentflow` | Agent native UI, CRM account sign-in, contacts, tasks, deals, agent messages and native BOB |
| ClientFlow iOS | [cnichols1734/clientflow](https://github.com/cnichols1734/clientflow), private | `~/clientflow` | Client native UI, discovery, saved homes, brokerage identity, deal participation, messages, documents, showings and calendar handoff |
| AgentFlow CRM | [cnichols1734/CRM](https://github.com/cnichols1734/CRM) | `~/PycharmProjects/CRM` | Web CRM, API contracts, authentication/authorization, persistent business data, migrations, background jobs and external services |

AgentFlow and ClientFlow sit directly under Christopher's home directory. CRM keeps its existing location under `PycharmProjects`. Additional workspace worktrees may exist; verify `git remote -v` in every checkout you use. The agent repository was renamed from `agentflow-client` to `agentflow` on October 31, 2026. Old commits may still contain both apps. Folder placement is not a build dependency.

AgentFlow uses `AgentFlow.xcodeproj`, scheme `AgentFlow Agent`, bundle `com.agentflow.agent`. ClientFlow uses `ClientFlow.xcodeproj`, scheme `ClientFlow`, bundle `com.agentflow.client`. AgentDesk is the agent app's old name. AgentFlow was also the client's old name. Existing `AGENTDESK` conditions, `AgentFlowAPIBaseURL`, storage keys and `agentflow` deep links retain legacy names for compatibility. Do not rename them as cosmetic cleanup.

## Shared backend and data flow

Both apps configure `https://agentflow.origentechnolog.com` as their backend. The CRM repository owns that service. The app split did not provision a second backend, database or deployment. App repositories do not own production migrations or server credentials.

| Contract or service | CRM owner | Native consumer |
| --- | --- | --- |
| `/api/agent/v1` | `routes/agent_api.py` | AgentFlow `AgentFlowAgent/AgentAPI/`; account entry, CRM records, tasks, deals and native BOB |
| `/api/client/v1` | `routes/client_api.py` | ClientFlow `ClientFlow/ClientAPI/` and `ClientFlow/App/SessionController.swift`; authorized deal participation, messages, documents, showings and calendar subscriptions |
| `/api/client/v1/discovery` | `routes/client_discovery.py` | ClientFlow `ClientFlow/Features/Discovery/`; client accounts, brokerage discovery, saved homes, inquiries and connecting eligible deals |
| Business data and tenant isolation | `models.py`, route/service permission checks and migrations | Web CRM and both mobile apps, with different permitted views of the same data |
| Message and date notifications | `services/device_push.py`, `services/apns_client.py` and background jobs | Both apps register devices through their own API namespaces; APNs topics remain `com.agentflow.agent` and `com.agentflow.client` |
| BOB models, tools and provider access | `services/bob_ai.py`, `services/bob_tools/`, `services/bob_attachments.py` and agent API routes | CRM BOB and AgentFlow `AgentFlowAgent/Features/BOB/`; ClientFlow does not contain BOB or WebRTC |
| Client listing provider | `services/repliers_listings.py`, discovery routes | ClientFlow discovery and listing views; provider credentials remain server-side |
| Document storage, signing and email | CRM document services, API routes and existing provider integrations | Web CRM and the apps through authorized server contracts; do not copy provider keys into an app |

Agent authentication uses CRM user/account credentials and agent bearer sessions. Client deal access is scoped to the authorized deal participant. Client discovery also has its own client-account authentication. These are different identities and authorization paths. Do not treat a client as an agent, a generic CRM contact as an authorized deal participant, or one token as interchangeable with another. Preserve organization boundaries, role checks, consent and document permissions when changing shared services.

A change made in the CRM or AgentFlow can appear in ClientFlow after its normal refresh. A client message or showing response can affect agent/web views. UI state, cached data and refresh rules therefore matter on both sides even though code is in different repositories.

## Decide which repositories a change affects

| Proposed change | Required impact review |
| --- | --- |
| Native layout, navigation or local draft behavior | Its owning app. Check the other app only when a copied helper or shared workflow is also affected. |
| API fields, error codes, pagination, dates, authentication or feature flags | CRM producer plus every consuming app. Read the actual Swift models/transport and existing server tests before changing the response. |
| Deal participants, messages, documents, milestones or showing responses | CRM permission/data behavior plus both apps' relevant readers, writers and refresh paths. |
| Push, calendar feeds, URL schemes, bundle IDs or session/storage identifiers | CRM registration/token behavior and the affected app's entitlements, configuration, persistence and routing. These require compatibility planning. |
| BOB tools or model behavior | CRM BOB and AgentFlow native BOB. ClientFlow only if the resulting business-data change affects its authorized client view. |
| Brokerage identity or listing/discovery behavior | ClientFlow and CRM branding/discovery services. Keep agent product branding separate. |
| Database schema, external provider or deployment configuration | CRM owns the change. Inspect affected API payloads and app behavior before deployment. |

Before implementing a change, identify the owning repository and affected consumers. Inspect their current source, branches, open PRs and API tests. Record which repositories need a change and which remain compatible. If a necessary repository or deployment cannot be inspected, state that validation gap rather than assuming compatibility. Work within the user's scope and preserve unrelated branches and local edits.

For a shared contract change, link the companion PRs and name the deployment order in each PR. Prefer additive backend changes that remain compatible with installed app versions. Deploy and verify required backend support before distributing an app that calls it. A successful local app build does not prove a new endpoint exists in production. Removing or changing a server field requires a migration or compatibility plan for older installed apps; coordinated merges alone are not enough.

## Native code ownership

The apps have independent copies of the small UI layer that was shared before the split. AgentFlow owns `AgentFlowAgent/UI/`, its asset catalog and resources. ClientFlow owns `ClientFlow/Theme/`, its stage rail, assets and resources. Helpers with names starting `Client` may still be used inside the agent app. They are local code, not imports from the client repo.

Neither app builds against sibling source directories, a shared local package or a submodule. Do not reintroduce that coupling just to avoid copying a small fix. If a helper bug affects both apps, port the fix deliberately and validate each copy. Keep product-specific behavior in its own repository.

## Validation and handoff

Run each changed app's `bash scripts/check-all.sh` and the relevant Xcode build. Full builds currently require Xcode 27.1 for Duo APIs. Read the local iPhone/Duo guide for UI changes. For backend changes, select existing focused CRM tests that cover the contract, authorization and affected business behavior. Examples include `tests/test_agent_api*.py`, `tests/test_agent_account.py`, `tests/test_client_portal_api.py`, `tests/test_client_discovery.py`, `tests/test_client_messages.py`, `tests/test_client_calendar.py`, `tests/test_apns_push.py` and `tests/test_agent_bob.py`.

ClientFlow also has `scripts/check-discovery-contract.sh`, an integration check against CRM's real Flask routes with isolated fake data. Its fixture requires a CRM checkout and Python environment; normal native builds and standalone checks do not. Follow ClientFlow's `docs/repository-split.md` to run it. Do not use production messages, emails or client records as test fixtures.

Before handing off, state the affected repositories, companion PRs, contract changes, compatibility behavior, deployment dependencies, checks performed and remaining gaps. Preserve existing repo-specific merge/release rules. Before a merge, check CI at the latest head and Copilot review comments/threads, fix valid findings and recheck. Wait for an in-progress Copilot review; disclose when none is available. App Store/TestFlight distribution and production deployment are separate actions from source changes.

## Keep this guide current

Each repository's `AGENTS.md` requires this guide. The same family guide is intentionally stored in all three repositories so a future agent can understand ownership from any one checkout. When repository ownership, API namespaces, authentication boundaries, shared services or deployment relationships change, update the guide and the affected root `AGENTS.md` summaries in all three repositories in the same workstream. A normal app-only change does not require editing all three copies. Verify the owning source before changing facts, and use links to the other repositories when local sibling folders are unavailable.

The split originated after agent PR #47 merged at `af791cd` on October 31, 2026. The agent-only cleanup merged in [agentflow PR #48](https://github.com/cnichols1734/agentflow/pull/48). Start new agent work from current `agentflow/main`. The private ClientFlow repository preserves the pre-split Git history; those historical commits contain both apps.
