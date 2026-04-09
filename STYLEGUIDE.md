# UI Style Guide

This document describes the design system used on pages that have been migrated to the new frontend stack. Use this as the reference when converting remaining pages.

## Stack

| Layer | Tool | Notes |
|---|---|---|
| CSS framework | Tailwind CSS 3.x | Compiled via Vite, NOT the CDN |
| JS framework | Stimulus (Hotwired) | Lightweight controllers, no SPA |
| Build tool | Vite | Entry: `frontend/main.js` -> `static/dist/` |
| PostCSS | Autoprefixer | Via `postcss.config.js` |

**Legacy pages** still load Tailwind CDN + DaisyUI CDN from `base.html`. Migrated pages use classes defined in `frontend/styles/app.css` which compile into `static/dist/app.css`. Both coexist during the transition.

## File Structure

```
frontend/
  main.js                          # Vite entry point, registers Stimulus controllers
  styles/
    app.css                        # All component classes (@layer components)
  controllers/
    dashboard_page_controller.js   # data-controller="dashboard-page"
    contacts_page_controller.js    # data-controller="contacts-page"

templates/
  components/
    ui.html                        # Jinja2 macros (page_header, badge, avatar, etc.)

static/dist/                       # Vite build output (committed to git)
  app.js
  app.css
```

## Color Palette

Defined in `tailwind.config.js`:

| Token | Usage | Values |
|---|---|---|
| `brand-50` to `brand-900` | Blue-grey scale for shell/sidebar elements | `#f0f4f8` to `#102a43` |
| `accent-50` to `accent-700` | Orange for primary actions and highlights | `#fff7ed` to `#c2410c` |
| `slate-*` | Tailwind built-in slate for all text, borders, backgrounds | Standard Tailwind |

**CSS custom properties** (set in `app.css` `:root`):

```css
--crm-shell:       #0f172a   /* Dark sidebar/header background */
--crm-shell-muted: #1e293b
--crm-page:        #f8fafc   /* Light page background */
--crm-panel:       #ffffff   /* Card/surface background */
--crm-border:      #e2e8f0
--crm-text:        #0f172a
--crm-muted:       #64748b
--crm-accent:      #f97316   /* Primary accent (orange) */
```

## Design Tokens and Conventions

- **Page background**: `bg-[#eef0f3]` (light warm grey)
- **Cards/surfaces**: white with `border border-slate-200 rounded-md`
- **Text hierarchy**: `text-slate-950` (headings) > `text-slate-900` (body) > `text-slate-600` (secondary) > `text-slate-500` (muted) > `text-slate-400` (disabled)
- **Font stack**: System fonts (`-apple-system, SF Pro Display, Helvetica Neue, Arial`)
- **Border radius**: `rounded-md` (6px) everywhere; `rounded-full` only for avatars
- **Shadows**: None on most elements (flat design); optional `shadow-panel` for elevated cards
- **Icons**: Font Awesome 6 (`fas fa-*`), sized with `text-xs` or `text-sm`

## Component Classes

All defined in `frontend/styles/app.css` under `@layer components`. Prefix: `crm-`.

### Layout

| Class | Purpose |
|---|---|
| `.crm-page` | Full-height page wrapper with grey background |
| `.crm-page__inner` | Centered content area with horizontal/vertical padding |
| `.crm-page-header` | Page title bar with eyebrow, title, and action buttons |
| `.crm-page-actions` | Flex container for action buttons (right side of headers) |

### Surfaces (Cards)

| Class | Purpose |
|---|---|
| `.crm-surface` | White card with border |
| `.crm-surface-muted` | Grey card with border (for nested cards) |
| `.crm-surface-header` | Card header row with border-bottom |
| `.crm-surface-body` | Card body with padding |

### Typography

| Class | Purpose |
|---|---|
| `.crm-page-title` | `text-2xl md:text-3xl font-semibold tracking-tight` |
| `.crm-page-header__eyebrow` | `text-xs uppercase tracking-wide text-slate-500` |
| `.crm-section-kicker` | Section eyebrow (same style as page eyebrow) |
| `.crm-section-title` | `text-lg font-semibold` |
| `.crm-section-description` | `text-sm text-slate-500` |

### Buttons

| Class | Appearance |
|---|---|
| `.crm-btn` | Base: rounded-md, border, px-3 py-2, text-sm, focus ring |
| `.crm-btn-primary` | Orange background, white text |
| `.crm-btn-secondary` | White background, slate border, slate text |
| `.crm-btn-subtle` | Slate-100 background |
| `.crm-btn-danger` | Red-50 background, red text |

Usage pattern:
```html
<a href="..." class="crm-btn crm-btn-primary">
    <i class="fas fa-plus text-xs"></i>
    Create contact
</a>
```

### Badges

| Class | Appearance |
|---|---|
| `.crm-badge` | Neutral (slate background, slate border) |
| `.crm-badge-accent` | Orange |
| `.crm-badge-success` | Green |
| `.crm-badge-warning` | Amber |
| `.crm-badge-info` | Sky blue |

Use the Jinja macro: `{{ badge('Label', 'success') }}`

### Avatars

| Class | Size |
|---|---|
| `.crm-avatar-sm` | 32px (h-8 w-8) |
| `.crm-avatar-md` | 40px (h-10 w-10) |
| `.crm-avatar-lg` | 56px (h-14 w-14) |

Use the Jinja macro: `{{ avatar('CN', 'sm') }}`

### Tables

| Class | Purpose |
|---|---|
| `.crm-table-wrap` | Scrollable container with border |
| `.crm-table` | Full-width table |
| `.crm-table thead th` | Uppercase, tracking, slate-50 background |
| `.crm-table tbody td` | Standard cell styling, cursor-pointer rows |
| `.crm-table-link` | Bold link text in cells |

### Forms

| Class | Purpose |
|---|---|
| `.crm-input` | Standard text input |
| `.crm-select` | Standard select dropdown |
| `.crm-search` | Search input (wider, with placeholder styling) |
| `.crm-filter-grid` | 3-column grid for filter panels |

### Dashboard-Specific

| Class | Purpose |
|---|---|
| `.crm-kpi-grid` | 3-column KPI card row |
| `.crm-kpi` | Single KPI card |
| `.crm-kpi__label` | KPI label (uppercase) |
| `.crm-kpi__value` | KPI big number |
| `.crm-kpi__meta` | KPI description text |
| `.crm-board` | 4-column kanban grid |
| `.crm-board-column` | Single kanban column |
| `.crm-deal-card` | Transaction card in kanban |

### Navigation

| Class | Purpose |
|---|---|
| `.crm-segment` | Segmented control container (tabs/toggles) |
| `.crm-segment__item` | Individual segment option |
| `.crm-segment__item.is-active` | Active segment (dark background) |
| `.crm-pill-tabs` | Pill-style tab container |
| `.crm-pill-tab` | Individual pill tab |

### Lists

| Class | Purpose |
|---|---|
| `.crm-stack` | Vertical list with gap-2 |
| `.crm-list-item` | List row with border, padding, hover state |

### Empty States

| Class | Purpose |
|---|---|
| `.crm-empty` | Empty state container |
| `.crm-empty-center` | Centered variant |
| `.crm-empty-icon` | Icon circle for empty states |

Use the Jinja macro:
```html
{% call empty_state('No contacts yet', 'Add your first contact.', 'fa-address-book') %}
    <a href="..." class="crm-btn crm-btn-primary">Create contact</a>
{% endcall %}
```

## Jinja2 Macros (`templates/components/ui.html`)

Import at the top of every migrated template:

```html
{% from "components/ui.html" import page_header, section_header, avatar, badge, empty_state %}
```

### `page_header(title, description, eyebrow)`
Page-level header with optional eyebrow text and caller block for action buttons.

```html
{% call page_header('Contacts', '', 'Database') %}
    <a href="..." class="crm-btn crm-btn-primary">Create contact</a>
{% endcall %}
```

### `section_header(title, description, kicker, badge)`
Section header inside a surface card.

### `avatar(initials, size)`
Circular avatar with initials. Sizes: `sm`, `md` (default), `lg`.

### `badge(label, tone)`
Inline badge. Tones: `neutral` (default), `accent`, `success`, `warning`, `info`.

### `empty_state(title, description, icon)`
Empty state placeholder with icon, text, and optional caller block for action buttons.

## Stimulus Controllers

Each migrated page gets its own Stimulus controller in `frontend/controllers/`.

### Naming Convention
- File: `{page_name}_controller.js` (snake_case)
- Registration: `application.register("{page-name}", Controller)` (kebab-case)
- Template: `data-controller="{page-name}"`
- Actions: `data-action="{page-name}#{methodName}"`
- Targets: `data-{page-name}-target="{targetName}"`

### Pattern

```javascript
import { Controller } from "@hotwired/stimulus";

export default class extends Controller {
  static targets = ["searchInput", "filterPanel"];

  connect() {
    // Setup logic
  }

  disconnect() {
    // Cleanup (clear timeouts, etc.)
  }

  someAction(event) {
    event.preventDefault();
    // Handle user interaction
  }
}
```

Register in `frontend/main.js`:
```javascript
import MyPageController from "./controllers/my_page_controller";
application.register("my-page", MyPageController);
```

## Migration Checklist

When converting a page to the new design system:

1. Add `{% from "components/ui.html" import page_header, section_header, avatar, badge, empty_state %}` at the top
2. Wrap content in `<div class="crm-page" data-controller="my-page"><div class="crm-page__inner">...</div></div>`
3. Replace custom headers with `{% call page_header(...) %}` macro
4. Replace card containers with `.crm-surface` / `.crm-surface-header` / `.crm-surface-body`
5. Replace buttons with `.crm-btn .crm-btn-primary` (or secondary/subtle/danger)
6. Replace badges with `{{ badge('Label', 'tone') }}` macro
7. Replace avatars with `{{ avatar('AB', 'md') }}` macro
8. Replace tables with `.crm-table-wrap` > `.crm-table` structure
9. Replace empty states with `{% call empty_state(...) %}` macro
10. Move any inline JS to a Stimulus controller in `frontend/controllers/`
11. Register the controller in `frontend/main.js`
12. Run `npm run build` to rebuild

## Build Commands

```bash
# Development (watch mode - rebuilds on file changes)
npm run dev

# Production build
npm run build

# Output goes to static/dist/app.js and static/dist/app.css
```
