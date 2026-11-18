# Sidebar, profile, notification centre and help centre — plan

Status: approved. Work starts with WP1.

## 1. Decisions (from the questions)

| Topic | Decision |
|---|---|
| Sidebar colour | Dark Brim teal (`forest` #3D5A63). Cream icons and text. |
| Nav | Flat list with small group labels (Manage, Finance). No folding. |
| Search | Moves into the sidebar on a computer. Stays at the top on a phone. `/` still opens it. |
| Profile pop-up | Name, email, phone, photo, password, two-step sign-in (MFA), notification choices. |
| Notifications | A full notification system. First events: needs-action items, horse movements, money. |
| Dashboard | Needs action card goes. A month calendar takes its place beside the map. |
| Support | Help centre inside the app, with guides and a contact link. |

## 2. Work packages

There are 5 packages. Each one is a separate pull request, so each one is small enough to review and test.

### WP1 — Sidebar shell and the collapse bug

**Layout, from top to bottom (wide sidebar):**

1. Logo + "Cgate" + collapse button.
2. Search box (looks like the reference: rounded, a little lighter than the sidebar).
3. Nav list (same 12 items and the same permission checks as now).
4. Bottom block, above a thin line: **Notifications** (with a count badge), **Support**, **Settings**.
5. Profile card: avatar, name, email, and a `⋮` button. Click opens the profile pop-up (WP2).

**Rail (collapsed, 64px):**

- The logo shows. When you put the mouse on the logo, it changes to the "expand" arrow (the same method that Linear and Notion use). This fixes the overlap: only one 32px item is in the top row.
- The search becomes a search icon. Click opens the search as a floating panel next to the rail.
- Notifications show a small dot with the count.
- The profile card shows only the avatar.

**Top bar on a computer:** search goes out. The bar keeps **+ Add** and the page filters. It becomes thinner. On a phone it does not change.

**Search code:** the search box, results panel and keyboard keys move from `_app_bar.html` to a new `includes/_app_search.html`. The phone top bar and the sidebar both include it. The search back end (`quick_find`) does not change.

**Files:** `templates/base.html`, `templates/includes/_app_bar.html`, new `templates/includes/_sidebar.html` and `_app_search.html`, `static/css/input.css` (sidebar section).

**Tests:** a template test for the sidebar per role (items hidden when access is hidden). A Playwright screenshot of wide and rail states to check the top row.

### WP2 — Profile pop-up

**Model:** new `UserProfile` (one-to-one with user): `phone`, `photo`, `photo_thumb`. Photo upload uses the same image code as horse photos (HEIC support, square thumbnail).

**Pop-up:** opens in the existing pop-up sheet (`_popup_sheet.html`). It has 3 tabs:

| Tab | Contents |
|---|---|
| Details | First name, last name, email, phone, photo (upload / remove). |
| Security | Change password (needs the current password). Two-step sign-in: status, set up, turn off, new recovery codes. Uses the current `account_security` code. The old page stays as a fallback link. |
| Notifications | Per type (Needs action, Horse movements, Money): "In the app" on/off and "By email" on/off. Built in WP3; this tab shows after WP3. |

**Rules:**

- Email change: check that no other user has the same email. Google sign-in users see a note that their Google email does not change.
- Avatar: the photo shows wherever the letter avatar shows now (sidebar, phone More sheet, user list in Settings).
- On a phone, the account row in the More sheet opens the same pop-up.

**Tests:** form validation, photo upload, password change, permission (a user can change only their own profile).

### WP3 — Notification system

**Models:**

- `Notification`: recipient, category (`action` / `movement` / `money`), kind, title, body, link URL, `dedupe_key`, `created_at`, `read_at`, `emailed_at`. Unique on (recipient, dedupe_key), so the same item never shows two times.
- `NotificationPreference`: user, category, `in_app` (default on), `email` (default off).

**Who gets a notification:** each active user whose role can see that area. Example: a role with Invoices hidden never gets a "Money" notification. This uses the same `has_feature_access` checks as the rest of the app.

**Where notifications come from:**

| Category | Source | When |
|---|---|---|
| Needs action | `core/dashboard/attention.py` (the same list as today). | A scheduled task runs each hour. Also, when a user opens the panel, a sync runs if the last one is older than 5 minutes. When an item is done, its notification closes by itself. |
| Horse movements | `PlacementService.move_horse`, new arrival, `confirm_departure`. | At the time of the change. |
| Money | `Invoice.mark_as_paid`, the overdue-invoice task, Xero sync failure. | At the time of the change. |

The person who did the action does not get a notification about their own action.

**Notification centre (the panel):**

- Opens from **Notifications** in the sidebar (computer) or a bell in the phone top bar.
- Slides out next to the sidebar. Width about 400px. On a phone, full screen.
- Tabs: **Unread** / **All**. Filter chips: Overdue, Health, Yard, Money (these move from the dashboard).
- Each row: icon, title, horse/owner, date, and the item's own action button (for example **Add passport**, **Record vaccination**). Click the row to open the record.
- Actions: mark as read, mark all as read. Admins keep **Dismiss** and **Restore** for needs-action items (yard-wide, as now).
- Badge: count of unread items. It updates every 60 seconds and after each page change.

**Email:** if a user turns on "By email" for a category: movements and money are sent at the time of the event (by the background worker); needs-action items go in one email each morning. Uses the current mail code.

**Clean-up:** read notifications older than 90 days are deleted by the current nightly purge task.

**Tests:** recipient rules per role, dedupe, auto-close when an item is done, each event hook, the panel view, email sends.

### WP4 — Dashboard

- Remove the **Needs action** card and the filter chips under the greeting.
- The headline ("1 thing needs doing") becomes a link that opens the notification centre.
- **Month calendar** replaces the "Next 14 days" card, beside the map, with the same height as the map.
  - 5 or 6 week rows, Monday first. Today is marked.
  - Each day shows coloured dots by type (same colours as now), and "+N" when there are many.
  - Click a day: a list of that day's items shows under the grid, with their action buttons.
  - Arrows go to the previous / next month (htmx, no full page load). A "Today" button goes back.
  - On a phone: the grid shows first; the day list shows under it.
- The "Visits worth booking together" list stays, under the calendar.
- The data comes from the current `upcoming` code, made to accept any start and end date.
- Dashboard settings: the "Needs action" widget toggle goes; "Next 14 days" is renamed "Calendar".
- The **Health** page does not change. It still uses the same attention code.

**Tests:** month view data (start/end dates, month edges), day list view, dashboard renders without the attention card.

### WP5 — Help centre and support

- New pages at `/help/`: a list of guides with a search box, and one page per guide.
- Guides are Django templates in `templates/help/articles/`. A small Python list holds each guide's title, summary and search words. (No new library is needed. The app has no Markdown library now.)
- First guides (about 15):
  1. Getting started
  2. Your profile and security
  3. Notifications
  4. Search
  5. Horses
  6. Locations and the map
  7. Health (vaccinations, worming, vet visits)
  8. Farrier
  9. Breeding
  10. Feed
  11. Owners
  12. Charges and invoices
  13. Costs and finances
  14. Users and roles (admins)
  15. Xero (admins)
- Each guide: what the page is for, steps for the main tasks, common problems.
- Guides show only for areas the user's role can see.
- Bottom of each page: **Contact support** (help@c-gate.app).
- **Support** in the sidebar opens `/help/`.

## 3. Order of work

1. WP1 — sidebar and bug fix (needed by all others).
2. WP2 — profile pop-up (without the Notifications tab).
3. WP3 — notification system, then the Notifications tab in the profile.
4. WP4 — dashboard (needs WP3, because the Needs action list must have a new home first).
5. WP5 — help centre (can be done at any time after WP1).

## 4. Risks

| Risk | What I will do |
|---|---|
| Moving search breaks the `/` key, the phone Locations page, or tests. | Keep the same element IDs. Run the full test suite and a Playwright check. |
| Hourly sync is slow for large yards. | The attention code is already used on each dashboard load. The sync writes only changed rows. |
| Too many notifications. | Dedupe key per item. Users can turn off a category. |
| The health page and the panel disagree. | Both use the same attention code, as now. |

## 5. Answers to the open points

1. Support email: **help@c-gate.app**.
2. Each user can turn off any category in the app, Needs action included.
