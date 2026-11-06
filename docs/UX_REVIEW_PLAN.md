# UX and visual review: plan

Date: 6 November 2026
Scope: the Django app in `horse_management/`, after the November changes
(focus ring, view transitions, sticky table headers, selected-row style,
no edge bars or gradients, resizable split views, "Cgate" name).

Method: seeded the app with `tools/qa/seed_qa.py` and `tools/qa/mobile_seed.py`.
Rendered 24 pages at desktop (1440px) and phone (iPhone 13). Read the
templates and `static/css/input.css` to confirm each finding.

## Status

| Phase | State |
|---|---|
| 1. Quick fixes | Done (alastairlpalmer/CGate#242) |
| 2. Action hierarchy | Done in this branch: shared More menu (`static/js/more_menu.js`), A1 to A4, B2 |
| 3 to 5 | Not started |

## 1. Summary

The base is strong. The palette, the type, the focus ring, the selected-row
style and the phone tab bar are good. Most problems are now about
**hierarchy**: too many actions with the same weight, and small
inconsistencies that make the app feel less exact than it is.

Do these five first. They give the largest gain for the least work.

| # | Change | Why | Effort |
|---|---|---|---|
| 1 | One primary action per page. Put the other actions in a "More" menu. | Invoice and horse pages show 4 to 6 buttons in 3 colours. Users must read all of them. | M |
| 2 | Format all money with the `gbp` filter. | Invoices show `£1470.00`. The dashboard shows `£1,470.00`. | S |
| 3 | Keep red for overdue, errors and delete only. | "Veterinary" and "Vaccination" badges are red. Red then means nothing. | S |
| 4 | Give the phone horse list room for names. | Three row icons take 40% of the width. Long names wrap to 4 lines. | M |
| 5 | Use one set of action words. | The dashboard says "Record". Health says "Re-vaccinate" and "Book" for the same task. | S |

Effort: S = under half a day. M = 1 to 2 days. L = 3 days or more.

## 2. Keep these

Do not change these when you do the work below.

- The palette (forest, cream, saddle) and the three self-hosted fonts.
- The keyboard focus ring (one style for every control).
- The selected-row and selected-card tile.
- The phone tab bar, the More sheet and the 44px touch targets.
- The pop-up edit sheet, the sticky form footer and the toasts.
- The dashboard "Needs action" card with a button on each row.
- The horse timeline with filter chips.

## 3. Findings and changes

Each item has: the problem, where you see it, the change, and the files.

### A. Actions and hierarchy

#### A1. Invoice page: too many buttons with equal weight — priority 1, M

- **Problem.** The header has 6 buttons: Edit, Download PDF, Download CSV,
  Send Invoice (teal), Delete Draft (red), Record Payment (rust). They wrap
  to two rows on desktop and three rows on phone. Two different "primary"
  colours compete.
- **Change.**
  1. Show one primary button. Choose it from the invoice status:
     - Draft: **Send invoice**.
     - Sent or part paid: **Record payment**.
     - Paid: **Download PDF**.
  2. Show **Download PDF** as a secondary button when it is not the primary.
  3. Put Edit, Download CSV and Delete draft in a **More (⋯)** menu.
     Put Delete last, in red, under a divider.
  4. Use the same layout on phone. The row must fit on one line.
- **Files.** `templates/invoicing/invoice_detail.html`. Make a shared
  `templates/includes/_more_menu.html` (Alpine, keyboard-safe, uses the
  existing pop-up rules).

#### A2. Horse page: "Depart" is the first and loudest button — priority 1, M

- **Problem.**
  - Depart is red and first in the header. It is a rare, serious action.
  - "Add photo" has three entry points: header, Photos card, Quick Actions.
  - On phone, Quick Actions are at the bottom of a 7,600px page. The most
    used actions are the hardest to reach.
  - The Quick Action icons use 6 colours (red, orange, blue, grey).
- **Change.**
  1. Header: **Move** (primary), **Edit** (secondary), **More (⋯)**.
     Put Depart in More, under a divider.
  2. Remove the Photo button from the header. Keep "+ Add" on the Photos card.
  3. On phone, put a row of 4 quick actions directly under the header:
     Move, Vaccination, Farrier, More. "More" opens the existing bottom sheet
     with the other actions.
  4. Show all quick-action icons in one colour (forest on sand).
  5. Remove the Details card. It only repeats "Passport: Yes" and
     "Status: Active", which the header badges already show. Move
     "+ Condition" into the Quick Actions.
- **Files.** `templates/horses/horse_detail.html`,
  `templates/includes/_icons.html`.

#### A3. Owner page: the key fact is missing — priority 1, M

- **Problem.**
  - The page does not show what the owner owes. The user must open invoices.
  - Archive is the first header button.
  - The Personal Data card (download data, erase) takes a full-width card on
    every owner page. Staff use it rarely.
  - "2 horses are on the yard…" sits as small grey text under the title. It
    reads as a stray note.
- **Change.**
  1. Add a summary strip under the name: **Outstanding**, **Unbilled
     charges**, **Last payment** (date and amount), **Horses**.
     Make each figure a link to the filtered list.
  2. Header: **Create invoice** (primary), **Statement**, **Edit**,
     **More (⋯)**. Put Archive, Download data and Erase in More.
  3. Show the "move or depart horses first" text only when the user picks
     Archive, inside the confirm sheet.
- **Files.** `templates/owners/owner_detail.html`,
  `core/views/` (owner detail context: totals already exist for statements).

#### A4. Row actions in tables — priority 2, M

- **Problem.**
  - Horse list: three grey icons per row (photo, edit, move) with no labels.
  - Charges list: a red "Delete" on every unbilled row. Red text on many rows
    makes the table look like it has errors.
- **Change.**
  1. Make the full row a link to the record (desktop and phone).
  2. Keep one visible row action that is the most common: **Move** for
     horses, **Edit** for charges.
  3. Put the other actions in a ⋯ menu at the end of the row.
  4. Show Delete only in the ⋯ menu and on the edit form.
- **Files.** `templates/horses/_horse_row.html`, `_horse_row_flat.html`,
  `templates/billing/charge_list.html` and its partials.

#### A5. One set of action words — priority 1, S

- **Problem.** The same task has different names:

  | Task | Dashboard | Health page |
  |---|---|---|
  | Record a vaccination | Record | Re-vaccinate |
  | Record a farrier visit | Record visit | Book |

  "Book" suggests a calendar booking. The app does not book.
- **Change.** Use "Record vaccination", "Record farrier visit",
  "Record worming" everywhere. Use a verb + noun on every button.
- **Files.** `templates/health/health_dashboard.html`, dashboard partials.

### B. Page orientation

#### B1. List pages have no visible title on desktop — priority 2, S

- **Problem.** Horses, Locations, Invoices and Charges hide the `<h1>`. This
  was a choice (the tab bar names the section on phone). On desktop, the
  page starts with a ~50px empty band and then a row of buttons. Health and
  Finances show a subtitle with no title above it.
- **Change.** On desktop only (`lg:` and up), show one compact title row:
  title, record count, and the primary action on the right. Keep the phone
  layout as it is. This also removes the empty band.
- **Files.** `templates/includes/page_header.html`, and the list templates
  that use `<h1 class="sr-only">`.

#### B2. Button order puts Export first — priority 2, S

- **Problem.** Invoices: Export CSV, Generate Monthly, **Create Invoice**.
  Charges: Export CSV, Providers, **Add Charge**. The least used action is
  first.
- **Change.** Primary action at the far right. Secondary actions to its
  left. Move Export CSV into a ⋯ menu or into the table footer (the horse
  list already puts "Export all horses (CSV)" there; do the same on every
  list).

#### B3. Two search boxes on one page — priority 3, S

- **Problem.** Invoices and Charges show the global search in the top bar
  and a second search box under the buttons. Users can type in the wrong one.
- **Change.** Label the page box as a filter: placeholder "Filter invoices
  by owner, number…" and a filter icon, not a search icon. Put it in the
  same row as the filter button.

### C. Numbers, dates and status words

#### C1. Money format — priority 1, S

- **Problem.** The invoice list, invoice detail and the invoice emails show
  `£1470.00`. The dashboard shows `£1,470.00`. 26 templates format money
  by hand with `floatformat:2` (written as `£` or `&pound;`). Only 16
  places use the `gbp` filter.
- **Change.** Use `|gbp` (in `core/templatetags/ui_extras.py`) everywhere.
  Add a test that fails if a template has `£` or `&pound;` before
  `{{ …|floatformat:2 }}`.
- **Files.** Find them with `grep -rln "floatformat:2" templates`.
  Start with `invoicing/invoice_list.html` (summary line),
  `invoicing/invoice_detail.html` and the emails in
  `notifications/email/`.

#### C2. Date format — priority 1, S

- **Problem.** The templates use 10 date formats. The invoice Details card
  shows "30 Jul 2026" and "06/11/2026" next to each other. In the charges
  and invoices tables, dates wrap to two lines.
- **Change.**
  1. Use "6 Nov 2026" (`j M Y`) for dates. Use "6 Nov" only where the year
     is clear from context. Use `d/m/Y` only in CSV and form inputs.
  2. Add `whitespace-nowrap` to date and money cells.
  3. Add a template filter `|day` so the format lives in one place.

#### C3. KPI numbers look like code — priority 2, S

- **Problem.** `.kpi-value` uses JetBrains Mono at 36px. "£0.00" reads like
  a terminal, with wide gaps between digits.
- **Change.** Use DM Sans with `tabular-nums` for KPI and money figures.
  Keep the mono font for codes only (invoice numbers, batch numbers, keys).
- **Files.** `static/css/input.css` (`.kpi-value`, `.board-site-meta b`,
  `.chip-count b`).

#### C4. "Draft (past due)" — priority 1, S

- **Problem.** A draft is not sent, so it cannot be past due. The pill also
  wraps to two lines in the table.
- **Change.** Show "Draft" as the status. Add a separate warning line:
  "Not sent. Due date has passed." Keep pills to one line.
- **Files.** `invoicing/models.py` (status label, line 372), invoice list
  and detail templates.

#### C5. Invoice line items order — priority 2, S

- **Problem.** Horses appear as 1, 3, 5, 2, 4, 6 (grouped by rate, but the
  user cannot see that).
- **Change.** Sort horse groups by horse name. Show the rate on each line,
  as now.

#### C6. Totals shown twice — priority 3, S

- **Problem.** The invoice page shows Net, VAT and Amount Due in the Totals
  card and again under the line items.
- **Change.** Keep the totals under the line items (this matches the PDF).
  Replace the Totals card with a small "Amount due" and "Paid" pair in the
  header area.

### D. Colour and visual system

#### D1. Red is used for categories — priority 1, S

- **Problem.** "Veterinary" and "Vaccination" type badges are red. "Unbilled"
  is rust. On the charges table almost every row has red or rust.
- **Change.**
  - Red: overdue, errors, delete. Nothing else.
  - Rust (saddle): primary action and "needs action" only.
  - Category badges (Veterinary, Farrier, Feed/Hay, Vaccination): one
    neutral style. Tell them apart by the word, or by a small icon.
- **Files.** `static/css/input.css` (`.badge-*`), charge and health templates.

#### D2. One icon per idea — priority 2, S

- **Problem.** Farrier uses a star. Egg count uses a question mark.
  Vaccination uses different icons on the dashboard, health page and
  horse page.
- **Change.** Make one icon map in `templates/includes/_icons.html`
  (vaccination, farrier, worming, egg count, vet, condition, move, charge).
  Use it in every template. Use a horseshoe for farrier.

#### D3. Finances chart misleads — priority 2, M

- **Problem.**
  - The lines are smoothed. With monthly data this makes a cost "hill"
    from May to July that does not exist.
  - The forecast jumps from £0 to £2,900 with no explanation.
  - The "Forecast" label overlaps the line.
- **Change.**
  1. Use bars for monthly revenue and costs (or set line `tension: 0`).
  2. Start the forecast after a clear divider with the text "Forecast from
     current placements".
  3. Put the label above the plot area, not on it.
- **Files.** `templates/finances.html`, `static/js/dashboard.js` if shared.

#### D4. Dashboard balance — priority 2, M

- **Problem.**
  - "What changed" has no limit. It makes the right column 1,000px taller
    than the Money card, so a large empty area shows on the left.
  - "Next 14 days" shows a full calendar strip when nothing is due.
  - The yard board shows rust dots and "2 locations need attention" but does
    not say why.
- **Change.**
  1. Show the last 8 changes, then "Show all".
  2. When nothing is due, collapse the strip to one line:
     "Nothing due in the next 14 days."
  3. Give each dot a reason in a tooltip and in the card text
     (for example "Fence check overdue", "Over capacity").

#### D5. White footer strip — priority 3, S

- **Problem.** The footer is white on a cream page and starts at the
  sidebar edge. It looks like a separate panel.
- **Change.** Use the page background and a top border, or remove the
  footer from the app pages (keep it on sign-in pages).

### E. Lists on phone

#### E1. Horse list rows — priority 1, M

- **Problem.** At 390px, the three row icons use about 160px. Long names
  wrap to 4 lines. Owner and location truncate ("Top F…").
- **Change.**
  1. Remove the three icons on phone.
  2. Tap the row to open the horse. Long-press or a single ⋯ button opens
     the actions sheet (Move, Photo, Edit).
  3. Let the name use the full width, two lines maximum.
  4. Show owner and location on their own line under the name.
- **Files.** `templates/horses/_horse_row.html`, `_horse_row_flat.html`.

#### E2. View switcher goes off screen — priority 2, S

- **Problem.** "Movements" is cut at the right edge. Nothing shows that the
  row scrolls.
- **Change.** Use icons with short labels that fit (All, Location, Owner,
  Moves), or add a fade on the right edge.

#### E3. Needs action row truncates the horse name — priority 1, S

- **Problem.** On desktop the departure row shows "O… · Left Bot…". The horse
  name is cut to one letter because the two buttons take the space.
- **Change.** Never truncate the horse name. Let the row wrap to two lines.
  Truncate the detail text first.

### F. Forms and settings

#### F1. Add vaccination form — priority 2, M

- **Problem.**
  - "Service" is the first field. "Horse" is second. The horse is the main
    choice.
  - The horse select shows Django's "---------".
  - "Vaccination type" plus "Or type a new vaccination" is two fields for one
    answer, with a long hint to explain them.
  - The vet "+" button is small and not aligned with the select.
  - Cost has no "£" in the field.
- **Change.**
  1. Order: Horse, Vaccination type, Date given, Service and cost, Vet,
     Batch, Notes.
  2. Use "Choose a horse…" as the empty label on every model select. Fix
     this once in the shared form widget.
  3. Make Vaccination type one combobox with "Add a new type…" at the end.
  4. Make the vet "+" a full-height button joined to the select.
  5. Show "£" as a prefix inside money inputs.
  6. Apply 2, 4 and 5 to every health form.
- **Files.** `templates/health/vaccination_form.html`, `health/forms.py`,
  `templates/includes/form_field.html`.

#### F2. Settings is one 5,750px page — priority 3, L

- **Problem.**
  - Business, users, roles, locations, vaccination types, providers,
    services, rates, Xero, dashboard and data are on one page.
  - Business details need a Save button. Dashboard toggles save at once.
    The user cannot tell which rule applies.
- **Change.**
  1. Split into sub-pages with a left menu on desktop and a list on phone:
     Business, Users and roles, Yard setup, Billing, Integrations,
     My preferences, Data.
  2. Use one save rule per page. Toggles save at once and show "Saved".
     Text fields use a sticky Save bar that shows only when something changed.
- **Files.** `templates/settings.html`, `horse_management/urls.py`,
  the settings view.

### G. Locations

#### G1. Location cards say little — priority 2, S

- **Problem.** Each card shows the name, the word "Horses" and a ring. The
  word "Horses" has no value next to it. Cards stop at two per row on a
  1440px screen.
- **Change.** Show the first 3 horse names and "+2 more". Show the next
  task (feed out, fence check) if there is one. Use a 3-column grid at
  `xl:`.

#### G2. Two stacked switchers — priority 3, S

- **Problem.** "Locations / Usage" and "Sort: Most horses first / Default"
  are two segmented controls on two rows.
- **Change.** Keep "Locations / Usage" as the view switch. Make sort a
  small dropdown on the right of the same row.

### H. New function that supports the above

#### H1. Health warnings in the horse list — priority 2, M

- **Problem.** The horse list does not show which horses need action. The
  user must open the dashboard or the health page.
- **Change.** Add a small status column (desktop) or a dot after the name
  (phone): red for overdue, rust for due in 14 days. The tooltip names the
  item. Add a "Needs action" filter chip.

## 4. Order of work

Each phase can ship as its own pull request.

| Phase | Items | Effort | Result |
|---|---|---|---|
| 1. Quick fixes | C1, C2, C4, A5, D1, E3, D5 | 1 to 2 days | Numbers, dates, words and colours are consistent |
| 2. Action hierarchy | Shared ⋯ menu, A1, A2, A3, A4, B2 | 3 to 4 days | One clear primary action per page |
| 3. Phone lists and orientation | E1, E2, B1, B3, G1, G2 | 2 to 3 days | Lists are easy to scan on phone and desktop |
| 4. Data views | C3, C5, C6, D2, D3, D4, H1 | 3 days | Dashboard and finances are accurate and balanced |
| 5. Forms and settings | F1, F2 | 3 to 5 days | Forms are shorter; settings are easy to find |

## 5. How to check each phase

1. Run `python manage.py test` and `node --test 'static/js/tests/*.test.js'`.
2. Run `npm run build:css`.
3. Seed with `tools/qa/seed_qa.py` and `tools/qa/mobile_seed.py`.
4. Take screenshots at 1440px and 390px of: dashboard, horses, horse detail,
   owner detail, locations, invoices, invoice detail, health, charges,
   finances, settings, add vaccination.
5. Check with the keyboard only: every new ⋯ menu opens with Enter, closes
   with Escape, and returns focus to its button.
6. Check each page has exactly one primary (rust) button.
