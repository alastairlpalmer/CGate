# Phone app: review and plan

Date: 22 November 2026
Scope: the main Cgate app on a phone or a tablet. This is the layout with
the tab bar, on screens narrower than 1024 px. C-vet and the farrier page
have their own plans (`C_VET_PLAN.md`, `FARRIER_PAGE_PLAN.md`). They are
out of scope, but section E uses the C-vet rules.

Goal: make the phone app fast, clear and safe to use in a field. Then make
it ready to launch as an app.

## Status

| Phase | State |
|---|---|
| 1. Nothing breaks, nothing is lost | Done in this branch: A1 to A7 (see the notes below the table) |
| 2. Faster taps | Not started |
| 3. Key information first | Not started |
| 4. Installed app | Not started |
| 5. Push notifications | Not started |
| 6. App stores | Later: the installed web app comes first (section 9, decision 6) |

Phase 1 notes:

- A1: `SignInRedirectMiddleware` answers an htmx request with
  `HX-Redirect` when the view sends it to the sign-in page. After
  sign-in, a tap on a link goes to the page that was asked for. A part of
  a page (a sheet, a count) or a save goes to the page the person was on.
- A2: a failed tap or save shows a message (`static/js/connection.js`).
  **Try again** shows only for a request that reads. A save is never sent
  again by the message: after a timeout the first one may have arrived,
  and the message says so. Time limits: 20 seconds to read (the plan said
  15; the limit includes the download, which is slow on a weak signal),
  60 seconds to save, 5 minutes for a save with a file. A "No signal" mark shows while
  the browser reports no connection. A request that the person did not
  start (a card that loads by itself, a count) fails with no message. A
  tap that gets a 403 or a 404 shows the app's own page for it.
- A3: the sheet keeps the form and shows the reason above it
  (`showSaveError` in `popup.js`). Save stays active.
- A4: after 5 minutes in the background, the page is fetched again. Only
  in the installed app and on a phone or a tablet: a computer has a
  reload button. Not when a sheet or a drawer is open, a field has the
  focus, something was typed on the page, or rows are selected.
- A5: `AppVersionMiddleware` sends `X-App-Version`. The page carries the
  same value (`<meta name="app-version">`). When they differ, the next
  tap on a link loads the page in full. A save is not changed.
- A6: the copies are deleted at sign-out and when the sign-in page loads.
  **Not done:** `hx-history="false"` on the owner and money pages. It
  makes Back on those pages a full page load and loses the place in the
  list, which is the main way the office uses invoices. The two deletes
  cover the fault that was found. `SECURITY.md` has a line on this.
- A7: one helper (`static/js/overlay_back.js`) for the horse and owner
  preview on a phone, the notification drawer, the More sheet, the photo
  viewer and a location picked on the phone Locations page. The pop-up
  sheet keeps its own code; the two work together. The helper also
  removes a fault that was there before: after a link in a sheet or a
  drawer, one Back press did nothing. **Not done:** the confirm dialog.

Found in phase 1, not corrected:

- **A8.** Back to the horse list gives 4 script errors ("filtersOpen is
  not defined"). The filter button still works. `main` has the same
  errors. They are noise in the error reports. Cause: the copy of the
  page that htmx keeps holds the filter button that Alpine moved into
  the app bar.
- A link in a sheet that loads a full page (the "full edit page" link in
  the quick edit) leaves one Back press that does nothing. Before
  phase 1 too.
- When the sign-in has ended, a save sends the person to the sign-in
  page, and what they typed is lost. Section E, level 3 is the answer.
- On the phone Locations page, Back after a return to the page leaves
  the page while a picked location shows.

## 1. Summary

The base is good. There is a tab bar, bottom sheets and large inputs. No
tested page scrolls sideways at any width from 320 px. The earlier
reviews corrected most of the visual faults.

Three problems remain. They are in order of importance.

1. **The app fails badly when the signal is weak or the sign-in has
   ended.** A tap does nothing, a page goes blank, or typed data is lost.
2. **Each tap moves more data than it needs.** 106 KB of each page is the
   same frame. The phone downloads it again on each tap.
3. **The first screen does not show the most useful facts.** The fixed
   bars take 22% of the screen. On most screens, more than half of the
   text is 12 px or smaller.

Do these eight first. They give the largest gain for the least work.

| # | Change | Why | Effort |
|---|---|---|---|
| 1 | Show the sign-in page when the sign-in has ended (A1). | Today a tap gives a blank page. Tested. | S |
| 2 | Show a message when a tap or a save fails (A2). | Today nothing shows. The person taps again and waits. Tested. | S |
| 3 | Keep the form when a save fails (A3). | Today the typed record is lost. Tested. | S |
| 4 | Remove page copies from the phone at sign-out (A6). | Owner names, emails and phone numbers stay on the phone. Tested. | S |
| 5 | Send only the page content on a tap, not the frame (B1). | A tap downloads 24 to 38 KB. It needs 1 to 15 KB. | M |
| 6 | One top bar in place of two (C1). | Gives about 50 px back on each screen. | M |
| 7 | One compact horse row on each list (C5). | Health is 9.8 screens long for 27 items. | M |
| 8 | On a horse page, put Record and Due above Pedigree (C4). | An empty pedigree is above the overdue vaccination. | S |

Effort: S = under half a day. M = 1 to 2 days. L = 3 days or more.

Phases 1 to 5 are about 6 to 8 weeks of work for one developer.

## 2. How the phone is used

These were assumptions. On 23 November 2026 the owner said that the phone
is used mostly by the people on the yards (section 9, decision 1). Phase 3
depends on this.

| Person | Where | Jobs on the phone |
|---|---|---|
| Yard manager | Walks the yard | Find a horse. See what is due. Record a vaccination, a farrier visit, a worming. Move a horse. Call an owner. |
| Yard staff | Field, barn | Move horses. Feed out. Training log. Add a photo. |
| Owner of the business, office | Away from the desk | Read only: who owes money, who arrived. |

Conditions: one hand, gloves or wet hands, bright light, a weak signal or
no signal. Each visit to the app is short (10 to 60 seconds).

The jobs, most frequent first:

1. Find one horse and read its status.
2. Record one thing for one horse.
3. Record the same thing for many horses (farrier day, worming day).
4. Move one horse or many horses.
5. See what is due today and this week.
6. Log an arrival or a departure.
7. Call an owner.
8. Add a photo.

## 3. What was measured

Method:

- Data: `seed_qa.py`, `mobile_seed.py` and the new `phone_yard_seed.py`
  (75 horses, 9 owners, 5 locations, health records, some overdue).
- Screen: Chromium as a 390 x 844 touch phone. Also 320 x 568, 360 x 740,
  768 x 1024, 820 x 1180 and 844 x 390 (a phone on its side).
- 27 pages: screenshots, page height, text sizes, fixed bars.
- Server: queries, time and HTML size for each page (Django test client).
- Behaviour: no signal, a server error, an ended sign-in, the Back button,
  and a weak signal (400 kbit/s, 400 ms delay).
- Training, maps and C-vet were switched on.

Not tested: a real iPhone or Android phone. Chromium is not Safari.
Section 8 is the list of tests for real phones.

The script `tools/qa/phone_review.js` repeats the measurements. It prints
PASS or FAIL for items A1, A2, A3, A6, A7 and B1. Before phase 1 all
eight checks failed. After phase 1 seven pass; B1 is phase 2.

| Item | Result |
|---|---|
| Sideways scroll | None, at each width from 320 to 844 px |
| Page errors | None in normal use |
| Fixed bars | 114 px at the top and 73 px at the bottom: 187 px. That is 22% of an 844 px screen and 33% of a 568 px screen. |
| Horses on the first screen | 5 full rows at 390 x 844. 2 at 320 x 568. C-vet's rule is 8 or more. |
| Text of 12 px or smaller | Dashboard 64%, Horses 63%, Health 53%, Vaccinations 39%, Farrier 83% (share of characters) |
| Grey text contrast | `sage` on `parchment` is 4.55 to 1. The minimum for small text is 4.5 to 1. |
| Page height | Vaccinations 10.8 screens, Health 9.8, Farrier 7.0, Horses 6.8, Dashboard 4.3, a horse 3.4 |
| HTML for each page | 108 to 406 KB. 106 KB of this is the frame (sidebar 24 KB, scripts in the page 29 KB, icons 9 KB, More sheet 8 KB, sheets). |
| HTML as the phone downloads it | 24 to 38 KB. The frame is about 24 KB of this. |
| Horse list | 406 KB and 3,198 elements for 75 horses. Each row is 4.4 KB and 41 elements. 19 of the 41 do not show on a phone. There is no paging. |
| Scripts and styles | 24 files. 611 KB as written, 134 KB compressed. Fonts 97 KB. `styles.css` is 313 KB as written (204 KB in October). |
| Database queries | Horse page 48, dashboard 36 (and 14 more for the Money part, which loads after), horse list 16, Health 10 |
| A tab tap, weak signal | 1.1 to 1.4 seconds |
| Open a Record sheet, weak signal | 0.9 seconds |
| Page copies on the phone | 560 to 790 KB in `localStorage`, with owner emails and phone numbers. They stay after sign-out. |
| Record vaccination sheet | 10 fields, 1,071 px high, 3 buttons |

## 4. Keep these

Do not change these when you do the work below.

- The tab bar, the More sheet and the bottom sheets.
- The Record sheet: it opens fast, it keeps the focus inside, and the
  phone's Back button closes it.
- The Move sheet: "Today" and "Pick a date", and the rest under
  "More options". It is the model for item C8.
- The search: one box, results in groups (Horses, Owners).
- Inputs of 16 px (an iPhone does not zoom) and 44 px form controls.
- Scripts for maps and charts that load only when a page needs them.
- The Undo button in messages.
- The palette and the fonts.

## 5. Findings and changes

Each item has: the problem, the change and the files. All paths are
relative to `horse_management/`.

### A. Reliability

#### A1. A tap after the sign-in has ended gives a blank page — priority 1, S

- **Problem.** A sign-in ends after 7 days with no use, or after 30 days.
  The next tap gets a redirect to the sign-in page. htmx looks for
  `#main-content` in that page, finds nothing and removes the content. The
  person sees the bars and an empty screen. Tested.
- **Change.** When an htmx request gets a redirect to the sign-in page,
  send `HX-Redirect`. The browser then loads the sign-in page in full.
  `MFAMiddleware._redirect` already does this for the two-step page. Keep
  `?next=` so the person returns to the same page.
- **Files.** `core/middleware.py`, a test in `core/tests/`.

#### A2. A failed tap shows nothing — priority 1, S

- **Problem.** With no signal, or when the server gives an error, a tap
  on a tab or a row does nothing. No message shows. Tested for both.
  A request also has no time limit (`timeout:0` in htmx), so a request on
  a dead signal waits for ever.
- **Change.**
  1. Add one handler in `base.html` for `htmx:sendError`,
     `htmx:responseError` and `htmx:timeout`. Show a message: "No signal.
     Try again." or "That did not work. Try again." with a **Try again**
     button that sends the same request.
  2. Set `htmx.config.timeout` to 15 seconds.
  3. Show a thin "No signal" bar under the top bar while
     `navigator.onLine` is false.
- **Files.** `templates/base.html`. `static/js/popup.js` already has a
  handler for requests in the sheet.

#### A3. A save with no signal loses the form — priority 1, S

- **Problem.** Fill in "Record vaccination" and tap Save with no signal.
  The sheet replaces the form with "That didn't load." The typed record
  is gone. Tested.
- **Change.** Keep the form. Show the message above it. Leave Save
  active, so the person can tap it again when the signal is back. Use
  the "didn't load" text only when the form itself did not load.
- **Files.** `static/js/popup.js` (`showLoadError`).

#### A4. The app does not refresh by itself — priority 2, S

- **Problem.** Pull-to-refresh is switched off
  (`overscroll-behavior-y: none`, `static/css/input.css`). An installed
  app on an iPhone has no reload button. The app can show data from
  yesterday when the person opens it again.
- **Change.** When the app comes back to the front after more than 5
  minutes, fetch the page again (`refreshMain` in `popup.js` does this).
  Do not refresh while a sheet is open or a form has changes.
- **Files.** `templates/base.html`, `static/js/popup.js`.

#### A5. A phone can keep old scripts after a deploy — priority 2, S

- **Problem.** A tap replaces only the page content. The scripts and
  styles in the page head load one time. An installed app can stay open
  for days, so new HTML can run with old scripts. This comes from the
  code. It was not reproduced.
- **Change.** Send the build version in a response header. When it is
  different from the version in the page, load the next page in full.
- **Files.** `core/templatetags/cache_bust.py` (the version),
  `core/middleware.py`, `templates/base.html`.

#### A6. Page copies stay on the phone after sign-out — priority 1, S

- **Problem.** htmx keeps a copy of the last 10 pages in `localStorage`,
  for the Back button. After sign-out, 560 to 790 KB stayed, with owner
  emails and phone numbers. A shared yard phone or tablet keeps them.
  Tested.
- **Change.** Delete `htmx-history-cache` at sign-out and when the
  sign-in page loads. Put `hx-history="false"` on the owner and money
  pages, so they are never stored. Add a line to `SECURITY.md`.
- **Files.** `templates/base.html`, `templates/registration/_auth_head.html`,
  the owner and invoice templates.

#### A7. Back leaves the page when a drawer is open — priority 2, M

- **Problem.** On Android, people close a sheet with the Back gesture.
  The Record sheet handles this. The horse preview sheet and the
  notification drawer do not: Back leaves the page. Tested for these two.
  The More sheet, the photo viewer and the Locations sheet use the same
  pattern and were not tested.
- **Change.** Move the Back code in `popup.js` (lines 80 to 160) into one
  helper. Use it for each sheet and drawer.
- **Files.** `static/js/popup.js`, `split_view.js`, `notifications.js`,
  `lightbox.js`, `locations_mobile.js`, `templates/base.html` (More sheet).

### B. Speed

#### B1. Each tap downloads the frame again — priority 1, M

- **Problem.** The sidebar, the tab bar, the More sheet, the sheets, the
  icons and the scripts in the page are 106 KB of each response. A tap
  uses only `#main-content` and discards the rest. On the weak signal
  test this is about 0.4 seconds of each tap.
- **Change.** For a request with the `HX-Boosted` header, render only the
  title, the messages and `#main-content`. Add `Vary: HX-Boosted`. A full
  load and a Back restore still get the full page.
- **Result, as downloaded.** Dashboard: 30 KB to 6 KB. Health: 27 KB to
  3 KB. A horse page: 30 KB to 8 KB. The horse list: 38 KB to 15 KB, and
  less after B4.
- **Files.** `templates/base.html` (118 templates extend it, so make the
  change there), a test that a boosted response has no sidebar.

#### B2. A tap gives no sign at once — priority 2, S

- **Problem.** The tab changes colour only after the response. The only
  sign of work is a 2 px line at the top. On a weak signal the person
  waits a second with no clear sign.
- **Change.** Mark the tapped tab or row at once. If the response takes
  more than 150 ms, show a skeleton in the content area.
- **Files.** `templates/base.html`, `static/css/input.css`,
  `includes/skeleton_table.html`.

#### B3. Start the request when the finger goes down — priority 3, S

- **Change.** For tab links and list rows, start the GET on `touchstart`
  (the htmx `preload` extension does this). This saves about 100 ms on
  each tap.

#### B4. The horse list is heavy — priority 2, M

- **Problem.** 4.4 KB and 41 elements for each row, and no paging. A yard
  with 300 horses gets about 1.3 MB of HTML and 12,000 elements.
- **Change.**
  1. Do not render the desktop cells on a phone, and the reverse.
  2. Make the row's action menu one time for the list, not one for each
     row.
  3. Above 100 rows, load the next 50 when the person gets near the end.
- **Files.** `templates/horses/_horse_row.html`, `_horse_row_flat.html`,
  `core/views/horses.py` (`get_paginate_by`).

#### B5. All 24 files load on each first visit — priority 3, S

- **Problem.** Seven scripts are only for maps (30 KB compressed). When
  maps are on, the dashboard also loads Leaflet and map tiles for
  "Near you" on each cold start. `styles.css` grew from 204 KB in October
  to 313 KB now.
- **Change.** Load the map scripts the same way as Leaflet: when a page
  has a map. On a phone, load the "Near you" map when the card comes into
  view. Add a size limit to CI for `styles.css` and for the scripts (C-vet
  has such a limit).
- **Files.** `templates/base.html` (lines 36 to 66), `.github/workflows/ci.yml`.

#### B6. Query counts — priority 2, M

- **Problem.** The horse page runs 48 queries and the dashboard 36, with
  more for its two late parts. On the live database each query is a trip
  across the network. The live times are not known.
- **Change.** Read `Server-Timing` on the live site for the 5 main pages
  (`ServerTimingMiddleware` exists). Then cut the queries to meet the
  targets in section 6.

#### B7. Add Brotli — priority 3, S

- **Problem.** The `brotli` package is not installed, so WhiteNoise sends
  gzip only.
- **Change.** Add `brotli` to `requirements.in`. Styles and scripts get
  about 15% smaller with no other work.

#### B8. Photos are sent at full size — priority 2, M

- **Problem.** A phone photo is 3 to 10 MB. The limit is 5 MB
  (`core/models.py`). The phone sends the file as it is. On a weak signal
  an upload takes minutes, or the server refuses it.
- **Change.** Make the photo smaller on the phone before it is sent
  (longest side 2,000 px, JPEG, about 500 KB). Show progress. Keep the
  photo in the sheet if the upload fails.
- **Files.** `static/js/photo_quick_add.js`,
  `templates/horses/partials/photo_form.html`.

### C. Layout of key information

#### C1. Two top bars — priority 1, M

- **Problem.** A 44 px bar shows the section name. A 70 px bar shows the
  search and Add. With the tab bar, 187 px is always used. On a small
  phone (320 x 568) only 2 horses show.
- **Change.** One bar of about 60 px: bell, search box, filter, Add.
  Remove the name bar. Show the page title as a heading on the pages that
  hide it now. The tab bar still names the section.
- **Files.** `templates/base.html` (line 151), `includes/_app_bar.html`,
  the list templates with `<h1 class="sr-only">`.
- This changes the earlier layout. See section 9, decision 3.

#### C2. Health is two taps away — priority 2, S

- **Problem.** The tabs are Dashboard, Horses, Locations, Training, More.
  Health and Farrier are in More. When Training is off, the bar has 4
  tabs and one free place.
- **Change.** When Training is off, show Health as the fourth tab. Then
  use PostHog to see which sections people open on a phone, and set the
  tabs from that.
- **Files.** `templates/base.html` (lines 239 to 293).

#### C3. The dashboard's first screen shows counts — priority 2, M

- **Problem.** The first screen has the greeting, 5 tiles and the top of
  a month grid. "Horses 74" and "Owners 9" are counts, not jobs. On a
  phone the grid is about 450 px high and shows dots, not words. "Today"
  is below the first screen. The Timeline shows 18 name chips for one
  farrier visit, each 20 px high.
- **Change, on a phone only.**
  1. Tiles: show only the ones that need action (over capacity,
     notifications, money overdue). Put the counts lower.
  2. Show a list in place of the grid: today and the next 7 days, one
     line for each item, with a Record button. A "Month" button opens the
     grid.
  3. Timeline: "Farrier · Trim only · 18 horses" on one line. A tap
     shows the names.
- **Files.** `templates/dashboard.html`, `templates/partials/dashboard/`
  (`metrics.html`, `calendar.html`, `activity.html`).
- Item 2 changes a decision of September. See section 9, decision 2.

#### C4. The horse page shows an empty pedigree before what is due — priority 1, S

- **Problem.** On a phone the order is: header, placement, Pedigree,
  Record row, Due. A horse with no pedigree shows about 300 px of "+ Add"
  boxes above an overdue vaccination. Notes and conditions (safety
  information) are 2.6 screens down. At 320 px the three header buttons
  stack into three rows.
- **Change, on a phone only.**
  1. Order: header, Record row, Due, placement, Notes and conditions,
     then the rest.
  2. Empty pedigree: one line, "Pedigree not entered · Add". A pedigree
     with data: one line "by X out of Y" that opens the tree.
  3. Put Move, Edit and More in one full-width row under the name.
  4. When a horse has an active condition or a handling note, show one
     line in the header that links to it.
- **Files.** `templates/horses/horse_detail.html`. The desktop layout of
  21 November does not change.

#### C5. One horse has four different rows — priority 1, M

- **Problem.** The same horse is an 85 px row on the horse list, a 180 px
  card on a location page, a 190 px card on Health and a chip on Farrier.
  The card puts a label above each value (TYPE, DETAIL, DUE). That is
  good for 5 rows and heavy for 50. Health is 9.8 screens. Vaccinations
  is 10.8 screens.
- **Change.** Make one phone row, 64 to 72 px high, and use it on each
  list:
  - Line 1: the name, then the status in words ("Flu · 15 days overdue").
  - Line 2: location and owner.
  - Right side: one action (Record) or an arrow.
- **Files.** A new include in `templates/includes/`,
  `health/health_dashboard.html`, the vaccination list,
  `locations/location_detail.html`, `owners/owner_list.html`,
  `.data-table-cards` in `static/css/input.css`.

#### C6. 27 overdue items are 27 separate cards — priority 2, M

- **Problem.** The usual job is "the vet gave 12 flu vaccinations today".
  Health shows each horse as its own card with its own button.
- **Change.** Group the items by type: "Flu vaccination · 27 horses
  overdue", with **Record for all**. The button opens the bulk sheet with
  those horses selected. The bulk form exists.
- **Files.** `templates/health/health_dashboard.html`, `health/views.py`.

#### C7. The text is small for outdoor use — priority 2, M

- **Problem.** On the dashboard and the main lists, more than half of
  the text is 12 px or smaller. The grey is at the contrast limit. The
  display size setting works only on a computer.
- **Change.** On a phone: 16 px for names, amounts and due status. 14 px
  for the second line. 12 px only for labels. No 10 px text. Use
  `charcoal-light` for small grey text. Add "Text size: Standard / Large"
  for the phone to the profile.
- **Files.** `static/css/input.css`, the profile form.

#### C8. The Record sheets are long — priority 2, M

- **Problem.** "Record vaccination" has 10 fields and 3 buttons. The
  usual record needs 2 answers: the type and the date. "Next due date"
  offers Yesterday, Today and Tomorrow, which do not fit a date that is
  months away.
- **Change.** Show the type, the date (Today) and Save. Put service and
  cost, course stage, vet, batch and notes under "More details", as the
  Move sheet does. For the next due date, offer "+6 months" and
  "+12 months". One row of buttons: Cancel and Save.
- **Files.** `templates/health/vaccination_form.html` and the other
  health forms, `static/js/date_quick.js`.

#### C9. The horse preview starts with an empty photo — priority 3, S

- **Problem.** For a horse with no photo, the sheet opens with a large
  rust block and "Add photo". The due status is not in the first view.
- **Change.** With no photo, show the small avatar. First lines: name,
  due status, location, owner with Call. Then the actions.
- **Files.** The preview partials in `templates/horses/partials/`.

#### C10. Small controls — priority 2, S

- **Problem.** The app's rule is 44 px. These are smaller: the bell
  (40 x 40), the calendar arrows (32 x 32), the tick boxes on a location
  page (20 x 20), the name chips in the dashboard Timeline (20 px high),
  and the header buttons and view tabs (40 px high).
- **Change.** Make the first four 44 px. For header buttons, decide if
  40 px is the rule and write it down.

#### C11. Notifications are tall — priority 3, S

- **Problem.** Each notification is about 145 px. Five "over capacity"
  notices show as five items, with only Dismiss.
- **Change.** Group by kind ("5 locations over capacity"). Give each
  group one main action. Use the compact row of C5.

#### C12. Tablets and a phone on its side — priority 3, S

- **Problem.** The manifest locks the installed app to portrait. A tablet
  on a stand cannot turn. The search box shows "Press /" on a touch
  screen. On a phone on its side the bars take 48% of the height.
- **Change.** Remove `orientation` from the manifest. Hide the "Press /"
  hint when the pointer is a finger. On a phone on its side, hide the
  name bar.

### D. App finish

#### D1. How long a phone stays signed in — priority 2, decision

- **Problem.** Staff must sign in again after 7 days with no use, and
  after 30 days. Administrators also need the two-step code. This can
  happen in a field with no signal.
- **Options.** (a) Longer times for a phone (C-vet uses 90 days).
  (b) Passkeys: sign in with Face ID or a fingerprint. (c) No change.
- See section 9, decision 4.

#### D2. No help to install — priority 2, S

- **Change.** Show an "Install the app" card one time in the phone
  browser. Android: a button (`beforeinstallprompt`). iPhone: two steps
  with pictures (Share, then Add to Home Screen). Put the same steps in
  Help.

#### D3. The manifest and the start screen — priority 2, S

- **Problem.** An iPhone shows a white screen at the start (no start
  image). The manifest has no `id`, `screenshots` or `categories`. The
  shortcuts are New Arrival, Health Dashboard and Invoices.
- **Change.** Add the start images and the missing fields. Make the
  shortcuts the phone jobs: Find a horse, Log arrival, Health.
- **Files.** `static/manifest.json`, `templates/base.html`.

#### D4. No service worker — priority 1 in phase 4, M

- **Problem.** The main app has none. With no signal, an installed app
  shows the browser's error page.
- **Change.** Add a service worker that keeps the styles, scripts, fonts
  and icons on the phone, and an "offline" page with **Try again**. It
  stores no page with personal data (unless you choose level 2 in
  section E). `templates/cvet/sw.js` is the pattern.

#### D5. No push notifications — phase 5, L

- **Problem.** Reminders go by email and to the notification centre in
  the app. A phone app is expected to send notifications.
- **Change.** Web push. It works on Android. It works on an iPhone
  (iOS 16.4 or later) after "Add to Home Screen". Start with three:
  a morning summary of overdue items, "farrier visit finished", and
  "arrival due today". Each person chooses in their profile. Show the
  count on the app icon.
- **Files.** `notifications/` (a subscription model, a Celery task),
  `static/js/notifications.js`, the service worker.

#### D6. Files and sharing — priority 3, S

- **Problem.** An invoice PDF opens in the app's own window. On an
  installed iPhone app there may be no way back (test 4 in section 8).
- **Change.** Use the phone's share sheet for the PDF, so the person can
  send it by email or a message app.

#### D7. Know what happens on phones — priority 2, S

- **Change.** Send PostHog events for: a failed request, no signal, a tap
  that takes more than 2 seconds, and "installed". Use the page data to
  choose the tabs (C2).

#### D8. One name — priority 2, decision

- **Problem.** The templates say "Cgate" (152 files) and "C-gate" (4).
  The scripts use `Yardway`. A store needs one name, and a check that
  the name is free to use.

#### D9. Legal pages — before any store

- **Problem.** The privacy page is a placeholder
  (`LAUNCH_READINESS_PLAN.md`, section 4). Both stores need a public
  privacy page and a support address.

Dark mode is not in this plan. Sunlight is the usual condition, and C7
is the answer to it.

### E. Work with no signal: three levels

The main app makes each page on the server. With no signal, nothing
works. There are three levels of answer.

| Level | What the person gets | Work | Risk |
|---|---|---|---|
| 1. Fail well | A clear message. Nothing is lost. Try again. | Phase 1 (items A1 to A4) | None |
| 2. Read with no signal | The last copy of the horse list and of the horse pages they opened, marked "Saved copy from 10:42". | M, with the service worker (D4) | Personal data is stored on the phone. Delete it at sign-out and after 24 hours. |
| 3. Record with no signal | The record is kept on the phone and sent when the signal is back. "3 records not sent" shows. | L (3 to 4 weeks) | Two people can change the same horse. Needs the C-vet rules: ids made on the phone, add only, the count always on the screen. |

Recommendation: level 1 now. Decide level 2 with phase 4. Decide level 3
after the first yards have used the app. The PostHog events of D7 will
show how often the signal fails.

### F. The route to an app in the stores

| Route | What the person gets | Work | Cost | What stops it |
|---|---|---|---|---|
| 1. Installed web app | An icon on the Home Screen, full screen, push. iPhone and Android. An update is live at once. | Phases 4 and 5 | None | On an iPhone the person must use "Add to Home Screen" by hand. |
| 2. Google Play | A page in Google Play. It opens the same web app. | S to M, after route 1 | About $25, one time | One web address for all yards. A privacy page. Play's rules for new accounts. |
| 3. Apple App Store | A page in the App Store. A small native shell with native push and Face ID. | L (3 to 6 weeks), a Mac to build, a review for each release | About $99 each year | Apple refuses an app that is only a website in a frame. One web address for all yards. A test account for the reviewer. |

**The main obstacle is not the phone work.** A store has one app for all
yards. Today each yard has its own web address and its own database
(`SECURITY.md`: "There is no multi-tenancy"). A store app must know
which yard to open. That needs the multi-yard sign-in of
`LAUNCH_READINESS_PLAN.md` (P2, 4 to 6 months), or a first screen that
asks for the yard's address.

Recommendation: launch with route 1. Routes 2 and 3 show the same web
app in a shell, so phases 1 to 5 are needed for them too. No work is
lost. Start the store work when the multi-yard sign-in exists, or when a
customer asks for it. Store rules change: read them again at that time.

## 6. Targets

| Item | Now | Target |
|---|---|---|
| First sign after a tap | After the response | Under 100 ms |
| A tab tap, good signal | 0.2 to 0.4 s (local) | Under 0.3 s |
| A tab tap, weak signal (400 kbit/s, 400 ms) | 1.1 to 1.4 s | Under 1 s |
| Download for one tap | 24 to 38 KB | Under 15 KB, long lists excepted |
| Open the installed app to the first screen | Not measured | Under 2 s on 4G |
| Fixed bars | 187 px | 135 px or less |
| Horses on the first screen (390 x 844) | 5 | 8 or more |
| Text of 12 px or smaller | 39% to 83% on the main lists | Under 25% |
| Longest list page | 10.8 screens | 4 screens for 50 items |
| Queries for one page | Up to 48 | 25 or fewer |
| Checks in `phone_review.js` | 7 of 8 pass (0 before phase 1) | 8 of 8 pass |

## 7. Order of work

Each phase is one or two pull requests, with tests and phone screenshots.

| Phase | Items | Effort | Result |
|---|---|---|---|
| 1. Nothing breaks, nothing is lost | A1, A2, A3, A6, A4, A5, A7 | About 1 week | Safe to use with a weak signal |
| 2. Faster taps | B1, B2, B3, B7, B8, B4, B5, B6 | About 1 week | Each tap is quicker and gives a sign at once |
| 3. Key information first | C1, C4, C5, C6, C3, C8, C7, C10, C2, C9, C11, C12 | 2 to 3 weeks | The first screen answers the question |
| 4. Installed app | D4, D2, D3, D1, D6, D7, the tests of section 8 | 1 to 2 weeks | Installs and starts like an app |
| 5. Push notifications | D5 | About 1 week | The app tells people what is due |
| 6. App stores | Routes 2 and 3, D8, D9 | After the decision | A page in each store |

Do phase 1 before a second yard uses the app on a phone. Phases 2 and 3
can swap.

How to check each phase:

1. Run `python manage.py test` and `node --test 'static/js/tests/*.test.js'`.
2. Run `npm run build:css`.
3. Seed a throwaway database with `seed_qa.py`, `mobile_seed.py` and
   `phone_yard_seed.py`. Run `tools/qa/phone_review.js` (the steps are in
   `tools/qa/README.md`). Compare the table and the PASS lines with
   section 6.
4. Look at the screenshots at 390 px and 320 px.

## 8. Test on real phones

Chromium on a computer cannot show these. Do them on one iPhone and one
Android phone before phase 4 is closed.

1. Add the app to the Home Screen. Open it. Look at the top and bottom
   edges (the notch and the home bar).
2. Sign in with Google from the installed app. Does it come back to the
   app?
3. Open the app after 1 day and after 8 days. Is the person still signed
   in?
4. Open an invoice PDF. Is there a way back?
5. Take a photo with the camera and save it, on 4G.
6. Open each Record sheet. Does the keyboard cover Save?
7. Use the Back gesture with each sheet and drawer open.
8. Turn the phone and the tablet on their sides.
9. Switch flight mode on, tap Save, switch it off, tap Save again.
10. Speak a note with the keyboard's microphone.
11. Set the phone's text size to large. Does the layout hold?
12. Use the app in bright sun with one hand.

## 9. Decisions needed from you

1. **Who uses the phone, and for which jobs?** Section 2 is an
   assumption. Correct it if it is wrong.
   **Answer (23 Nov 2026): mostly the people on the yards.** The yard
   manager and yard staff rows of section 2 come first in phase 3.
2. **Dashboard on a phone:** a list of today and the next 7 days in
   place of the month grid (C3)? Recommended: yes. The grid stays on a
   computer and behind a "Month" button.
   **Answer (23 Nov 2026): yes.**
3. **One top bar** with the search box, and no name bar (C1)?
   Recommended: yes.
4. **Sign-in on a phone** (D1): longer times, passkeys, or no change?
   Recommended: 30 days with no use and 90 days in total for an installed
   phone, then passkeys later.
5. **Saved copies for reading with no signal** (section E, level 2)?
   This stores owner data on the phone for up to 24 hours.
6. **App stores** (section F): installed web app first? Recommended: yes.
   **Answer (23 Nov 2026): yes.**
7. **The name** for the icon and the stores (D8).
