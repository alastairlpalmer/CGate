# Horse list: split view, preview pane and mobile sheet

**Goal:** Make the horse list the place you work from. Pick a horse, read
its record beside the list, act on it, move to the next — without a page
load between each one.

**Scope:** the horses list (All, Location, Owner, Departed, search
results), a collapsible sidebar, and a mobile peek sheet. The Movements
log is a placement log, not a horse list, so it keeps today's table.

**Status:** planned.

---

## 1. The problem today

| Task | What happens now | Why it hurts |
|------|------------------|--------------|
| Check one horse's due dates | Click the row, read the page, press Back | Two page loads, and the list scroll position is lost |
| Check five horses | Repeat five times | Ten page loads for a job that is one glance each |
| Read a record on a phone | Same, on a slow yard connection | The list is gone the moment you tap |
| Work on a narrow laptop | The sidebar takes 256px, always | The list and the record cannot share the screen |

The record itself is already good. What is missing is a way to read it
without leaving the list.

## 2. What is built

### 2.1 Split view (desktop, `lg` and up)

```
┌──────────────┬───────────────────────────┐
│ All Horses   │  Antoinette - M brand     │
│  · Name A–Z  │  Active · No passport     │
│              │  ─────────────────────    │
│ ▸ Antoinette │  Location │ Rate │ On site│
│   Avicii     │  ─────────────────────    │
│   Beech      │  Move  Depart  Edit       │
│   Caraluna   │  CREDENTIALS              │
│   …          │  DOCUMENTS                │
│              │  TIMELINE                 │
└──────────────┴───────────────────────────┘
```

- Clicking a row loads the preview into the pane. No page load.
- The pane header carries `1 of 12`, previous/next arrows, `Open full
  page`, and a close button.
- `↑` `↓` `J` `K` step through the list. `Esc` closes the pane.
- With the pane open the list drops to Horse and Owner columns (the rest
  is in the pane). With the pane closed it keeps Location and Status.
- Without JavaScript every row is still an ordinary link to the horse
  page.

### 2.2 Mobile peek sheet (below `lg`)

- Tapping a row opens a bottom sheet at about a third of the screen.
- The sheet is **not** modal: no scrim, and the list above it still
  scrolls, so you can flick through horses and keep reading.
- Dragging the sheet up expands it to nearly full height.
- Dragging past the top opens the full horse page.
- It matches the existing pop-up sheet (`includes/_popup_sheet.html`):
  same rounded top, drag handle, and shadow.

### 2.3 Collapsible sidebar

- A toggle collapses the 256px sidebar to a 64px icon rail.
- The choice is kept in `localStorage` and applied before the first
  paint, so it does not flash on each page load.
- Collapsed icons keep their names as tooltips and as screen-reader text.

## 3. Data the design needs that does not exist yet

| Row in the design | Today | Change |
|-------------------|-------|--------|
| Microchip | No such field anywhere | Add `Horse.microchip` (blank allowed), and put it on the horse form and the quick-edit form |
| Worming due | `WormingTreatment` has a date, no due date | Add `WormingTreatment.next_due_date`, defaulted on save to the treatment date plus 13 weeks, the same way `FarrierVisit` defaults to six weeks |
| Flu / Tetanus | `VaccinationType` rows, not fixed fields | Read the horse's own vaccination types and print each one's next due date. A yard with different types gets its own labels, not ours |
| Farrier due | `FarrierVisit.next_due_date` | Already there |
| Passport | `Horse.has_passport`, `passport_number` | Already there |

Both new fields get a migration. The worming migration backfills
existing rows so the pane is not blank on day one. Worming reminders are
**out of scope** — the field is for display here.

## 4. How it is put together

| Piece | File |
|-------|------|
| Preview endpoint | `core/views/horses.py::horse_preview`, `core/urls.py` |
| Timeline builder, shared with the detail page | `core/views/horses.py::build_horse_timeline` |
| Preview markup | `templates/horses/partials/preview.html` |
| List markup | `templates/horses/horse_list.html`, `_horse_row.html` |
| Pane, sheet and keyboard handling | `static/js/split_view.js` |
| Sidebar rail and pane styling | `static/css/input.css` → `npm run build:css` |
| Sidebar toggle | `templates/base.html` |

The preview loads over htmx into `#horse-preview-body` with
`hx-select="unset"` and `hx-push-url="false"`, which is what the htmx
lint (`core/tests/test_htmx_lint.py`) requires of any partial swap.

The detail page and the preview share one timeline builder, so the two
can never drift apart.

## 5. Bulk select

The design has no checkbox on a row. Bulk select is not dropped: a
`Select` button in the list header turns the checkboxes and the bulk
action bar on. Rows stay clean until you ask for them.

## 6. Tests

- `core/tests/test_horse_preview.py` — the endpoint renders, respects
  feature access, and prints passport, microchip and every due date.
- `core/tests/test_query_counts.py` — a cap on the preview endpoint, so a
  per-row query cannot creep back in.
- Worming due date: default on save, and the backfill.
- `static/js/tests/split_view.test.js` — the index arithmetic behind
  next/previous, and which snap point a drag lands on.
- The existing htmx and vocabulary lints must stay green.
