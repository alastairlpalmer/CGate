# Horse page: UI and UX review

Date: 20 November 2026
Scope: the horse page (`templates/horses/horse_detail.html`), every card
and include on it, the pop-ups it opens, and `HorseDetailView`
(`core/views/horses.py`). The horse list and Fill in records are out of
scope, except where they lead to this page.

Checklist: the SaaS UI pattern library at
github.com/rvailleux/saas-ui-patterns (79 patterns). Section 6 says which
patterns fit this page and which do not.

## Status

| Phase | State |
|---|---|
| 1. Quick fixes | Not started |
| 2. What the page shows first | Not started |
| 3. Actions and feedback | Not started |
| 4. Forms, navigation and history | Not started |
| 5. Consistency and polish | Not started |

## 1. Summary

The page is fast, keyboard-safe and complete. Its main problem is
**order**. A yard manager opens a horse to answer four questions:

1. Is anything due or overdue?
2. Is there anything I must know before I handle it?
3. Where is it, and who owns it?
4. Who is it (identity)?

Today the page answers them in the order 3, 4, 2. Question 1 is not
answered at the top: an overdue vaccination is only in the Timeline, at
the bottom of the left column. On a phone that is nearly 5 screens down. The horse
list marks the same horse "Needs action", but its page gives no warning at
the top.

Do these six first. They give the largest gain for the least work.

| # | Change | Why | Effort |
|---|---|---|---|
| 1 | A **Due** strip at the top: overdue and due-soon items, each with a Record button (A1). | The most urgent facts are now the hardest to find. | M |
| 2 | Move **Notes & conditions** above Identity and Pedigree (A2). | "Bites when rugged" is safety information. It is now the 4th card, after Identity and Pedigree. | S |
| 3 | Fix the **passport badge** so it agrees with the number and the scan (A4). | The header can say "No passport" next to a passport number and a passport scan. | S |
| 4 | **Cap the Timeline** at 10 entries, with "Show older" (A6). | Up to about 110 entries. On a phone, Ownership, Documents and Photos come after all of them. | M |
| 5 | **Hide actions the role cannot use** (B1). | "Log Arrival", "Manage" ownership and "+ Condition" lead to a deny page for some roles. | S |
| 6 | **Accessibility quick fixes** (C1 to C4): headings, filter pill contrast, toasts that screen readers announce, worming overdue in words. | Moderate failures on every horse page. | S |

Effort: S = under half a day. M = 1 to 2 days. L = 3 days or more.
Priority 1 = do first. 2 = next. 3 = when there is time.

## 2. How the review was done

- **Test horses (5):** a horse with full records (2 owners, 8 health
  records, 1 overdue, notes, a condition, documents, photos, extra
  numbers), an empty horse, a mare in foal with 2 foals, a foal, and a
  departed horse.
- **Screens:** desktop 1440px, tablet (iPad Mini, 768px) and phone
  (iPhone 13, 390px), full page.
- **Accessibility scan:** axe-core 4.10 on each horse page.
- **Keyboard:** Tab through the page, open and close the Edit pop-up with
  the keyboard, open the More menu.
- **Speed:** queries and server time with the Django test client.
- **Code:** every template, include and pop-up on the page, read line by
  line. Each finding below names the file and line.
- **Earlier reviews:** `UX_REVIEW_PLAN.md`, `UI_AUDIT.md` and
  `MOBILE_QA_REPORT.md`. Section 8 lists what they asked for that is still
  open on this page.

Measured:

| Item | Result |
|---|---|
| Database queries | 29 (empty horse) to 36 (mare). No repeated query shapes. |
| Server time (local, SQLite) | 37 to 102 ms |
| HTML | 128 to 179 KB; 27 KB after gzip |
| Page height, phone | 4,175 px for the full horse (6.3 screens); 2,528 px for the empty horse |
| Page errors, console errors | None |
| Sideways scroll on a phone | None (wide tables scroll inside their card) |
| axe-core | "heading-order" on every horse page; "region" (notification badge outside a landmark, all pages) |

## 3. Keep these

- **Header actions** follow UX plan A2: Move (primary), Edit, More, and
  Depart last, in red, behind a date step (`horse_detail.html:90–157`).
- **Context-aware header:** a horse with no placement gets "Log Arrival"
  instead of Move.
- **Pop-up sheet** (`static/js/popup.js`): `role="dialog"`, focus kept
  inside, focus to the first field (or the first error), Escape closes,
  focus returns to the trigger, a skeleton while loading, a question
  before unsaved changes are lost, phone Back closes it. Save and Cancel
  stay visible at the bottom on a phone.
- **More menu:** `aria-haspopup`, `aria-expanded`, `role="menu"`, Escape
  and outside click close it.
- **Prev and next stepper** with `rel="prev/next"` and 44px targets.
- **Focus ring** on every control (tested with Tab), and a reduced-motion
  rule.
- **Empty states** for Placement, Ownership, Documents, Photos and the
  Identity "Missing: … Add" line each have a clear next step.
- **Identity card:** only filled rows, chip in groups of three, Copy
  without spaces, duplicate-chip warning with a link.
- **Scanner support:** Enter is blocked in the microchip box, so a scanner
  does not submit half a form.
- **Pedigree tree:** boxes in family reading order with full labels; the
  3-generation tree fits a phone.
- **In Foal card:** gestation progress bar with ARIA values, and the EHV
  schedule in words.
- **Speed:** a fixed number of queries whatever the records.
- **Permissions on money:** rates are hidden from roles without
  `charges.view`.

## 4. Findings and changes

### A. What the page shows first

#### A1. No "Due" summary: overdue items are hidden — priority 1, M

The page never says what is due or overdue. Due dates are only in the
Timeline entries (`horse_detail.html:728–745`), and the Timeline is the
last card of the left column. In the test, the overdue Flu vaccination
was the 8th Timeline entry: at the foot of the desktop page and nearly 5
screens down on a phone.

The app already has what is needed:

- `horse_credential_rows()` (`core/views/horses.py:1599–1641`) builds a
  next-due row for each vaccination type, farrier and worming. Only the
  list's preview pane uses it (`horses/partials/preview.html:186–196`).
- `_health_flags()` (`core/views/horses.py:2226`) gives the same overdue
  and due-soon items as the dashboard and the list's "Needs action" dot.

Change:

1. Add a **Due** strip under the header, from `_health_flags()` for this
   horse, so it agrees with the list and the dashboard.
2. One row for each item: "Flu vaccination · overdue 15 days", "Farrier ·
   due 22 Dec", with a **Record** button that opens the right pop-up.
3. Nothing due: one quiet line, "Nothing due in the next 14 days", or no
   strip at all.
4. Overdue rows use the "needs attention" colour and the word "Overdue",
   not colour alone.

Pattern: Data Storytelling (context, not raw data), Actionable
Notifications (1 or 2 actions on each item).

#### A2. The card order does not match daily work — priority 1, S

Reference data comes before daily information:

- Identity (`horse_detail.html:293`) and Pedigree (`:296`) are above
  "Notes & conditions" (`:299`). In the test, the notes said "Box rest
  until the vet checks again. Turn out alone. Bites when rugged." That
  card was 4th in the left column on desktop and about 3 screens down on a
  phone.
- Identity and Pedigree are also above a mare's In Foal card (`:350`).

Change: order the left column as follows.

1. Current placement
2. Due (A1)
3. Notes & conditions
4. In foal or Foal record (when there is one)
5. Training (when there is one)
6. Timeline (A6)
7. Identity
8. Pedigree. Collapse it to the byline ("by Moonlit Bay out of Fair Lady")
   when the horse is not used for breeding.

The header already shows the registered name and the byline, so moving
Identity and Pedigree down loses nothing at a glance.

#### A3. The same facts appear two or three times — priority 2, M

- Registered name: header (`horse_detail.html:68–70`) and Identity card
  (`_identity_card.html:24–27`).
- Foaling date: header (`:76`) and Identity card (`:52–55`).
- Passport: header badge (`:55–63`) and Identity card (`:44–47`).
- Foal Record repeats sex, colour, dam, sire and chip
  (`partials/foal_record.html:35–47`).
- Training is in the Training card and in the Timeline (`:794–796`).
  Breeding is in Breeding History and in the Timeline (`:764–778`).
- The free-text "Breeding" field (`:308–313`) repeats the Pedigree card
  and has the same name as the mare's Breeding card.

Change: the header keeps the summary (age, sex, colour, location, owner);
the Identity card keeps the numbers. Leave training and breeding out of
the Timeline when their own cards are on the page. Rename the free text
"Lineage notes", or fold it into Pedigree.

#### A4. The page contradicts itself: passport and owner — priority 1, S

**Passport.** The header badge reads only `has_passport`, a tick box that
is ticked by default (`core/models.py:791`) and is not linked to anything
else.

- The full test horse had a passport number, a passport scan in
  Documents and an "ID checked" stamp, and the header said **No
  passport**.
- The empty test horse had no passport number and no scan, and the header
  said **Passport** with a check icon, while its Identity card said
  "Missing: passport number".
- The foal milestone "Add passport" uploads a passport document
  (`partials/foal_record.html:72`, `core/views/photos.py:113–121`), but
  the milestone reads `has_passport` (`health/services.py:350`). Nothing
  sets `has_passport`, so the milestone stays open and "No passport"
  stays in the header.
- In the Edit pop-up, "Has passport" sits next to Height, not next to the
  passport number.

Change:

1. Decide the one meaning: "the passport is held at the yard" (it must
   travel with the horse). Rename the tick box to say that.
2. Badge rules: no badge when the passport is held; "Passport not here"
   (warning) when it is not ticked; "No passport details" only when there
   is no number and no passport document.
3. Saving a passport number or a passport document ticks it (and the
   message says so). The foal milestone counts a passport document as
   done.
4. Put the tick box with the passport number and issuer in the pop-up.

**Owner.** The header shows an owner taken from the placement when there
are no ownership shares (`current_owner`, `core/models.py:1161–1167`),
while the Ownership card on the same page says "No ownership shares set"
(`horse_detail.html:846–852`). Label the fallback "Billed to (from the
stay)", or show the Ownership empty state in the header too.

#### A5. A departed horse does not say when or where it left — priority 2, S

The header shows "Inactive" (`horse_detail.html:50`) and the placement
card says "isn't currently at a location" (`:279–280`). Other places say
"departed" (`:189`, `_identity_card.html:103`). Show "Departed 3 Nov 2026
from Top Field" in the header and the placement card, and use the word
"Departed" everywhere.

The "Active" badge with a pulsing dot (`:45–48`) shows on every horse on
the yard. It draws the eye to the least useful fact. Show a badge only
for a departed horse.

#### A6. The Timeline is long, cut off without saying so, and cannot collapse — priority 1, M

- Each type has its own limit (10 vaccinations, 20 farrier notes, 20
  stable stays, 10 training entries, every breeding record:
  `core/views/horses.py:1252–1327`). The "All" view mixes complete and
  cut-off series, and nothing links to older records.
- All entries render, each with an Alpine `x-show` and transition
  (`horse_detail.html:622–627`), even when a filter hides most of them.
- The empty message (`:801–803`) shows only when there are no entries at
  all. A filter with no entries shows a blank area.

Change: one cap of 10 entries, a **Show older** button (`hx-get`, paged),
and a "See all vaccinations" link for each type. Add a message for an
empty filter. Use an `<ol>`.

#### A7. On a phone, owner and documents come after the whole Timeline — priority 1, M

Below the `lg` width the right column (Record, Ownership, Documents,
Photos) stacks after the left column, which ends with the Timeline
(`horse_detail.html:204–207`, `:810–862`). The full test horse needed
6.3 phone screens. The "All" tile in the phone action row only jumps to
the Record card at the foot (`:178–181`), and the Record tiles are then on
the page twice.

Change:

1. A6 (Timeline cap) solves most of the length.
2. Add a call link to the owner's name in the header (the name is there
   already).
3. Make "All" open the Record tiles in the pop-up sheet, as UX plan A2
   item 3 asked, and hide the Record card on phones.

### B. Actions

#### B1. Actions show for roles that cannot use them — priority 1, S

These have no `feature_access` check, but their views require full
access, so some roles reach a deny page:

- "Log Arrival" in the empty placement card (`horse_detail.html:281`).
  The header copy is checked (`:90`).
- Ownership "Manage" (`:830`) and "Set Ownership" (`:849`). The view is
  `@feature_required('horses')` (`core/views/horses.py:2188`).
- "+ Condition" (`:303`). The view needs `health` at full level
  (`health/views.py:1178–1179`).

The breeding cards are not consistent either: the Breeding card checks
`feature_access.breeding.view` (`:350`), but Breeding History (`:451`),
Foals (`:509`), Foal Record (`:209`) and the Timeline breeding entries
and filter (`:600`) do not.

Change: wrap each action in the same check as its view. Show every
breeding block only with `feature_access.breeding.view`. Add a test that
renders the page for a view-only role and follows each link.

#### B2. More than one primary (rust) button — priority 2, S

UX plan section 5 step 6 asks for exactly one rust button on a page. This
page can show:

- Move or Log Arrival in the header (`horse_detail.html:98–111`)
- a second Log Arrival in the empty placement card (`:281`)
- "Scan result" or "Record foaling" in the In Foal card (`:365`, `:367`)
- "Wean" in the Foal Record (`foal_record.html:19`)
- "Set Ownership" (`:849`)
- "Mark as active" (`:198`)

Change: rust only for the header action. In cards, use secondary or
ghost buttons, and remove the second Log Arrival.

#### B3. One task, several names — priority 2, S

- Covering: "+ Covering" (`:362`, `:435`, `:496`), "Covering" tile, pop-up
  "Record a covering".
- Foaling: "Record foaling" (`:367`), "Foaling" (`:497`, `_quick_actions.html:43`).
- The same foal edit is "Details" (`:542`) and "Edit details"
  (`foal_record.html:21`).
- Ownership: "Manage" (`:830`), "Manage ownership" (`:125`), "Set
  Ownership" (`:850`).
- "See all" (`training/_horse_card.html:14`) and "All records →" (`:459`).
- Card buttons: ghost `btn-xs` in most cards, `btn-secondary btn-sm` for
  "Record training", text links elsewhere.

Change: one verb and noun for each task (Record covering, Record foaling,
Edit foal details, Manage ownership, See all records), as UX plan A5
asks. One button style for every card header.

#### B4. Four delete patterns; only one has Undo — priority 2, M

| What | Now |
|---|---|
| Extra number (identifier) | Remove, then Confirm; hard delete; no Undo (`_identity_card.html:88–92`) |
| Covering, scan | Browser `confirm()` (`breeding_history_rows.html:15, 34`), which skips the app's styled confirm; hard delete |
| Photo | Styled confirm; hard delete (`_photo_grid_card.html:49–50`) |
| Document | Styled confirm **and** an Undo message (two steps for one action) |
| Depart | Date and Confirm; no Undo |

Change: anything that can come back gets a soft delete and an Undo
message (`core/views/_undo.py`), with no confirm step. The rest use the
styled confirm. Add Undo to Depart (reopen the placement).

Pattern: Toast with Undo ("replaces Are you sure? modals").

#### B5. "Scan to check" asks for a scan but has no box to scan into — priority 1, S

When the scan does not match, the form keeps the scan in a hidden field
(`partials/scan_check.html:22`). The text says "Scan again to be sure"
(`:20`), but the only primary button saves the scanned number (`:39`).

Change: in the mismatch state, show a visible, focused scan box with
**Check again** as the primary button. Make "Save the scanned number" a
secondary button.

#### B6. Conditions cannot be edited or closed from the page — priority 2, S

The conditions list has no Edit link (`horse_detail.html:328–342`),
although `condition_update` exists. Resolved conditions look the same as
active ones. Add Edit to each row, put active conditions first, and fold
resolved ones into "2 resolved".

#### B7. "Other charge" disappears after it is saved — priority 2, S

The Record card has an "Other charge" tile (`_quick_actions.html:64–68`).
The view loads `extra_charges` (`core/views/horses.py:1261–1263`), but no
template shows them. Add charges to the Timeline (for roles with
`charges.view`), or a small "Charges" card with "See all".

#### B8. Photo buttons are small and next to each other — priority 2, S

"Use as profile picture" and "Delete photo" are 28px buttons on each
photo (`_photo_grid_card.html:42, 53`). On a phone the photos are about
100px wide. Nothing marks the current profile photo. Every photo also
shows a "Condition" label (its category), which reads like a health
warning.

Change: move both actions into the photo viewer or a More menu on each
photo (44px targets). Mark the profile photo. Show the category only when
it is not the default.

### C. Accessibility

#### C1. Heading levels skip — priority 1, S

The page goes from h1 to h3: every card title is h3 (`horse_detail.html:216,
302, 356, 433, 456, 513, 584, 818, 829`, `_identity_card.html:9`,
`pedigree/_card.html:9`, and others). Inside cards, h5 follows h3
(`:413`, `foal_record.html:60`, `breeding_history_rows.html:9, 25`).
axe-core reports "heading-order" on every horse page.

Change: card titles h2, sub-headings h3. Make the change in the
`.section-title` cards across the app, not only here.

#### C2. Timeline filter pills fail contrast and show state by colour only — priority 1, S

The pill colours are hex values in the template (`horse_detail.html:561–582`):

- selected "Placements": white on `#2E86AB`, 4.11:1
- selected "Vaccinations": white on `#6A8990`, 3.75:1
- selected "Worming" (`#E6E1D1`) against an unselected pill (`#E4EAEB`):
  1.08:1, so you cannot see which pill is selected

All fail AA for 12px text. The buttons have no `aria-pressed`.

Change: one selected style from the tokens (forest with white text,
7.39:1) and `:aria-pressed`. Show a count on each pill, and hide types
with no entries.

#### C3. Worming overdue is shown by colour only — priority 1, S

A worming entry can get the overdue dot and border
(`core/views/horses.py:1206`, `horse_detail.html:631–637`), but its text
never shows the next due date or the word "Overdue" (`:780–786`).
Vaccination and farrier entries show both. Add "Next due: {date} ·
Overdue" as for farrier, and a hidden "Overdue" text in the dot.

#### C4. Messages after a save are not announced and close by themselves — priority 1, S

Every pop-up save, Depart, delete and Undo on this page answers with a
message (toast). The toast area has no `role` or `aria-live`
(`templates/base.html:180`), and the messages close after 5 seconds, or
10 with Undo (`:183`).

Change: `role="status" aria-live="polite"` on the area (`role="alert"`
for errors). Stop the timer while the pointer or focus is on a message.
Keep an Undo message until the user closes it or leaves the page.

Pattern: Toast with Undo ("pause on hover").

#### C5. Focus is lost when a control hides or replaces itself — priority 2, S

- Identifier "Remove" hides itself with `x-show="!sure"`, so focus falls
  to the page, and there is no Cancel (`_identity_card.html:90–91`).
- "Show 3 generations" and "Show 2 generations" are two buttons that swap
  before the response arrives (`pedigree/_card.html:12–15`).
- Pedigree boxes replace their own container (`pedigree/_tree.html:35–40`)
  with no new focus target.

Change: one toggle button with changing text and `aria-pressed`; after a
pedigree swap, focus "← Back to …"; in Remove, focus Confirm and add
Cancel.

#### C6. Focus in the More menu is almost invisible — priority 1, S

`.more-menu-item` replaces the focus ring with a parchment background
(`static/css/input.css:312`): 1.12:1 against white. Add
`focus-visible:ring-2 ring-inset ring-forest`.

#### C7. Text below AA contrast — priority 2, S

- Header foaling date: `text-sage/80` on the header panel, 3.26:1
  (`horse_detail.html:76`). Use `text-sage` (4.79:1).
- Photo category label: 10px white on `bg-charcoal/60` over a light
  photo, about 3.9:1 (`_photo_grid_card.html:35`). Use `bg-charcoal/80`
  and at least `text-xs`.

#### C8. Facts only in tooltips — priority 2, S

`title` tooltips do not show on touch screens or for keyboard users. Only
the tooltip shows the exact date of each Timeline entry (`:702`, the text
says "3 months ago"), the "ID checked" date (`:53`), the header passport
number (`:56`) and the Other charge help (`_quick_actions.html:65`). Show
"12 Mar 2026 · 3 months ago" on health records, and put instructions in
visible help text.

#### C9. Touch targets — priority 2, S

- **Tablet:** `btn-xs` is 24px high from 640px up
  (`input.css:138`). On an iPad the card buttons Edit, Scan to check,
  Show 3 generations and Manage were 24px high (27 controls under 32px on
  the full test horse). Add a `@media (pointer: coarse)` minimum height of
  44px to `.btn-xs`.
- **Phone:** photo buttons 28px (B8), Record tiles 40px (`input.css:283`),
  "Add note" 38px (`foal_notes.html:26`); the foal Dam link
  (`foal_record.html:36`) and the training Lot and Today links have no
  `tap-pad`.

#### C10. Smaller accessibility items — priority 3, S

- A `<nav>` holds the phone row of Record buttons (`horse_detail.html:165`).
  Use `role="group"` with a label. Use `<section aria-labelledby>` for cards.
- Timeline Edit links read "Edit vetvisit record" (`:679–700`,
  `event.type|cut:'_'`). Pass a readable label.
- Copy says "Copied" visually but the `aria-label` hides it
  (`_copy_button.html:6–9`). Add a `role="status"` span.
- Training day letters use `aria-label` on `<li>`
  (`training/_horse_card.html:48`); many screen readers ignore it. Use
  hidden text.
- Help text given with `help=` is not linked by `aria-describedby` for
  UELN, foaling date and height (`core/forms.py:572–600`). Set
  `help_text` on the form fields.

### D. Forms in pop-ups

#### D1. Chip and UELN problems show only after the save — priority 2, M

Wrong length or a chip that is also on another horse shows as a message
after the pop-up closes (`core/views/horses.py:1562`). The microchip box
has no `inputmode` (`core/forms.py:556–559`), and UELN and country use
only the CSS `uppercase` class.

Change: check on blur with `hx-post` to a small check view ("15 digits ✓",
"12 digits: check it", "Also on Rosie"). Show the typed chip in groups of
three under the box. Add `inputmode="numeric"` to the chip box (a scanner
still types into it) and `autocapitalize="characters"` to UELN and
country.

Pattern: Inline Validation with Debounce, Field Masking (format for
display only, normalise on the server).

#### D2. The quick edit has an "Active" tick box that repeats Depart — priority 3, S

The tick box (`quick_edit_form.html:50`) is blocked while a placement is
open, and its help text says "Untick only if the horse has left for
good". Remove it from the quick form: departure goes through Depart.

#### D3. Error boxes are hand-written — priority 3, S

The pop-ups write their own error panels (`quick_edit_form.html:14`,
`identifier_form.html:13`, `pedigree/partials/edit_form.html:11`,
`scan_check.html:11`) instead of the `.alert-*` classes (UI audit 3.11).

### E. Navigation and history

#### E1. Back and prev/next ignore the list the user came from — priority 2, M

"All Horses" always goes to the unfiltered list (`horse_detail.html:13`).
Prev and next step A to Z over all active (or all departed) horses
(`core/views/horses.py:1226–1233`). A user who opened the horse from a
location group or from "Needs action" leaves that set at the first step.

Change: carry the list's query string (`?from=…`), step inside that set,
and go back to it. Add ← and → keys, as the split view already has
(`static/js/split_view.js:421–425`).

Pattern: Persistent and URL-encoded Filters ("preserve filters when the
user goes to a detail page and back"); Dynamic Breadcrumb.

#### E2. The horse's change history is recorded but not shown — priority 2, S

Horse, extra numbers and pedigree changes are kept by
django-simple-history (`core/models.py:822`, `core/audit.py`). The owner
and invoice pages show a collapsed History panel
(`includes/history_panel.html`); the horse page does not. A chip that a
scan replaced leaves only an "Old microchip" row.

Change: `history_entries = audit.history_for(horse)` in the view, and the
collapsed History panel at the foot of the page.

Pattern: Audit Trail (who, what, when; show the change, not only that
something changed).

### F. Consistency

#### F1. Title Case and sentence case are mixed — priority 3, S

"Current Placement", "Expected Departure", "Breeding History", "Foal
Record", "EHV Vaccination Schedule", "Vet Visits", "Log Arrival", "Set
Ownership", "Save Changes", "All Horses" next to "Notes & conditions",
"Record foaling", "Vet visit". Use sentence case everywhere.

#### F2. Uppercase small headings — priority 3, S

`input.css:221–223` keeps uppercase letter-spacing for table column
headers. These ignore it: `horse_detail.html:413`, `:646–673` (Timeline
type labels), `foal_record.html:60`, `breeding_history_rows.html:9, 25`,
`foal_notes.html:8`, `pedigree/_diagram.html:22, 31, 34`. Use `.dt-label`.

#### F3. Money, dates and durations skip the shared filters — priority 2, S

- Timeline rate `&pound;{{ … }}/day` (`horse_detail.html:711`); the card
  above uses `|gbp` (`:256`).
- The foal milestone "Due by 06 May 2027" (`health/services.py:352`) next
  to "Done 6 May 2027" (`foal_record.html:67`).
- Time on site uses `timesince|cut:","` (`:223`) with the title "Days since
  arrival", but shows months. Use `stay_length` (`ui_extras.py:73`).
- Foal note dates in the code font (`foal_notes.html:33`).

#### F4. Red for things that are not errors — priority 2, S

Active condition (`horse_detail.html:335`) and "Lost" (`breeding_status_badge.html:9`)
use `badge-danger`; a high egg count uses `text-error-red` (`:789`). Use
the "needs attention" colour for active and high, and neutral for Lost
(UX plan D1).

#### F5. One breeding status, two labels — priority 3, S

The shared badge says "In foal" and "Covered"
(`breeding_status_badge.html:2–11`); the Timeline says "Confirmed In-Foal"
(`horse_detail.html:768–776`). Use the shared badge in the Timeline.

#### F6. Inline icons instead of the icon sprite — priority 3, S

Location pin (`:237`, `:274–277`, `:642–645`), Depart (`:136`, the app's
departure icon is `#i-gate`), trash (`_documents_card.html:39–41`,
`_photo_grid_card.html:55–57`; `#i-trash` exists), condition heart
(`_quick_actions.html:27–31`).

#### F7. Wording — priority 3, S

- The stranded-horse banner says "use **Depart**" (`:193`); Depart is now
  "Depart…" in More.
- "Rust dot: the next one is overdue." (`:613`) names a colour and shows
  even when nothing is overdue. Show it only when needed, in words.
- "14d" and "HB" (`:485–486`): write them out.
- Notes, lineage text and markings lose their line breaks (`:311`, `:317`,
  `_identity_card.html:62`). Use `linebreaksbr` as the foal notes do.
- The photo empty state says "with Quick Add" (`_photo_grid_card.html:69`),
  a button that is not on this page.

#### F8. Empty states do not use the shared include — priority 3, S

`includes/empty_state.html` exists (UI audit 3.8). The Timeline empty
state has no action (`:802`); Placement and Ownership each use their own
layout and a rust button. Use the shared include with one secondary
action.

### G. Speed

#### G1. The Training card runs extra queries for each row — priority 2, S

Rows read `rider_label` and `venue.name`, but the query joins only
`work_type` (`training/horse.py:41`). Up to 2 extra queries for each
row. Use `select_related('work_type', 'rider', 'venue')`.

#### G2. The header photo loads late — priority 3, S

The header photo is `loading="lazy"` (`_horse_avatar.html:34`), but it is
above the fold. Use `loading="eager" fetchpriority="high"` for the large
size.

#### G3. Each pop-up save reloads the whole page — priority 3, M

After a save, the page fetches and swaps all of `#main-content`
(`popup.js:325–345`). With A6 the page is smaller, so this matters less.
Later, refresh only the changed card.

## 5. Proposed layout

Desktop (two columns, as now):

```
┌ ← All horses (Top Field)                                 Rosie 23 → ┐
│ [photo] Avicii                              [Move] [Edit] [More ▾]  │
│ Karma Avicii (GB) · 7yo bay gelding · by X out of Y                 │
│ Top Field · 7 weeks · Mrs Sophie Macpherson (60%) ☎                │
├─────────────────────────────────────────────┬───────────────────────┤
│ DUE  Flu vaccination · overdue 15 days [Record]│ Record              │
│      Worming · due in 20 days          [Record]│ [Vaccination][Farrier]│
├─────────────────────────────────────────────┤ [Worming] [Vet visit] │
│ Current placement                           │ …                     │
│ Notes & conditions  (Box rest… Bites when   ├───────────────────────┤
│                      rugged · Sweet itch)   │ Ownership             │
│ Timeline (10 entries)        [Show older]   │ Documents             │
│ Identity                                    │ Photos                │
│ Pedigree (byline; open for the tree)        │ History (collapsed)   │
└─────────────────────────────────────────────┴───────────────────────┘
```

Phone (one column):

```
Header, owner with call link
[Vaccination] [Farrier] [Vet visit] [More → sheet]
Due
Current placement
Notes & conditions
Timeline (10) · Show older
Identity
Pedigree
Ownership · Documents · Photos
```

## 6. How the SaaS UI patterns fit this page

| Pattern | Fit | Where |
|---|---|---|
| Actionable empty states | Yes. Most cards have one; Timeline and Photos need one. | F8, F7 |
| Data storytelling | Yes. "Overdue 15 days", not only dates. | A1 |
| Actionable notifications | Yes. One Record button on each due item. | A1 |
| Toast with Undo | Yes. Only documents have it today. Messages must be announced and pause on hover. | B4, C4 |
| Inline validation, field masking | Yes, for chip and UELN. | D1 |
| Audit trail | Yes. The data exists; the page does not show it. | E2 |
| Persistent URL filters, breadcrumb | Yes. Keep the list's filter in Back and prev/next. | E1 |
| Keyboard shortcuts | Small: ← and → for prev and next. "/" and Ctrl+K search exist. | E1 |
| Skeleton screens | Already in the pop-up. Add one to the pedigree swap. | C5 |
| Morphing button states | Already global (`.btn-loading`). Keep. | — |
| Motion under 300 ms | Already met, with reduced motion. Keep. | — |
| Contextual right panel, split view | Already on the horse list (preview pane). On this page the right column can hold Due and Owner on desktop. | A1 |
| Optimistic UI | Only for small, safe toggles (training "Mark done", profile photo). Not for health records. | — |
| Data masking | Only if owner phone numbers go on this page: show them only to roles with `owners.view`. | A7 |
| Floating action button | No. The Record tiles and the phone action row do this job. | — |
| AI patterns (assistant panel, streaming, confidence) | Not now. They fit Phase 2 of the identity plan (read passports with AI): confidence for each read value, and a diff view (accept or reject) on the review screen. | Roadmap |

## 7. Plan

Each phase is one PR, with tests and phone and desktop screenshots.

| Phase | Items | Effort |
|---|---|---|
| 1. Quick fixes | B1, A4 (passport and owner), C1, C2, C3, C4, C6, B5, G1, G2 | M (1 to 2 days) |
| 2. What the page shows first | A1, A2, A6, A7, A5 | M to L |
| 3. Actions and feedback | B2, B3, B4, B6, B7, B8 | M |
| 4. Forms, navigation and history | D1, E1, E2, C5, C8, C9 | M |
| 5. Consistency and polish | A3, C7, C10, D2, D3, F1 to F8, G3 | M |

## 8. Earlier recommendations still open on this page

| Document | Item | State |
|---|---|---|
| UX_REVIEW_PLAN | A2 item 3: phone "More" opens the Record tiles in the sheet | Part done: "All" jumps to the foot (A7) |
| UX_REVIEW_PLAN | A2 item 2: one way to add a photo | Not done: Record tile, Photos "+ Add", foal milestone |
| UX_REVIEW_PLAN | A5: verb and noun on every button | Not done here (B3) |
| UX_REVIEW_PLAN | C1, C2 item 3, C3: money filter, date filter, code font only for codes | Not done here (F3) |
| UX_REVIEW_PLAN | D1, D2: red only for overdue and errors; one icon set | Not done here (F4, F6) |
| UX_REVIEW_PLAN | Section 5 step 6: one primary button | Not met (B2) |
| UI_AUDIT | 3.6: header strip with location and days on site | Not done (section 5 layout) |
| UI_AUDIT | 3.8, 3.11, 3.12: shared empty state, alert classes, quieter table actions | Not done here (F8, D3, B3) |
| MOBILE_QA_REPORT | T2.2: card layout for wide tables | Not done for Breeding History and Foals (7 columns; Actions off screen) |
| MOBILE_QA_REPORT | T1.1, T3.2: 44px controls on a phone | Not met in places (C9) |
