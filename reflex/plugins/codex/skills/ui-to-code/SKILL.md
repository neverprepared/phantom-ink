---
name: ui-to-code
description: Convert UI screenshots, mockups, wireframes, or design descriptions into working component code. Use when the user provides an image of a UI or describes a visual layout and wants HTML/CSS/React/Svelte/Vue components generated.
---

# UI to Code

You convert visual designs into working component code. Given a screenshot, mockup, wireframe, or textual description, produce pixel-accurate, responsive, accessible components.

## Execution Model

1. **Analyze the visual** — identify layout structure, components, colors, typography, spacing
2. **Decompose** — break into logical components (header, card, form, table, sidebar, etc.)
3. **Determine framework** — infer from project context or ask if ambiguous
4. **Generate** — produce all components in one pass with proper hierarchy
5. **Style** — match colors, spacing, and typography from the visual as closely as possible

## What to Produce

### For Every Component
- **Markup** — semantic HTML (no `div` soup)
- **Styles** — scoped CSS/Tailwind/CSS modules (match project convention)
- **Props** — typed props interface for all configurable values
- **Responsiveness** — mobile-first, breakpoints at 640/768/1024/1280px
- **Accessibility** — ARIA labels, keyboard navigation, focus management, color contrast
- **States** — hover, focus, active, disabled, loading, empty, error

### Component Hierarchy
Generate a parent layout component that composes child components. Example:
```
DashboardPage
├── Sidebar
│   ├── NavItem
│   └── UserAvatar
├── Header
│   ├── SearchBar
│   └── NotificationBell
└── MainContent
    ├── StatsGrid
    │   └── StatCard
    └── DataTable
        └── TableRow
```

## Framework-Specific Patterns

### React (TypeScript)
```tsx
interface CardProps {
  title: string;
  description: string;
  variant?: 'default' | 'highlighted';
}

export function Card({ title, description, variant = 'default' }: CardProps) {
  return (
    <article className={`card card--${variant}`}>
      <h3 className="card__title">{title}</h3>
      <p className="card__description">{description}</p>
    </article>
  );
}
```

### Svelte 5
```svelte
<script lang="ts">
  interface Props {
    title: string;
    description: string;
    variant?: 'default' | 'highlighted';
  }
  let { title, description, variant = 'default' }: Props = $props();
</script>

<article class="card" class:highlighted={variant === 'highlighted'}>
  <h3>{title}</h3>
  <p>{description}</p>
</article>

<style>
  .card { /* ... */ }
</style>
```

### Vue 3 (Composition API)
```vue
<script setup lang="ts">
interface Props {
  title: string;
  description: string;
  variant?: 'default' | 'highlighted';
}
const props = withDefaults(defineProps<Props>(), {
  variant: 'default',
});
</script>

<template>
  <article :class="['card', `card--${props.variant}`]">
    <h3>{{ props.title }}</h3>
    <p>{{ props.description }}</p>
  </article>
</template>
```

## Color Extraction

When analyzing a screenshot:
- Extract the exact hex values for: primary, secondary, accent, background, text, border, error, success
- Build a CSS custom property palette:
```css
:root {
  --color-primary: #3b82f6;
  --color-bg: #0f172a;
  /* etc. */
}
```
- Use these variables throughout — never hardcode colors in components

## Typography Extraction

- Identify font families (or closest match from Google Fonts / system fonts)
- Extract: heading sizes (h1-h6), body text, small text, label text
- Extract: font weights, line heights, letter spacing
- Encode as CSS custom properties or Tailwind config

## Spacing System

- Derive the spacing scale from the visual (usually 4px or 8px base)
- Use consistent spacing tokens: `--space-1` through `--space-12`
- Apply via CSS custom properties or Tailwind spacing

## Rules

- **No placeholder images** — use CSS gradients, SVG shapes, or data URIs for icons
- **No Lorem Ipsum** — use realistic sample data matching the domain
- **Semantic HTML** — `<nav>`, `<main>`, `<article>`, `<section>`, `<aside>`, `<button>` (not `<div onclick>`)
- **No inline styles** — use classes, CSS modules, or Tailwind
- **Working interactions** — buttons have hover states, inputs have focus states, modals open/close
- **Icons** — use Lucide, Heroicons, or inline SVG (match project convention)

## Output Format

Write all component files using apply_patch. Print the component tree and any additional setup needed (font imports, icon library, etc.) at the end.
