# Dashboard redesign plan

Status: decisions taken on 4 September 2026 (section 0); the build is on
branch `claude/dashboard-redesign-overhaul-lzy7ei`.

This plan comes from a read of the whole codebase: models, services, views,
templates, the role suite, the reminder tasks, the tests, and the earlier
review documents (`CODEBASE_REVIEW.md`, `UI_AUDIT.md`, `FULL_QA_REPORT.md`,
`MOBILE_QA_REPORT.md`, `POPUP_EDIT_PLAN.md`, `CUTOVER.md`).

Section 10 lists the questions that were put to the yard; section 0 records
the answers and what changed because of them.

---

## 0. Decisions (4 September 2026)

Answers from the yard, and the effect on the plan:

| Question | Answer | Effect |
|---|---|---|
| Tiers of user | One tier, keep it simple | No lenses. One dashboard for everyone; zones still hide by feature access and per-user switches stay. |
| Who uses it, on what | Operations and management are the same people, on both phone and desktop | Desktop and phone get equal care. |
| Morning walk of the yard | No | The "Today's round" phone mode is dropped. |
| Farrier and vet | Booked and ad hoc; the farrier does several in one visit, sometimes one; the vet is mostly ad hoc, sometimes booked | Items due the same day for two or more horses become one row with "Record for N". No assumption of fixed rounds. |
| Sites | Somerford and Colgate, staff work across both | Site switch, default All, remembered per user. |
| Monthly invoices | Charlie runs them; to be picked up separately | Money zone stays compact: drafts to review, outstanding with aged split, received, unbilled, send and Xero state. No cycle stepper yet. |
| Daily run-rate | Not daily | Dropped from the dashboard. |
| Resting Locations year totals | Your call | Off the home page. The Yard board shows days rested so far; year totals stay on Locations, Land use. |
| Breeding | Seasonal block | "In foal" renders only when a mare has a confirmed pregnancy. |
| Feed stock | Not tracked now, could be | Low-stock rows appear in the inbox only when stock data exists. |
| Per-user or per-role layout | Your call | Per-user switches, as today, re-keyed to the six zones. |
| Looks | Follow the UI audit; make the sections stand out | State title, domain icon sprite, coat-colour dots, staggered entrance, the yard's name in the sidebar, hover-quiet actions. |

What was built, in order: the data layer (`core/dashboard/`), the six
zones, the health bulk form's pop-up mode for "Record for N", the Health
overview switched to the shared collectors, the visual items above, and
the tests.

---

## 1. What the dashboard does today

Files: `core/views/dashboard.py`, `core/dashboard_widgets.py`,
`templates/dashboard.html`, `templates/partials/dashboard/*.html`,
`core/models.py` (`DashboardPreference`).

- 14 widgets in three fixed groups: 4 KPI tiles, 6 list cards, 4 lazy-loaded
  health alert cards. Each user can switch widgets on or off in Settings.
  Widgets tied to a feature area the user's role hides are dropped.
- The page title is a greeting. The subtitle shows the date and
  "N items need attention".
- The list cards are: Pending Departures (off by default), Recent Activity,
  Vaccinations Due (30 days), Farrier Due (14 days), Outstanding Invoices
  (table), Resting Locations (rest days this year).
- Health alerts load after the page: Upcoming Departures (7 days), EHV due,
  High Egg Counts, Vet Follow-ups (30 days).
- Actions on the page: Record (vaccination, farrier), Record Payment,
  Mark Paid, Confirm departure.

## 2. What is wrong today

Each finding names the file so you can check it.

1. **The layout ignores urgency.** The list grid is `lg:grid-cols-2` with
   `grid-flow-row-dense` (`templates/dashboard.html`). Cards fall where they
   fit. In the current screenshot the only overdue invoice (INV00002, due
   14 Apr 2026, 143 days late) is the last thing on the page, under Resting
   Locations. The most urgent item is below the fold.
2. **The attention count is not honest.** `attention_count` is overdue
   vaccinations plus overdue invoices only (`core/views/dashboard.py`,
   "Header summary"). Farrier, vet follow-ups, documents, egg counts and
   departures do not count. Both inputs are lists already capped at 10 rows,
   so the count can never exceed 20. The screenshot shows "1 item needs
   attention" while two farrier visits are 14 days overdue on the same page.
3. **Recent Activity is not recent activity.** The view takes the 3 newest
   rows of each of 6 record types and sorts them (`core/views/dashboard.py`,
   "Recent Activity Timeline"). Rare types surface old rows. The screenshot
   shows "Hay to The Big Field, 5 months ago" as recent.
4. **Resting Locations counts days that have not happened.** An open usage
   period has `end_date = None`, and `get_effective_dates_in_period` clips it
   to the period end, which the widget sets to 31 December
   (`core/models.py`, `LocationUsagePeriod.get_effective_dates_in_period`;
   `core/views/dashboard.py`, "Field rest this year"). In the screenshot
   Sandhills shows 291 rested + 74 horses = 365. On 4 September the year has
   only 247 days. The numbers are labelled "RESTED" and "HORSES", which reads
   as horse counts. `UI_AUDIT.md` §3.9 already flags the labels.
5. **KPI tiles waste the top of the page.** "Vaccinations Due 0" holds the
   second tile. "Horses On Site" has an empty meta line
   (`templates/partials/dashboard/kpi_total_horses.html`).
6. **Farrier items are per horse, but the farrier works in rounds.** Two
   horses due 21 August and two due 14 September appear as four rows with
   four Record buttons. There is no way to record one visit for several
   horses from the dashboard, although a bulk health form exists
   (`health/views.py`, `bulk_health_form`).
7. **There is no sense of place.** The yard has several sites
   (`Location.site`; the screenshot shows Somerford and Colgate; the importer
   in `import_data.py` maps seven site names). Nothing on the dashboard
   groups by site or location. A user cannot narrow the page to the site
   they are standing on.
8. **There is no sense of time.** Nothing shows what is due today, this
   week, or when the next foal, vet visit or departure is expected.
   `CODEBASE_REVIEW.md` item #7 (calendar view) is still open.
9. **The monthly billing cycle is invisible.** Draft invoices are generated
   on the 1st of each month (`invoicing/tasks.py`,
   `generate_monthly_draft_invoices`). Drafts, sends, send errors
   (`Invoice.send_error`) and Xero sync errors (`XeroInvoiceSync`) never
   appear on the dashboard.
10. **Valuable data is never shown.** Document expiry (`Document.expiry_date`,
    only emailed), active medical conditions (`MedicalCondition.status`), low
    feed stock (computed in `billing/views.py`, `feed_dashboard`), foaling
    due dates (`BreedingRecord.date_foal_due`), passport gaps
    (`Horse.has_passport`), aged debt (`StatementService.aged_debtors`).
11. **Same names, different numbers.** The dashboard egg-count widget has no
    date bound; the Health overview uses 90 days. The dashboard vet
    follow-ups exclude overdue; the Health overview includes them. The two
    pages disagree for the same label (`core/views/dashboard.py`,
    `dashboard_health_alerts`; `health/views.py`, `health_dashboard`).
12. **Personalisation is shallow.** `DashboardPreference.layout` stores an
    `order` per widget, but no UI changes it. There is no per-role default,
    no remembered site, no "since your last visit".

## 3. Who the dashboard serves

There are no commercial plan tiers in the code. Access is a per-role matrix of
14 feature areas at three levels: hidden, view, full (`core/features.py`,
`core/permissions.py`). Two roles ship: Administrator (everything) and Viewer
(read across the yard, write on health and breeding). Admins can create any
other role. Users are not scoped to sites.

The plan therefore defines four **lenses**. A lens is the default dashboard
for a shape of role. The app derives the lens from the user's access map, so
no new tier concept is needed.

| Lens | Role shape (from the access map) | First question when they open the app |
|---|---|---|
| **Manager** | full on horses, health and invoices | What needs doing, and what does the month look like? |
| **Yard** | full on health, view or full on horses, finance hidden | Which horses need something today, and where are they? |
| **Office** | full on invoices or charges, horses hidden or view | Where is the billing cycle, who owes what, what failed? |
| **Read-only** | view on most things, full on nothing | What is the state of the yard? (no action buttons) |

The test suite already uses these shapes: "Bookkeeper", "Groom", "Viewer"
(`core/tests/test_feature_enforcement.py`, `core/tests/test_role_suite_ui.py`).

## 4. The redesign

The page stops being a grid of equal cards. It becomes six zones. Each zone
answers one question. Zones render only for lenses that need them, and every
zone stays switchable per user.

### Zone A — Headline: the yard's state

- The `h1` is the state, not a greeting: "3 things need doing" or
  "All clear across 2 sites". The greeting and date move to the subtitle.
  (`UI_AUDIT.md` §3.9 proposes the same.)
- Under the title, one chip per item type with a count: Overdue 3 ·
  Due this week 6 · Departures 1 · Money 1. A chip filters Zone B.
- A **site switch** on the right: All sites · Somerford · Colgate. The choice
  is remembered per user and narrows every zone. Sites come from distinct
  `Location.site` values, as `core/forms.py` `get_site_choices` does today.
- The count is computed from the full item set in Zone B, not from capped
  lists.

### Zone B — Needs action: one inbox, not eight cards

One list of **attention items** from every domain, sorted by severity then
age. Each row: type icon, horse (or owner, or location), what, due label,
one action button. The action opens the existing popup sheet
(`static/js/popup.js`, `PopupFormMixin`).

| Item type | Source | Rule | Action |
|---|---|---|---|
| Vaccination | `current_vaccinations` | overdue, or due within `reminder_days_before` | Record vaccination |
| Farrier | `current_farrier_visits` | overdue, or due within 14 days | Record visit (single) or Record round (see Zone C) |
| Vet follow-up | `VetVisit.follow_up_date` | overdue, or within 30 days | New visit |
| High egg count | `WormEggCount` | count > 200 in last 90 days, latest per horse | Record worming |
| EHV | `BreedingRecord.ehv_vaccination_dates` | inside the −14/+7 day window the email task uses | Record vaccination |
| Foal due | `BreedingRecord.date_foal_due` | within 30 days | Open record |
| Document expiry | `Document.expiry_date` | expired, or within 30 days | Open horse or owner |
| Departure to confirm | `Placement` closed, horse active, no open placement | as today's Pending Departures | Confirm / Cancel |
| Expected departure | `Placement.expected_departure` | within 7 days | Open horse |
| Overdue invoice | `Invoice` sent or overdue, `due_date < today` | balance from payments | Record payment · Mark paid · Send reminder |
| Draft invoices | `Invoice.status = draft` | any | Review drafts |
| Send failed | `Invoice.send_error` | not empty | Open invoice |
| Xero | `XeroInvoiceSync.sync_status = error`, connection refresh expiry | any | Open Xero settings |
| Low feed stock | balance per (site, feed type, unit) | ≤ 0 red, < 10 amber, as the feed page does | Log delivery |

Rules:

- Group rows by horse when one horse has several items, so "Punk Rock:
  farrier 14 days overdue · flu due in 6 days" is one row with two actions.
- Show the first 8 rows. "Show all N" expands in place.
- When the list is empty, show one line: "Nothing needs doing." Do not
  render an empty card.
- Read-only lens: same list, no action buttons.
- **One source of truth.** The item collectors live in a new module
  (`core/attention.py`) and the Health overview reuses them for Action
  Required and Coming Up. Finding 11 disappears. The push-notification idea
  in `CODEBASE_REVIEW.md` can use the same module later.

### Zone C — Next 14 days: time as the axis

- A 14-day strip that starts today. Fourteen days is the farrier window and
  fits inside the 30-day vaccination window, so the strip shows the bookings
  that are close. Each day shows small counts by type: due vaccinations,
  farrier due, vet follow-ups, expected departures, foals due, EHV due,
  invoice due dates. Tap a day to list its items.
- **Rounds.** Farrier items due within 14 days grouped by site and due week:
  "Somerford — 4 horses due by 14 Sep". One button, "Record round", opens the
  existing bulk health form with those horses preselected
  (`bulk_health_form` with `action_type=farrier`). The same pattern fits vet
  visits later.
- This is a first, cheap step toward roadmap item #7 (calendar). The full
  month grid and iCal feed stay a separate project.

### Zone D — Yard board: place as the axis

- One band per site. Inside, one tile per active location: name, occupancy
  ring (`templates/horses/_capacity_ring.html`, already shared with the
  Locations page), land-use badge (`_usage_badge.html`) for rested, hay,
  mixed and arable. A small marker on a tile means a horse there has an
  attention item.
- Rested locations show how long they have rested so far: "Rested since
  14 Apr · 143 days". The year totals stay on the Locations Land use tab.
  This fixes finding 4 by design.
- Tap a tile: location detail. Tap the site name: horse list grouped by
  that site.
- Data: `Location.objects.active()` with annotated open-placement counts, as
  `core/views/finances.py` does for the capacity chart, plus the open
  `LocationUsagePeriod` per location. Two queries.
- On phones the board collapses to one row per site: horses / capacity,
  locations resting, items due.

### Zone E — Money this month: the billing cycle

For Manager and Office lenses only.

- **Cycle stepper** for the period that was invoiced on the 1st:
  drafts → sent → paid, with counts and totals, and the next action as a
  button: "Review 14 drafts", "Send 9", "Chase 1 overdue".
  Data: `Invoice` grouped by status for the latest `period_end`.
- **Three figures** that matter to a livery:
  - Daily run-rate: the sum of `rate_type.daily_rate` over open placements,
    shown as "£/day" and "per month at today's occupancy". The Finances page
    already computes this for its forecast.
  - Outstanding, net of part-payments, with the aged-debt split from
    `StatementService.aged_debtors` as a thin segmented bar (current,
    1–30, 31–60, 61–90, 90+).
  - Unbilled extras (`ExtraCharge.unbilled_total()`), with a button to the
    charge list.
- Status pills: Xero connected / not connected / N sync errors; N send
  failures.
- Links: Finances overview for charts. Charts do not return to the
  dashboard.

### Zone F — What changed: a real log

- One chronological log grouped by day: Today, Yesterday, then dates.
  Sources: movements (arrivals, moves, departures from `Placement`), health
  records, charges, payments, documents, photos, feed. One union query
  ordered by date, then the last 20. Not 3 per type.
- Each row names who or what, and links to the record. Movements use the
  vocabulary of the Movements tab.
- Optional "New since your last visit" marker. Needs one new field,
  `DashboardPreference.last_seen_at`.

### Removed or moved

| Today | Where it goes |
|---|---|
| 4 KPI tiles | Zone A chips and Zone E figures. A KPI that is 0 does not render as a tile. |
| Vaccinations Due, Farrier Due, Vet Follow-ups, EHV, Egg Counts, Pending Departures, Upcoming Departures, Outstanding Invoices | Zone B rows |
| Resting Locations (year totals) | Zone D badge with days rested so far; year totals stay on Locations → Land use |
| Recent Activity | Zone F |

## 5. Default zones per lens

| Zone | Manager | Yard | Office | Read-only |
|---|---|---|---|---|
| A Headline | yes | yes | yes (money chips) | yes |
| B Needs action | all types | health, movements, documents, feed | money types | all visible types, no buttons |
| C Next 14 days | yes | yes, with Rounds | invoice due dates only | yes |
| D Yard board | yes | yes, first on phones | no | yes |
| E Money | yes, compact | no | yes, full width | if finances visible |
| F What changed | yes | movements and health | money rows | yes |

A widget never renders for a feature the role hides. That rule exists today
(`DashboardPreference.visible_ordered_keys_by_group`) and stays.

## 6. Phone behaviour

`MOBILE_QA_REPORT.md` treats 390 px as the primary phone target and the app
already has a bottom tab bar, popup sheet and 44 px targets. The dashboard
must keep that standard.

- Order on a phone: Headline → Needs action → Rounds → Yard board (site
  rows) → Money (two tiles + stepper) → What changed.
- The Yard lens gets a **Today's round** mode: Zone B grouped by location in
  site order, so a person walks the yard once. One toggle, remembered.
- The 14-day strip scrolls sideways with today in view.
- Every action stays in the popup sheet. No full-page forms from the
  dashboard on a phone.

## 7. Technical approach

- **New package** `core/dashboard/`:
  `attention.py` (item collectors and the `AttentionItem` dataclass),
  `board.py` (sites, locations, occupancy, rest), `money.py` (cycle state,
  run-rate, aged debt), `activity.py` (union log), `week.py` (day buckets).
  Each function takes `(user, site=None, today=None)` and returns plain
  data. Views stay thin. Tests hit the functions directly.
- **Widget registry** (`core/dashboard_widgets.py`) keeps its shape. Keys
  change to the zones and their sub-widgets. `resolved_layout()` already
  ignores stale keys, so stored preferences survive the re-key. Add
  `default_visible(lens)` so new users get the lens default.
- **Lens** is derived in `core/dashboard/lens.py` from `access_map(user)`.
  No new model. A later phase may add an explicit override per role.
- **`DashboardPreference`** gains `site` (char, blank) and `last_seen_at`
  (nullable datetime). One migration.
- **Loading.** Zones A and B render in the first response. Zones C to F load
  with `hx-trigger="load"` behind skeletons, the pattern the health alerts
  use today. Each is its own endpoint under `/_partials/`.
- **Queries.** Target: Zones A + B in at most 12 queries at any yard size.
  Add a `core/tests/test_dashboard_queries.py` in the style of
  `core/tests/test_query_counts.py`.
- **Roles.** Every partial and every action checks `feature_access` as the
  outstanding-invoices table does today. Every new endpoint carries
  `@feature_required('dashboard')` and the item collectors skip domains the
  role hides.
- **Health overview** switches to `core/dashboard/attention.py` for its two
  lists, so both pages agree.
- **CSS.** New classes in `static/css/input.css` (`zone`, `attn-row`,
  `week-strip`, `board-tile`, `cycle-step`) and a rebuild of
  `static/css/styles.css` with `npm run build:css`. Tokens stay as they are.
- **Tests to update:** `core/tests/test_dashboard_preferences.py` (widget
  keys, all-caught-up, header), `health/test_overdue_latest.py`,
  `core/tests/test_feature_enforcement.py` (widget visibility per role),
  `core/tests/test_htmx_lint.py`, `core/tests/test_vocabulary_and_nav.py`
  (the "field" lint; use "location").

## 8. Phases

Sizes follow the S/M/L scale `CODEBASE_REVIEW.md` uses. They are relative,
not hours.

| Phase | Scope | Size | Depends on |
|---|---|---|---|
| **0 Fix the current page** | See section 9. Ships value the same day, no redesign risk. | S | nothing |
| **1 Headline + Needs action** | `attention.py`, Zone A, Zone B, honest count, popup actions, Health overview reuse. Removes eight list widgets. | M | answers to Q1, Q2 |
| **2 Yard board + site switch** | `board.py`, Zone D, remembered site, site filter across zones. | M | Phase 1, answers to Q3 |
| **3 Money + What changed** | `money.py`, `activity.py`, Zones E and F, drafts and Xero states. | M | Phase 1, answers to Q4 |
| **4 Time + Rounds** | `week.py`, Zone C, Record round via bulk form. | M | Phase 1, answers to Q2 |
| **5 Lenses + phone round mode** | lens defaults, reorder UI, last-seen marker, Today's round toggle. | S/M | Phases 1–4 |
| **6 Visual identity** | `UI_AUDIT.md` items that touch the dashboard: domain icon sprite, coat-colour dots, staggered card entrance, business name in the sidebar. | S/M | any time after Phase 1 |

Risks:

- Phase 1 changes what the tests call widgets. Expect a day of test updates.
- The union activity log needs care on PostgreSQL vs SQLite (`UNION` with
  mixed column types). Use `values()` with explicit casts, or a Python merge
  over six small ordered queries. Measure both.
- "Record round" relies on the bulk health form accepting preselected
  horse ids from a GET link. Today the Alpine `bulkActions` component builds
  that form. A small view change is needed.

## 9. Phase 0: fixes to ship now

1. Title becomes the state ("N things need doing" / "All clear"); greeting
   and date become the subtitle.
2. Attention count covers overdue vaccinations, farrier, vet follow-ups,
   invoices, expired documents and departures to confirm, computed from
   counts, not from capped lists.
3. Resting Locations: clip periods at today, not 31 December; label the
   numbers "days rested" and "days with horses"; add "so far this year".
4. KPI tiles: fill the "Horses On Site" meta line with the site split
   ("across 2 sites"); hide a KPI whose value is 0 and show its label as a
   chip in the header instead.
5. Order the list grid by urgency: overdue first, then due, then reports.
6. Recent Activity: one merged query ordered by date, last 12, grouped by day.
7. Farrier Due: group rows that share a due date under one heading.

## 10. Questions

Grouped. The first group changes the plan most.

**A. People and tiers**

1. When you say "tiers of user", do you mean the roles in the app
   (Administrator, Viewer, and roles you create such as Bookkeeper or
   Groom), or commercial plans for other yards? The code has no plan tiers.
   If plans are coming, what are they and what does each include?
2. Who opens the app each day, and on what? The docs suggest yard staff on
   phones and the office on a desktop, but no document confirms it. How
   many people, and which roles do they hold today?

**B. Rhythm of the yard**

3. Is there a morning routine where someone checks every horse or every
   site? If yes, would a "Today's round" list grouped by location match how
   you walk the yard?
4. How does the farrier work: booked rounds per site on a set day, or one
   horse at a time? Same question for the vet.
5. Which sites are live? The screenshot shows Somerford and Colgate. The
   importer names seven. Do staff work at one site, or move between them?

**C. Money**

6. Who runs the monthly invoice cycle, and when? Is
   `auto_generate_invoices` switched on in production, so drafts appear on
   the 1st? Is Xero connected in production?
7. Is the daily run-rate (horses × daily rate) a number you want to see
   every day, or only at month end?

**D. Content and priorities**

8. Which current widgets do you use? In particular: are the Resting
   Locations year totals useful on the home page, or do they belong on the
   Locations page only?
9. Is breeding a major part of the operation? Should foaling and EHV items
   sit in the main list, or in a seasonal block that appears only when a
   mare is in foal?
10. Is feed stock tracked in practice? The screenshot shows one feed-out five
    months ago.
11. Do you want the dashboard to stay switchable per user, or should each
    role get a fixed layout that an admin sets?
12. Looks: do you want to stay with the current palette and type (forest,
    sage, saddle; DM Sans and Source Sans 3), or is the visual identity open
    as well? `UI_AUDIT.md` argues for domain icons and more of the saddle
    accent.

## 11. Assumptions until the questions are answered

- "Tiers" means role shapes, not commercial plans.
- Yard staff use phones; the office uses a desktop.
- The farrier and vet visit several horses at a site in one go.
- All active sites matter equally, so the site switch defaults to All.
- The monthly cycle is drafts on the 1st, review and send in the first
  week, chase after 30 days.
- The palette and typography stay. The redesign spends its effort on
  layout, information and actions.

## 12. Gaps I could not close from the code

- The real horse, owner, location and site counts. The documents give
  estimates only ("~150 owners", 31 horses on site in the screenshot).
- Whether the reminder emails go to real inboxes in production
  (`CUTOVER.md` warns the console backend is the default until SMTP is set).
- Whether anyone uses the stored widget `order`. No UI writes it.
- Why reminders run Monday to Friday only (`REMINDER_DAYS_OF_WEEK`).
- Worming has no due date in the data model, so the dashboard cannot show
  "worming due". Adding one is a small model change if wanted.
