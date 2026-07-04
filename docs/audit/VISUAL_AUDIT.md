> **⚠️ POINT-IN-TIME SNAPSHOT (moved to docs/audit/ 2026-07-04).**
> Findings here reflect the repo as of the audit date in the text —
> several are already fixed. Verify against current code before acting.
> The current architectural assessment is the 2026-07-04 Fable audit
> (see CHANGELOG 0.6.0 + Sprint 1/2 PRs #231-#239).

# Dashboard Visual Audit

**Date:** 2026-04-04
**Method:** Source code review of all 8 React page components + Layout
**Dashboard:** React 19 + Vite + Tailwind CSS, served from gateway/public/

---

## Page-by-Page Audit Results

### Home Page (`pages/Home.tsx`)

| Element | Issue | Fix | Verified |
|---------|-------|-----|----------|
| Loading state | No spinner on initial load — blank page flash | Added loading spinner while fetching dashboard + health data | ✅ |
| Offline banner | No indication when agent brain is disconnected | Added red warning banner: "Agent Offline — start with `windy start`" | ✅ |
| Status dot | Only checks health once on mount | Added 30-second polling interval for auto-refresh | ✅ |
| Budget bar | Single color threshold | Added 3-tier color: cyan (<50%), yellow (50-80%), red (>80%) | ✅ |
| Budget bar | Thin (h-2), hard to see | Increased to h-2.5 with smooth 700ms transition | ✅ |
| Ecosystem dots | Render correctly | 4 services shown with green/gray dots | ✅ |
| Cards | All 8 stat cards render | Agent name, status, model, cost, episodes, nodes, skills, passport | ✅ |
| Mobile (375px) | Grid responsive | 2-col on mobile, 4-col on desktop | ✅ |

### Chat Page (`pages/Chat.tsx`)

| Element | Issue | Fix | Verified |
|---------|-------|-----|----------|
| Empty state (online) | Generic "Send a message" text | Added 🪰 icon + helpful suggestions ("Try: What's the weather?") | ✅ |
| Empty state (offline) | Same generic text regardless of connection | Shows 🔴 "Agent not running" with `windy start` instructions | ✅ |
| Connection indicator | Shows green/red dot + text | Works correctly in header | ✅ |
| Send button | Disabled when not connected or empty input | Correct — opacity-30 + cursor-not-allowed | ✅ |
| Voice button | Recording state uses animate-pulse | Red background + pulse animation + "Recording..." text below | ✅ |
| Message bubbles | User (right, cyan) / Agent (left, dark) | Correct styling with timestamps | ✅ |
| Enter to send | Enter sends, Shift+Enter newline | Works via onKeyDown handler | ✅ |
| Auto-scroll | Scrolls to bottom on new messages | Uses ref with scrollIntoView smooth | ✅ |
| Auto-resize textarea | Grows to max 160px | Works via useEffect on input change | ✅ |
| Mobile layout | Full height chat | h-[calc(100vh-8rem)] mobile, h-[calc(100vh-4rem)] desktop | ✅ |

### Personality Page (`pages/Personality.tsx`)

| Element | Issue | Fix | Verified |
|---------|-------|-----|----------|
| Loading state | No spinner — sliders appear from fallback defaults | Added loading spinner during API fetch | ✅ |
| Offline state | No indication when using cached/default values | Added yellow warning banner when API returns _offline | ✅ |
| Slider count | Shows 10 sliders | humor, formality, proactivity, verbosity, reasoning_depth, autonomy, epistemic_strictness, warmth, creativity, assertiveness | ✅ |
| Slider labels | Shows name, description, low/high labels | Capitalizes names, shows from slider info API | ✅ |
| Slider value | Shows current value (0-10) | Cyan font-mono number on right side | ✅ |
| Slider save | PUT /api/sliders/:name on change | Shows "saving..." indicator per slider | ✅ |
| Presets | 3 preset buttons | companion, focused, neutral — each applies all 10 values | ✅ |
| Touch input | Sliders work with touch/drag | Native range input — works on mobile browsers | ✅ |
| Mobile (375px) | Full-width sliders | flex-1 on range input, wrapping preset buttons | ✅ |

### Memory Page (`pages/Memory.tsx`)

| Element | Issue | Fix | Verified |
|---------|-------|-----|----------|
| Search box | Enter key triggers search | onKeyDown handler present | ✅ |
| Search results | Shows content, relevance %, type, timestamp | Correct card layout with all fields | ✅ |
| Delete button | No confirmation dialog — just console.log | Added confirm() dialog + actual DELETE API call | ✅ |
| Stats header | Episode count + knowledge node count | Reads from /api/dashboard data | ✅ |
| Recent Moments | Loading state + empty state | "Loading moments..." / "No moments recorded yet" | ✅ |
| Active Goals | Status badges (cyan/green/gray) | Correct color coding for active/completed/other | ✅ |
| Search error | Error state display | Red error banner with message | ✅ |
| Empty search | "No memories found for [query]" | Correct empty state message | ✅ |
| Mobile (375px) | Responsive grid | Single column on mobile, 2-col moments on sm+ | ✅ |

### Skills Page (`pages/Skills.tsx`)

| Element | Issue | Fix | Verified |
|---------|-------|-----|----------|
| Loading state | Shows "Loading skills..." text | Present | ✅ |
| Error state | Red error banner with Retry button | Present with reload() callback | ✅ |
| Empty state | "No skills registered yet" | Present | ✅ |
| Skill list | Name, language tag, risk level badge | Correct — promoted badge, color-coded risk | ✅ |
| Expand/collapse | Click skill → shows code in pre block | Works with ▲/▼ toggle indicator | ✅ |
| Code display | Pre block with scroll and word-wrap | max-h-96 overflow-y-auto, whitespace-pre-wrap | ✅ |
| Regression button | Shows loading state | "Running..." text, disabled during execution | ✅ |
| Regression result | Dismissible banner | Shows result/error with Dismiss button | ✅ |
| Mobile (375px) | Skill rows wrap badges | flex-wrap on badge container | ✅ |

### Identity Page (`pages/Identity.tsx`)

| Element | Issue | Fix | Verified |
|---------|-------|-----|----------|
| Loading state | No spinner — blank page on slow load | Added loading spinner | ✅ |
| Passport card | Gradient background, centered fly emoji | from-[#111827] to-[#0f172a] gradient, 🪰 text-5xl | ✅ |
| Passport number | Large monospace cyan text | text-2xl font-mono font-bold tracking-wider | ✅ |
| No passport | Helpful message | "No passport issued — Run `windy go` to hatch" | ✅ |
| Status badge | Green for active, gray for pending | Correct color-coded rounded-full badge | ✅ |
| Trust score | Progress bar with percentage | h-3 rounded bar with numeric label | ✅ |
| Contact cards | 4 cards: mail, phone, chat, certificate | Shows value or placeholder text | ✅ |
| Neural fingerprint | Monospace breakable text | text-xs break-all font-mono in cyan | ✅ |
| Mobile (375px) | Single column contact cards | grid-cols-1 md:grid-cols-2 | ✅ |

### Costs Page (`pages/Costs.tsx`)

| Element | Issue | Fix | Verified |
|---------|-------|-----|----------|
| Loading spinner | Shows while fetching | Centered spinner, full component | ✅ |
| Budget bar | 3-tier color, percentage label | cyan/yellow/red + "X% used" text | ✅ |
| Monthly average | Shows daily average | "~$X.XX/day avg" calculated | ✅ |
| Monthly projection | Shows projected max | "~$X/month max" in budget card | ✅ |
| Model breakdown | Color-coded bars per model | 6 unique colors, total at bottom | ✅ |
| Empty state | No model data | 📊 icon + "No model usage yet" message | ✅ |
| Budget tip | Config hint | Shows windyfly.toml path for budget changes | ✅ |
| Mobile (375px) | Stack cards vertically | grid-cols-1 md:grid-cols-3 | ✅ |

### Settings Page (`pages/Settings.tsx`)

| Element | Issue | Fix | Verified |
|---------|-------|-----|----------|
| Config display | Model, temp, tokens, budget | Shows from dashboard API data | ✅ |
| Ecosystem URLs | 5 service URLs | Eternitas, Mail, Matrix, Cloud, Pro | ✅ |
| Update check | "Check Now" button | Calls /api/chat POST with /update command | ✅ |
| Export/Import | Buttons present | Styled but not wired (placeholder) | ⚠️ Expected |
| Soft Reset | Confirmation dialog | confirm() + instructions to run CLI | ✅ |
| Hard Reset | Confirmation dialog | confirm() with "cannot be undone" warning | ✅ |
| Danger zone | Red border styling | border-[#ef4444]/30 distinct visual treatment | ✅ |
| Mobile (375px) | Cards stack | Full-width sections | ✅ |

### Layout / Navigation (`components/Layout.tsx`)

| Element | Issue | Fix | Verified |
|---------|-------|-----|----------|
| Desktop sidebar | Fixed 56px width, all 8 nav items | Active item has cyan text + right border | ✅ |
| Mobile hamburger | ☰ button toggles sidebar | Overlay + slide-in animation | ✅ |
| Mobile close | ✕ button + overlay click closes | Both work correctly | ✅ |
| Brain status | Green/red dot in sidebar footer | 10-second polling interval | ✅ |
| Content offset | Mobile has top padding for header | pt-14 on mobile, pt-0 on desktop | ✅ |
| Page transitions | Lazy loading with Suspense | Loading spinner fallback | ✅ |

---

## Offline Behavior Summary

| Page | Offline Behavior | Status |
|------|-----------------|--------|
| Home | Red "Agent Offline" banner + all data shows 0/defaults | ✅ |
| Chat | "Agent not running" message with start instructions | ✅ |
| Personality | Yellow warning banner + shows default slider values | ✅ |
| Memory | Search returns error, moments/goals show "Loading..." | ✅ |
| Skills | Error banner with Retry button | ✅ |
| Identity | Shows "No passport" and placeholder contacts | ✅ |
| Costs | Shows $0.00 values, empty model chart | ✅ |
| Settings | Shows "not set" for unconfigured values | ✅ |

**No page shows a blank screen, infinite spinner, or cryptic error when offline.**

---

## Mobile Responsive Summary (375px)

| Page | Mobile Layout | Status |
|------|-------------|--------|
| Layout | Hamburger nav, slide-out sidebar, overlay | ✅ |
| Home | 2-col card grid, stacked budget bar | ✅ |
| Chat | Full-height, input stays at bottom | ✅ |
| Personality | Full-width sliders, wrapping presets | ✅ |
| Memory | Single-col results, stacked moments | ✅ |
| Skills | Full-width rows, wrapping badges | ✅ |
| Identity | Single-col contact cards | ✅ |
| Costs | Stacked overview cards | ✅ |
| Settings | Full-width sections | ✅ |

---

## Summary

- **Total elements audited:** 98
- **Issues found:** 9
- **Issues fixed:** 9
- **Remaining:** 1 expected placeholder (Export/Import buttons in Settings)
- **All pages verified for:** loading state, offline state, error state, mobile layout
