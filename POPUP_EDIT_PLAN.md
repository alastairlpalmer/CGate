# Pop-up editing plan: Edit, Photo, Move

**Goal:** Let users make small changes without leaving the page they are on.
**Scope:** Horse Edit, Horse Photo, Horse Move. Then the same pattern for other quick actions.
**Status:** All four phases built, plus phase 5 (Record payment, Feed out, Log arrival, Add document, Placement edit, health record edits, Add owner/location, in-place Placements filters).

---

## 1. The problem today

| Action | What happens now | Why it hurts |
|--------|------------------|--------------|
| Edit | Opens `/horses/<id>/edit/` (full page, 13 fields + ownership formset). Save returns to horse detail. | A one-field change costs two page loads and loses list scroll position. |
| Photo | Opens `/horses/<id>/photos/add/` (full page). Save returns to horse detail. | Same. Also the button name does not say what the photo is for (see section 2). |
| Move | Opens `/horses/<id>/move/` (full page, 6 fields). Save returns to the **horse list**, even if you came from horse detail. | Two loads. Four of the six fields are rarely changed. Return target is inconsistent. |

Entry points that all go to these full pages:

- `templates/horses/horse_detail.html`: header buttons Photo, Edit. Quick Actions: Move, Photo.
- `templates/horses/horse_list.html`: row icons Photo, Edit, Move (two places: grouped rows, lines 427-434 and 492-504).
- `templates/locations/location_detail.html`: Move, Photo (line 623, 630).
- `templates/locations/location_list.html`: Move (line 187, 243).
- Arrival toasts link to "Add photos" (`core/views/horses.py` lines 493, 530).

## 2. Answer: what does the Photo button do?

The Photo button does **not** change the profile picture.

- **Photo button / "+ Add" on the Photos card** creates a `HorsePhoto` row. It shows in the Photos card on horse detail. Category "Passport" is the exception: that file is saved as a `Document` instead (`core/views/photos.py`, `horse_photo_add`).
- **Profile picture** is the `photo` field on the `Horse` model. You can only change it on the Edit page ("Photo" field under Identification). The thumbnail is generated on save (`Horse._sync_photo_thumb`).

There is no link between the two today. The plan fixes this in Phase 2:

1. Rename the Edit-page field label from "Photo" to "Profile picture".
2. Add a "Set as profile picture" tick box in the photo pop-up. Off by default. When on, the first photo of the batch is copied to `Horse.photo`.
3. Add a "Use as profile picture" action on each photo in the Photos card.

## 3. What already exists that we can reuse

The app already has a working pop-up. It is the bulk-action modal on the Health dashboard:

- Markup: `templates/health/partials/bulk_action_bar.html` lines 50-90. It is a bottom sheet on phones (`items-end`, `rounded-t-2xl`, safe-area padding) and a centred dialog on desktop (`sm:items-center`, `max-w-lg`).
- Form partial: `templates/health/partials/bulk_health_form.html`. It posts with `hx-post`, `hx-target` the container, `hx-select="unset"`, `hx-push-url="false"`.
- Loader: `bulkActions()` in `templates/base.html` lines 572-686. It fetches the form HTML and calls `htmx.process`.

Two things must stay true for every pop-up (guarded by `core/tests/test_htmx_lint.py`):

- Every `hx-get` or `hx-post` that targets something other than `#main-content` sets `hx-select="unset"`.
- Every `hx-get` or `hx-post` sets `hx-push-url="false"`.

Reason: `<body>` is `hx-boost="true"` with `hx-select="#main-content"` and `hx-push-url="true"`. Children inherit those. A pop-up that forgets them blanks the page and rewrites the URL.

## 4. Design

### 4.1 One shared sheet, rendered once

Add `templates/includes/_popup_sheet.html` and include it once in `base.html`, next to the More sheet. It holds:

- Backdrop (`fixed inset-0 bg-charcoal/50 z-50`). Click on backdrop closes.
- Panel. Phone: bottom sheet, `max-h-[92dvh]`, drag handle, `rounded-t-2xl`, `pb-[env(safe-area-inset-bottom)]`. Desktop (`sm:`): centred, `max-w-lg`, `rounded-lg`, `max-h-[90vh]`.
- Header: title, small avatar, X button (44 px).
- Body: `<div id="popup-body">`. htmx swaps the form partial in here.
- Footer: none. Each form partial brings its own footer so buttons stay with the form.

Alpine state lives in one store: `Alpine.store('popup', { open, title, dirty, opener })`.

### 4.2 How a trigger opens it

Triggers stay real links. Without JavaScript they still go to the full page.

```html
<a href="{% url 'horse_move' horse.pk %}"
   hx-get="{% url 'horse_move' horse.pk %}"
   hx-target="#popup-body" hx-select="unset" hx-swap="innerHTML"
   hx-push-url="false"
   data-popup-title="Move {{ horse.name }}"
   class="btn-icon" ...>
```

One body listener (`htmx:beforeRequest` on elements with `data-popup-title`) opens the sheet, sets the title, and shows a skeleton until the swap lands. The listener also records the trigger element so focus can return to it on close.

### 4.3 How the server answers

Each of the three views checks `request.headers.get('HX-Request')`.

| Request | Non-HTMX (today, unchanged) | HTMX (new) |
|---------|------------------------------|------------|
| GET | Full page | Partial only: `horses/partials/<name>_form.html` |
| POST invalid | Full page with errors | Partial with errors (status 200 so htmx swaps it) |
| POST valid | Redirect | `204 No Content` + header `HX-Trigger: {"popup:saved": {}}` |

Django `messages` set during the POST are shown on the next page render. On `popup:saved` the client closes the sheet and re-fetches the current page into `#main-content` (`htmx.ajax('GET', location.href, {target:'#main-content', select:'#main-content', swap:'outerHTML'})`). The existing toast extractor in `base.html` (`htmx:afterSwap`, lines 496-507) lifts the success toast out of that response. Result: you stay where you were, the data on screen is fresh, and the toast shows.

Template split: move the `<form>` and fields out of each full-page template into a partial. The full page includes the partial. The partial is the only thing that changes when fields change.

- `horses/horse_move.html` includes `horses/partials/move_form.html`
- `horses/photo_quick_add.html` includes `horses/partials/photo_form.html`
- `horses/horse_form.html` stays as the full edit page. New `horses/partials/quick_edit_form.html` for the pop-up (see 4.6).

Form partial rules:

- `hx-post="{{ request.path }}" hx-target="#popup-body" hx-select="unset" hx-swap="innerHTML" hx-push-url="false"`.
- File uploads add `hx-encoding="multipart/form-data"`.
- Footer: Cancel (`@click="$store.popup.close()"`) and the primary button.
- Full-page fallback: when the partial renders inside the full page, Cancel is a plain link back. Use a template variable `in_popup` to switch.

### 4.4 Phone behaviour

- Sheet rises from the bottom. It covers the bottom tab bar, so a stray tap cannot navigate away mid-form (the T1.2 problem from `MOBILE_QA_REPORT.md` does not apply inside the sheet).
- The form footer is `sticky bottom-0` inside the sheet, so Save stays above the keyboard while you scroll fields.
- Body scroll is locked while open (`overflow:hidden` on `html`).
- Swipe down on the handle, tap the backdrop, tap X, or press the Android back button closes it. Opening pushes one history state so Back closes the sheet instead of leaving the page.
- Camera and gallery inputs (`capture="environment"`) work the same inside the sheet.
- All controls keep the 44 px minimum. Inputs keep 16 px font on phones (already in `input.css`, no zoom on focus).

### 4.5 Desktop behaviour

- Centred dialog, 32 rem wide, scrolls inside if long.
- Focus moves to the first field on open and returns to the trigger on close.
- Escape closes. Cmd/Ctrl+Enter submits.
- `role="dialog" aria-modal="true" aria-labelledby="popup-title"`.
- Respect `prefers-reduced-motion`: no slide, fade only.

### 4.6 Field sets per pop-up

**Move** (the smallest form, ship first)

Always shown:
1. New location (grouped select, required).
2. Move date: two chips, **Today** (default) and **Pick a date**. Picking reveals the date input.
3. Notes: one-line auto-grow textarea.

Behind a "More options" disclosure (closed by default):
4. New owner.
5. New rate.
6. Expected departure.

Header shows "Currently at Bottom field · Mrs Sophie Macpherson · £7.00/day" as one line, so the current state is visible without a card.

**Photo**

Same fields as today: category chips, Take photo, Choose from gallery, pending thumbnails, note. Plus the new "Set as profile picture" tick box. The Alpine `quickPhotoAdd()` component moves into the partial. Alpine initialises swapped-in components on its own, so no extra wiring is needed.

**Quick Edit**

The full form is too big for a sheet. The pop-up shows the fields people change day to day:

- Name, Sex, Colour
- Date of birth, Age
- Passport number, Has passport
- Notes
- Is active (with the existing "cannot untick while placed" guard message)

Not in the pop-up: profile picture, dam and sire, breeding text, ownership shares. A "Full edit" link at the bottom of the sheet goes to the existing page for those. Server side: a `QuickHorseForm` (subclass of `HorseForm` with `Meta.fields` limited) so validation stays in one place. `HorseUpdateView` keeps the full form; a new `horse_quick_edit` view serves the pop-up.

### 4.7 Unsaved changes

The store tracks `dirty` (set on first `input` event inside `#popup-body`). Closing a dirty sheet asks "Discard changes?" with Keep editing / Discard. This is the only confirm dialog in the flow.

## 5. Phases

| Phase | Work | Size | Result for users |
|-------|------|------|------------------|
| 1 | Shared sheet in `base.html`. Move pop-up. Wire Move triggers on horse detail, horse list, location list, location detail. Non-HTMX Move honours a same-origin `next`, else lands on the horse list as today. | M | Moving a horse is one tap, one pick, one save. You stay on the list. |
| 2 | Photo pop-up. "Profile picture" label. "Set as profile picture" tick box. "Use as profile picture" on photo grid. | M | Camera opens from the sheet. Profile picture and photo log are clearly separate. |
| 3 | Quick Edit pop-up + `QuickHorseForm`. Edit triggers on horse detail and horse list point at it. Full edit page keeps its URL. | M | Rename or fix a colour without leaving the list. |
| 4 | Same pattern for the other Quick Actions that already deep-link with `?horse=` (Vaccination, Farrier, Worming, Egg count, Vet visit, Charge), then Owner and Location edit. | L | One editing model across the app. |

Phase 1 alone is worth shipping. Phases 2 and 3 do not depend on each other.

## 6. Tests to add

Per view, in `core/tests/test_popup_forms.py`:

- GET with `HX-Request: true` returns the partial only (no `<html`, no `#main-content`).
- GET without the header returns the full page (unchanged).
- POST valid with header returns 204 and `HX-Trigger` contains `popup:saved`.
- POST invalid with header returns 200 with the field error in the body.
- POST valid without header redirects as before.
- View-only role gets the same 403 or redirect as today (`core/roles_testutils.py`).

`test_htmx_lint.py` already covers the attribute rules for every new `hx-*` element.

Mobile check: the Playwright sweep from `MOBILE_QA_REPORT.md` at 390 x 844. Open each sheet, confirm no horizontal overflow, Save visible with keyboard open, all tap targets 44 px.

## 7. Risks and how the plan handles them

| Risk | Handling |
|------|----------|
| Inherited `hx-select` / `hx-push-url` from the boosted body blanks the page. | Every trigger and form sets both. Lint test fails the build otherwise. |
| A boosted navigation while the sheet is open (base.html has a race guard for overlapping `#main-content` swaps). | Sheet cancels any in-flight popup request on close. Page refresh after save uses one `htmx.ajax` call, not a boosted link. |
| File input inside a swapped partial loses Alpine state. | The Alpine component lives inside the partial. Alpine's mutation observer initialises it after the swap. |
| Toast not shown after save. | Refresh of `#main-content` goes through the existing toast extractor. Test asserts the message is in the refreshed response. |
| Move currently redirects to the horse list. | HTMX path never redirects. Non-HTMX path honours a same-origin `next` (horse detail passes its own path), else the horse list as today. |
| Deep links from emails and old bookmarks. | Full pages stay at the same URLs. |
| Keyboard covers Save on small phones. | Sticky footer inside the sheet plus `dvh` height. |
| Users on view-only roles. | Triggers stay behind `feature_access.horses.full`, same as today. |

## 8. Who gains

- **Yard staff on a phone:** one-thumb move and photo from the list, camera opens inside the sheet, no bottom-bar mis-taps.
- **Office on desktop:** flick through the list and fix names, colours, passport numbers in place. Scroll position and filters are kept.
- **View-only users:** nothing changes for them.
- **Everyone:** one consistent pop-up for every small change, and a clear split between profile picture and photo log.
