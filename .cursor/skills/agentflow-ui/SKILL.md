---
name: agentflow-ui
description: AgentFlow product UI law. Always apply on any frontend, CSS, template, motion, or visual work.
alwaysApply: true
---

# AgentFlow UI

The current shipped CRM is the baseline. Match it. Do not redesign the product.

## The target

Apple Music on the web. Simple, quiet, premium. A daily tool for agents and coordinators, not a landing page. Dark slate shell, light work surfaces, restrained borders, orange accent. If it looks like a generic AI demo, it fails.

Visual slop and copy slop both fail. Copy lives in `documentation/skill.md` and `.cursor/skills/unslop/SKILL.md`. This skill is look, layout, and motion.

Brand orange is `#f97316` / `#ea580c`. Not red. Not purple. Light tokens: `--accent` and `--accent-ink` in `frontend/styles/app.css`. Tailwind `accent-500` / `accent-600` in `tailwind.config.js`. Dark skin may use `#fb923c` for the same orange, not a new hue.

## How to work

Start with the whole page. Constraints first: this skill, the `crm-*` classes already in the repo, the page you are on, and how an agent actually uses that screen. Do not spot-fix one control and wreck the rest.

If feedback arrives, ask whether it changes a constraint before you add an affordance. Most "just add a chip" notes are local. Keep them local unless someone says the rule changed.

After any UI change, look at every new element and ask if it is needed. Extra copy, icons, lines, and cards inside cards are the usual junk. Cut them.

Do not invent a new look inside a random template. Use the shared classes. If you need a new control, add it to the system first, then use it. System files: `crm-*` in `frontend/styles/app.css`, macros in `templates/components/ui.html`.

Judge the real product with real data. Rest, hover, focus, selected. Light and dark. Phone and desktop. Steal from Apple Music web and from screens already in this app, not from generic SaaS templates.

## Glass

Apple Music glass is allowed and required where the product already uses it. Shell chrome, tab bars, sheets, overlays.

Tokens live on `html.am-skin` in `static/css/am_skin.css`: `--am-glass`, `--am-glass-border`, `--am-glass-shadow`, `--am-glass-blur`. Chrome that already has it includes `header.crm-topbar`, the mobile header, `aside#sidebar.crm-sidebar`, `.crm-user-dropdown`, `.crm-search-results`, `.crm-notification-popover`, `.bob-panel`, and `.crm-row-menu`.

Work surfaces stay solid. `.crm-surface`, tables, forms, and the page canvas use `--paper` / `--canvas`. Do not frost those. Do not add generic startup glass, purple glow, or extra blur because it looks fancy.

Nested `backdrop-filter` inside already-glass chrome samples empty glass. Portal overlays to `body` the way the topbar menus already do.

## Motion

The motion already in the app comes from https://transitions.dev. That site is the reference. Match it. Do not invent a second motion language. Keep what we already wired up. Files: `static/css/transitions-root.css`, `static/css/transitions-snippets.css`, `static/css/transitions-bridge.css`, `static/js/transitions.js`.

Reuse the existing classes. `.t-page-slide`, `.t-panel-slide`, `.t-skel`, `.t-dropdown`, `.t-tabs`, `.t-confetti-overlay`. Brief and purposeful. No bounce, no elastic, no stagger-in circus. `transitions-root.css` lists bounce easings from the library. Do not use them.

Honor `prefers-reduced-motion`. Every new animation needs a reduce path, the way `.crm-rail` and the skeleton snippets already do.

## What to reuse

`documentation/STYLEGUIDE.md` is the class and token catalog. Not vibe.

- `frontend/styles/app.css` for `crm-*` and `:root` tokens (`--canvas`, `--paper`, `--ink`, `--hairline`, `--accent`)
- `templates/components/ui.html` for Jinja macros
- `tailwind.config.js` for `accent-*` and `brand-*`
- `static/css/am_skin.css` for the shipped Apple Music chrome

If the catalog and a live page disagree, this skill and the shipped UI win. Do not invent a new palette.

## Do not

- Redesign the product or import a second design language
- Purple, indigo, cyan, or neon gradients
- Red or purple as the brand color
- Extra glass on solid work surfaces
- Cards nested in cards, side-tab accent borders, giant pills, drop-shadow soup
- Centered app layouts, oversized empty-state art, fake dashboard drama
- Bounce, elastic, or looping attention motion. Do not invent a second motion language. Match https://transitions.dev
- A new type system. Keep the fonts the product already uses unless someone asked for a rebrand
- Navy gradients and `rounded-xl` cards from the old dashboard notes. Those are dead.

## QA bar

QA already scores CSS and motion PRs this way. Fail the change if you see ghosted or doubled text, stacked selected pills, leftover glow or halo on search, header chips that were not there, or a broken `.crm-segment` selected state.

Check the page you touched and the other surfaces that share that chrome. Empty, error, flag, and route variants too.
