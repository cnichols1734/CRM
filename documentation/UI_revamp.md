# UI Revamp Style Guide

This document captures the UI design preferences established during the dashboard redesign. Use this as a reference when updating other templates to maintain consistency.

## Reference Template

**Primary Reference:** `templates/dashboard.html`
- This template has been fully updated to match the desired style
- Review it for implementation patterns before starting work on other templates

**Secondary Reference:** `templates/auth/login.html`
- Clean, premium login page that inspired the dashboard redesign
- Uses CSS variables for theming

---

## What We DON'T Like (Remove These)

### Hover Effects
- ❌ `transform: translateY(-2px)` or any translateY on hover
- ❌ `transform: scale(1.02)` or any scale on hover
- ❌ `hover:-translate-y-*` Tailwind classes
- ❌ `hover:scale-*` Tailwind classes
- ❌ Cards that "lift" or "float" on hover — feels amateur/AI-generated

### Animations
- ❌ Staggered entrance animations (`animate-fade-in-up-delay-1`, `-delay-2`, etc.)
- ❌ `pulse-glow` or any pulsing animations
- ❌ Animated shimmer effects on banners
- ❌ Per-card slide-in animations
- ❌ Rotating icons on hover (`group-hover:rotate-180`)

### Visual Effects
- ❌ Glassmorphism (`backdrop-filter: blur()`)
- ❌ Heavy drop shadows (`shadow-lg`, `shadow-xl` on cards)
- ❌ Rainbow gradients (indigo → purple → pink)
- ❌ Gradient explosions everywhere
- ❌ Colored shadows (`shadow-orange-500/30`)
- ❌ Too many different accent colors competing

### General
- ❌ "Marketing" or "playful" feel
- ❌ Overly decorative elements
- ❌ Gimmicky interactions

---

## What We DO Like (Keep/Add These)

### Color Palette
- ✅ **Primary accent:** Orange (`#f97316`, Tailwind `orange-500/600`)
- ✅ **Secondary accent:** Navy (`#2d3e50`, CSS var `--brand-navy`)
- ✅ **Neutral base:** Slate grays (`slate-50` through `slate-800`)
- ✅ **Background:** Subtle radial gradients with very low opacity orange/navy tints

**Orange + Navy work beautifully together.** Use them as a pair:
- Orange for primary actions, active states, highlights
- Navy for secondary actions, headers, professional emphasis
- Sometimes navy is better than orange depending on context (e.g., "View All" buttons, less urgent actions)

**Navy color values:**
```css
--brand-navy: #2d3e50;
--brand-navy-2: #1a2634; /* darker variant */
```

Tailwind approximation: `slate-700` to `slate-800` (though custom hex is preferred for brand consistency)

### Buttons

**Primary Action Buttons (orange gradient) — for main CTAs:**
```css
background: linear-gradient(135deg, #f97316, #ea580c);
color: #fff;
font-weight: 600;
border-radius: 10px;
box-shadow: 0 2px 4px rgba(249, 115, 22, 0.2);
```
Hover: slightly darker gradient, subtle shadow increase

**Primary Action Buttons (navy gradient) — alternative for less urgent actions:**
```css
background: linear-gradient(135deg, #2d3e50, #1a2634);
color: #fff;
font-weight: 600;
border-radius: 10px;
box-shadow: 0 2px 4px rgba(45, 62, 80, 0.2);
```
Hover: slightly lighter navy, subtle shadow increase

**When to use Orange vs Navy:**
- **Orange:** "Add", "Create", "Start", primary page actions, active filter states
- **Navy:** "View All", "See More", navigation actions, secondary emphasis

**Secondary Buttons:**
```css
background: #f8fafc;
border: 1px solid #e2e8f0;
color: #64748b;
border-radius: 10px;
```
Hover: `bg-orange-50`, `border-orange-200`, `text-orange-600`
(Or navy variant: `hover:bg-slate-100`, `hover:text-slate-800`)

**Filter Pills/Tabs (active state):**
```html
class="bg-gradient-to-r from-orange-500 to-orange-600 text-white shadow-md"
```

### Cards
- ✅ Solid white background (`bg-white`)
- ✅ Subtle border (`border border-slate-200`)
- ✅ Minimal shadow (`shadow-sm` or custom `0 1px 3px rgba(15, 23, 42, 0.06)`)
- ✅ Rounded corners (`rounded-lg` or `rounded-xl`)
- ✅ Hover: border color shift only (`hover:border-slate-300`)

### Hover States (Subtle Only)
- ✅ Border color change: `hover:border-slate-300`
- ✅ Background tint: `hover:bg-slate-50` or `hover:bg-orange-50`
- ✅ Link color change: `hover:text-orange-600`
- ✅ Slight shadow increase (small): `hover:shadow` (not `hover:shadow-lg`)
- ✅ Transition: `transition-colors` (not `transition-all duration-300`)

### Gradients (Controlled Use Only)
Gradients are allowed ONLY on:
1. **Icon tiles** (small colored squares with icons)
2. **Primary action buttons** (one per section max)
3. **Section header accent bars** (if needed)

Everything else = solid colors

### Icon Tiles
```html
<div class="icon-tile w-10 h-10 bg-gradient-to-br from-orange-500 to-orange-600">
    <i class="fas fa-chart-line text-white"></i>
</div>
```

### Inputs
```css
border: 1px solid #e2e8f0;
border-radius: 10px;
background: #fff;
```
Focus: `border-color: #f97316; box-shadow: 0 0 0 3px rgba(249, 115, 22, 0.1);`

### Checkboxes
- ✅ `text-orange-500` when checked
- ✅ `hover:border-orange-400`
- ✅ `focus:ring-orange-500`

### Links
- ✅ Default: `text-slate-500` or `text-slate-600`
- ✅ Hover: `hover:text-orange-600`

### Kanban/Pipeline Columns
Use soft, muted header colors with dark text:

| Column | Header BG | Text Color | Count Badge |
|--------|-----------|------------|-------------|
| Preparing | `bg-slate-200` | `text-slate-700` | `bg-slate-400` |
| Active | `bg-teal-100` | `text-teal-700` | `bg-teal-500` |
| Under Contract | `bg-amber-100` | `text-amber-700` | `bg-amber-500` |
| Closed | `bg-sky-100` | `text-sky-700` | `bg-sky-500` |

### Animations (Minimal)
- ✅ Simple page fade-in on load (optional): `animation: fadeIn 0.4s ease-out`
- ✅ `transition-colors` for color changes
- ✅ `transition-opacity` for showing/hiding
- ❌ No per-element staggered animations

---

## CSS Variables (from login page)

```css
:root {
    --brand-navy: #2d3e50;
    --brand-navy-2: #1a2634;
    --brand-orange: #f97316;
    --bg: #f8fafc;
    --panel: #ffffff;
    --text: #0f172a;
    --muted: #64748b;
    --border: #e2e8f0;
    --shadow-sm: 0 1px 3px rgba(15, 23, 42, 0.06);
    --shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
    --focus: rgba(249, 115, 22, 0.1);
}
```

---

## Templates To Update

Use this checklist when updating other templates:

- [ ] `templates/contacts/list.html` - Already has good orange buttons, may need hover cleanup
- [ ] `templates/contacts/view.html`
- [ ] `templates/tasks/list.html`
- [ ] `templates/tasks/view.html`
- [ ] `templates/transactions/list.html`
- [ ] `templates/transactions/view.html`
- [ ] Other templates as needed

---

## Quick Checklist When Updating a Template

1. [ ] Remove all `hover:-translate-y-*` and `hover:scale-*` classes
2. [ ] Remove `animate-fade-in-up-delay-*` staggered animations
3. [ ] Remove `backdrop-filter`, `blur()`, glassmorphism
4. [ ] Remove animated shimmer effects
5. [ ] Remove `shadow-lg`/`shadow-xl` on cards (use `shadow-sm`)
6. [ ] Replace rainbow gradients with orange gradient on primary buttons only
7. [ ] Add `hover:border-slate-300` to cards instead of lift effects
8. [ ] Update links to `hover:text-orange-600`
9. [ ] Update checkboxes to use `text-orange-500`
10. [ ] Update input focus to orange ring
11. [ ] Ensure filter tabs use orange gradient for active state

---

## Summary

**The goal:** Professional, clean, premium — not gimmicky, not marketing, not playful.

**The vibe:** Like a well-designed SaaS tool (Linear, Notion, Stripe Dashboard) — restrained, functional, with controlled accent color usage.

**The colors:** Orange (`#f97316`) + Navy (`#2d3e50`) — these two look great together. Use orange for energy/action, navy for stability/professionalism.

**When in doubt:** Less is more. Solid colors over gradients. Subtle borders over shadows. Color changes over transforms.
