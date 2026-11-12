# Yardway — barns and stables build plan

A four-phase plan to add barns, numbered stables and day turnout to Yardway.

Written for an AI coding agent. Read all of it before you write any code.

**Revision 1.** Written on 2026-11-12 from a scoping session with the owner. Section 2 holds
the owner's decisions. Section 4 holds the recon answers, checked against the codebase on the
same date.

**Revision 2.** 2026-11-12. The owner confirmed four open points. More than one horse can
share a stable, with no fixed limit (6.4).

**Revision 3.** 2026-11-12. The plan was reviewed from end to end against the codebase. The
review found seven errors, nine screens the plan missed, and one billing risk. All are
corrected in place and listed in section 13. The owner made four more decisions (section 2,
rows 12 to 15).

---

## 0. How to use this document

**Work one pull request at a time.** Phase 1 is one pull request. Phase 2 is two (2a, 2b). Phase 3 is one. Phase 4 is several small ones.

**Stop at the end of each pull request.** Report what you built. Wait for approval. Do not start the next one.

**Never build a dependency on a later phase.**

If this document conflicts with the existing codebase, follow the codebase and report the conflict.

**Codebase rules that apply to every phase.**

- All code lives under `horse_management/`. Run every command from that directory.
- Before you push, run the same checks CI runs:

  ```bash
  ruff check .
  python manage.py makemigrations --check --dry-run
  python manage.py check
  DJANGO_SETTINGS_MODULE=horse_management.test_settings python manage.py test --parallel auto
  node --test 'static/js/tests/*.test.js'
  ```

- Every page is an HTMX swap of `#main-content` (`hx-boost` on `<body>`). Scripts inside swapped content run again. Any script you add must be safe to run twice.
- Every element with `hx-get` or `hx-post` that swaps a partial must set `hx-select="unset"` and `hx-push-url="false"`. `core/tests/test_htmx_lint.py` fails the build if it does not.
- Edit forms open in the shared pop-up sheet (`PopupFormMixin`, `is_popup_request`, `popup_saved_response`). Use it for every new form. Do not add a new modal.
- Use `Location.objects.active()` everywhere. Archived locations must never appear in a picker or on a plan.
- The template vocabulary lint (`core/tests/test_vocabulary_and_nav.py`) fails on phrases such as "this field" or "the field" in a template. "Location" is the word for the record. `Fields` as a group heading passes. `Turnout location` is the label for a turnout field.
- Tailwind's default colours are off. Use the brand tokens in `tailwind.config.js`. A class that is built at runtime must be listed in `static/css/input.css`.
- A day rate is money. It shows only to roles that can see charges (`core/tests/test_day_rate_visibility.py`). A rate **name** shows to everyone.
- Do not write a migration that edits an older migration. The next number is `0056`.

---

## 1. Product context

Yardway manages horses on farms and yards.

The domain model has two levels today:

- **Site** — a farm or property. In the code a site is a text value, `Location.site`. There is no Site table.
- **Location** — a place inside a site that holds horses. Model `core.Location`. It has a `usage` (Horses, Rested, Hay, ...), a `capacity`, an optional map point and an optional boundary.

A **Placement** puts one horse in one location for a date range, with one owner and one rate type. A horse has at most one open placement. Invoices read placements and nothing else.

**The problem.** The model fits a stud or a livery yard that keeps horses in fields. A racing yard keeps horses in numbered stables, in more than one building. Today such a yard can make a location named "Top Barn" with capacity 20. The app then cannot tell which stable a horse is in, cannot show the building, and treats the barn as a field: an empty barn goes to "Rested".

**The goal.** A site can hold fields, barns, or the two together. A barn shows a plan of its stables. A horse can have a stable. A horse that lives in a barn can have a standing turnout field. A yard that uses only fields sees no change.

---

## 2. Owner decisions

Decided by the owner on 2026-11-12. Do not reopen them.

| # | Question | Decision |
|---|---|---|
| 1 | Words | **Barn** for the building. **Stable** for one unit in it. |
| 2 | Stable and field together | Yes. A horse has one home (the barn) and can have one **standing turnout location**. Staff set it one time. There is no record of each day. |
| 3 | Stable for each horse | Not mandatory. A horse can be in a barn with no stable. |
| 4 | Layouts | Three templates: courtyard, centre aisle, row. No drag tool. |
| 5 | Price | The rate belongs to the horse and its owner (the placement), not to the stable. Typical rate types: "Box care", "Field care". |
| 6 | Rate on a move | When a horse moves between a field and a barn, the move form **suggests** the correct rate. It never changes the rate without the operator. |
| 7 | Capacity | A barn can hold more horses than it has stables. The count shows in red, as it does for any location over capacity. |
| 8 | Shared stable | More than one horse can share a stable, after a confirmation. No fixed limit. Reason: a mare and her foal. |
| 9 | Convert | A current location can be converted to a barn. There is no conversion from a barn to a field. |
| 10 | Group arrivals | Horses that arrive in a group get no stable at first. Staff give them stables on the plan. |
| 11 | Rate suggestion default | A rate type with no "applies to" setting never causes a suggestion. |
| 12 | Turnout land use | Add a land use, **Turnout**, for a field that only turnout horses use. The finance capacity chart does not count it. |
| 13 | Release | Build phases 1 and 2 as separate pull requests. Turn the feature on in production only after phase 2. |
| 14 | Order | The horse list and the farrier page are in phase 2. Turnout is phase 3. |
| 15 | Access | A stable change for a horse needs **Horses** access. Stable set-up (add, rename, out of use) needs **Locations** access. |

---

## 3. Non-negotiable principles

### 3.1 A barn is a location

A barn is a `Location` row with `location_type = 'barn'`. It is not a new top-level model.

Placements, moves, arrivals, departures, pickers, search, the Yard board, archive rules and invoices all work on `Location` today. They must continue to work for a barn.

### 3.2 The placement is the home, and only the placement sets the price

`Placement.location` says where the horse lives. `Placement.rate_type` says what the owner pays. Stables and turnout are extra records beside the placement.

**No code in `invoicing/` or `billing/` reads a stable or a turnout.** If a change in this plan makes an invoice differ, the change is wrong.

### 3.3 A stable change is not a placement change

A horse that moves from Stable 7 to Stable 9 in the same barn keeps its placement. No placement is closed and none is opened. Only a `StableStay` row changes.

### 3.4 A placement change in the same barn is not a stable change

The opposite of 3.3. A rate change or an owner change is saved as a "move" to the same location: the old placement closes and a new one opens the next day. Foaling does this (`health/services.py`, the block that calls `move_horse` with `new_location=mare_placement.location`). The move form does it too.

**The horse must keep its stable through such a change.** The stay stays open, as one row. See 6.3.

### 3.5 One function keeps the records in step

A stable stay and a turnout are valid only while the horse has an open placement in the correct type of location. `Placement.save()` and `Placement.delete()` are the choke point for every placement write (services, forms, Django admin). They call one reconcile function. Do not patch each caller.

### 3.6 Barns have no usage history

Field rules do not apply to a barn. A barn's `usage` is always `horses`. A barn never has a `LocationUsagePeriod` row. An empty barn is empty, not rested.

### 3.7 Never change a rate silently, and never let a wrong rate stay hidden

Two halves:

1. The app can preselect a rate and must show that it did. The operator confirms with the Save button. There is no server-side rate change that the form did not show.
2. A horse on the wrong type of rate shows on the dashboard attention list (7.4). This catches every path that has no suggestion: foaling, a placement edit, the admin site, a form with JavaScript off.

### 3.8 The plan is drawn, not stored

A stable stores its order in the barn (`position`). The plan is computed from the barn's layout and the ordered cells. Change the layout and the plan redraws. Do not store rows, columns or pixel positions.

### 3.9 The phone gets a list

The plan needs width. Below the `lg` breakpoint, show the stables as a list in order. The list and the plan read the same context and offer the same actions.

### 3.10 Access follows the action

| Action | Needs |
|---|---|
| See a barn, its plan and who is in each stable | `locations` at view |
| Make or edit a barn, add or edit stables, set out of use, convert | `locations` at full |
| Put a horse in a stable, move, swap, take out, set or stop turnout | `horses` at full **and** `locations` at view |

This matches the code today: `horse_move`, `horse_arrive` and `horse_depart` are gated on `horses`. Do not add a new feature key.

---

## 4. Phase 0 — Recon (done)

Findings from the repository on 2026-11-12. Rows 11 to 19 were added in revision 3.

| # | Question | Answer |
|---|---|---|
| 1 | Where does the code assume a field? | `LocationUsageService` in `core/services.py`. `horses_arrived`, `rest_if_empty`, `undo_auto_rest`, `undo_arrival`, `clear_auto_rest_from` and `align_auto_rest_start` change `Location.usage` when a location fills or empties. `Placement.save()` and `Placement.delete()` call them. |
| 2 | What reads `capacity`? | The ring partial `templates/horses/_capacity_ring.html`, `core/dashboard/board.py`, `core/views/locations.py` (`location_preview`, list view), `core/views/horses.py` (`_location_group`), `core/views/finances.py` (site capacity chart) and `core/admin.py`. All read the stored column. |
| 3 | What decides "this location holds horses"? | The literal `usage in (HORSES, MIXED)` in three places: `board.py`, `finances.py` and `location_preview`. |
| 4 | Can a move change the rate? | Yes. `MoveHorseForm.new_rate_type` is optional. Empty means "keep the current rate". The field is inside a closed "More options" block in `templates/horses/partials/move_form.html`. The view is `horse_move` in `core/views/horses.py`. |
| 5 | How does the rate type look? | `core.RateType`: `name`, `daily_rate`, `is_active`, plus dated prices in `RatePrice`. It has no link to a type of location. |
| 6 | Where are locations listed and counted? | `LocationListView`, `board.sites_overview`, `finances.py` and `core/views/settings.py` each write the `horse_count` annotation by hand. |
| 7 | What else points at a location? | `billing.FeedOut.location`, `health` foaling and weaning forms (`foal_location`), `DashboardPreference.pinned_location`, `LocationUsagePeriod`, `LocationBoundaryHistory`. |
| 8 | Do barns exist in live data already? | Probably. The vocabulary test names "Big Barn" and "Stables Block A" as real locations. Phase 1 must convert an existing location (5.9). |
| 9 | Feature flags | One precedent: `LOCATION_MAPS_ENABLED`, an env setting read through `django-environ` and exposed by a context processor. Every screen must work with that flag on and off. |
| 10 | Role access | `core/features.py`. Moves are gated on `horses`. Location set-up is gated on `locations`. |
| 11 | Same-location moves | They exist. Foaling changes the mare's rate with `move_horse(mare, new_location=<same>)`. Any operator can do the same on the move form. See 3.4. |
| 12 | Other ways to move a horse | The bulk action form (`health/views.py`, action `move`): many horses, one location, one optional rate. Weaning moves the foal. `PlacementUpdateView` edits a placement's location and dates directly. `cancel_departure` re-opens a closed placement. |
| 13 | Row locks | The placement services use no `select_for_update`. They rely on the constraint `unique_active_placement_per_horse`, and the views catch `IntegrityError`. |
| 14 | The name `kind` | Already used for the map payload (`'polygon'`/`'circle'`), for horse list groups (`'location'`/`'site'`/`'owner'`) and for attention items. Do not use it for the new column. |
| 15 | Board labels | `board.rest_state()` returns `grazing` for any location with horses. The phone map and the preview panel print "Grazing". `circle_radius_m()` sizes a point by capacity: 20 stables gives a 68 m circle. `_ready_neighbours()` offers rested fields near a location. |
| 16 | Location page tabs | `LocationDetailView` has four tabs: `current`, `history`, `feed`, `usage`. The activity feed links to `?tab=feed`. |
| 17 | The horse list | `core/views/horses.py` groups horses by location (`_location_group`), with a capacity ring, a land-use strip (`_usage_strip.html`), a use-case filter and a sort by usage. Rows are `_horse_row.html` (grouped) and `_horse_row_flat.html`. |
| 18 | Where a horse's location prints | `horse_detail.html`, `horses/partials/preview.html`, `horses/partials/quick_view.html`, the two horse row partials, `placements/_movement_table.html`, `farrier/_horse_row.html`, `partials/dashboard/quick_find_results.html`. (`_location_where.html` is the coordinate block of the location form. It is not a horse page partial.) |
| 19 | Undo | `core/views/_undo.py`: a success message can carry an Undo button that posts to a URL. |

### 4.1 The feature flag

Add to `settings.py`:

```python
BARNS_ENABLED = env.bool('BARNS_ENABLED', default=False)
```

Expose it in the context processor as `barns_enabled`. Document it in `.env.example`. Set it on in `test_settings.py`.

With the flag off: the location form has no "Barn" option, no barn page renders its plan, and no stable or turnout control shows. Migrations run in every environment regardless of the flag. Existing rows get `location_type = 'field'`, so behaviour is the same.

**The flag goes on in production only after phase 2b is merged** (decision 13). A plan with no horses in it has no use.

**The flag is temporary.** Remove it in a small pull request when the owner asks, after phase 2 has run in production. A "Barn" option on the location form does no harm to a yard that uses only fields.

---

## 5. Phase 1 — Barns and stables

**Result:** an operator can make a barn, see its plan, and manage its stables. Horses go into a barn as they go into any location. No horse has a stable yet. The flag stays off in production.

### 5.1 Schema

Migration `0056`. All changes are additive.

```python
class Location(models.Model):
    class LocationType(models.TextChoices):
        FIELD = 'field', 'Field'
        BARN = 'barn', 'Barn'

    class Layout(models.TextChoices):
        COURTYARD = 'courtyard', 'Courtyard'
        AISLE = 'aisle', 'Centre aisle'
        ROW = 'row', 'Row'

    location_type = models.CharField(max_length=10, choices=LocationType.choices,
                                     default=LocationType.FIELD, db_index=True)
    layout = models.CharField(max_length=10, choices=Layout.choices, blank=True, default='')
    # constraint location_layout_only_on_barn: Q(location_type='barn') | Q(layout='')


class Stable(models.Model):
    """One cell in a barn: a stable, or a gap such as a tack room."""
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='stables')
    label = models.CharField(max_length=20)           # "7", "Foaling", "Tack room"
    position = models.PositiveSmallIntegerField()     # order in the barn, from 1
    is_gap = models.BooleanField(default=False)       # True = not a stable (tack room, feed room, door)
    in_use = models.BooleanField(default=True)        # False = out of use
    note = models.CharField(max_length=200, blank=True)
    created_at / updated_at

    # ordering ['location', 'position', 'pk']
    # unique (location, label)
    # NO unique rule on position. See below.
```

`position` has no unique constraint. Removing or inserting a cell renumbers the others, and PostgreSQL checks a plain unique rule row by row, so the renumber fails part way. Order by `(position, pk)` and let the plan use the index in that order.

Add `LocationQuerySet.fields()` and `.barns()`. Add `Location.is_barn`. `Stable.clean()` refuses a location that is not a barn, and refuses `in_use = False` on a gap.

Add to `Location`:

```python
RESIDENT_USAGES = (Usage.HORSES, Usage.MIXED)
```

and use it in the three places in recon row 3 in place of the literal tuple. Phase 3 adds to it.

### 5.2 Barn capacity

Keep `Location.capacity` as the one stored number. Every reader in recon row 2 then works with no change.

- A barn **with stables**: `capacity` = the number of cells with `is_gap = False` and `in_use = True`. Write it in one function, `core.stables.sync_barn_capacity(barn)`. Call it after every stable create, delete, `is_gap` change and `in_use` change. The location form shows the number as read-only text.
- A barn **with no stables**: the operator types the capacity, as for a field. This lets a yard start with a barn and add stables later.

### 5.3 Switch the field rules off for barns

In `LocationUsageService`, return at once for a barn in `_set_usage_auto`, `undo_auto_rest`, `undo_arrival`, `clear_auto_rest_from` and `align_auto_rest_start`. `set_usage` with a manual source raises `ValidationError("A barn has no usage to change.")`.

Then:

| Place | Change |
|---|---|
| `set_location_usage` view | 404 for a barn. Hide the "Change usage" control. |
| Usage tab of `LocationListView` | Fields only. |
| `LocationDetailView` tabs | A barn has two tabs: **Stables** (the `current` tab) and **History**. `?tab=feed` and `?tab=usage` on a barn fall back to `current`. |
| `FeedOut` forms in `billing/forms.py` | Fields only in the location choices. Hide "Feed Out" on a barn card. |
| Horse list (`core/views/horses.py`) | A barn group has no land-use strip and no "Change usage" link. Barns stay under the `Horses` use-case filter. |
| Foaling and weaning forms (`foal_location`) | No change. A mare can foal in a barn. |
| Location pickers (`get_grouped_location_choices`) | No change. Barns show by name under their site. |

### 5.4 Board, map and preview

Recon row 15. A barn must not read as grazing land.

- `board.rest_state()` gets one more argument, `is_barn`. For a barn it returns `over`, or a new state `housed` (label "In use", the `grazing` colour) when it has horses, or `empty`. It never returns `rested` or `recovering` for a barn.
- `board.sites_overview()` adds `is_barn` to each tile. `_shape_band()` adds `is_barn` to the map payload. **Do not add a key named `kind` or change the existing one.**
- `circle_radius_m()`: a barn with a point and no boundary draws at a fixed 15 m, whatever its capacity.
- `location_preview` for a barn: no rest chip, no area chip, no "ready neighbours" block. Its horse rows show the stable label from phase 2a.
- `_ready_neighbours()` returns nothing for a barn. Barns are never offered as a neighbour, because they are never `rested`.

Add tests to `core/tests/test_locations_board.py` and `test_location_map.py`.

### 5.5 The plan

A new module, `core/stables.py`, holds pure functions. No ORM calls in the maths.

```python
def plan_cells(layout: str, count: int) -> dict:
    """{'cols': int, 'rows': int,
        'cells': [(row, col), ...],        # one for each cell 1..count, stables and gaps
        'features': [{'kind': 'yard'|'aisle'|'walkway',
                      'row', 'col', 'row_span', 'col_span'}]}"""
```

Rows and columns start at 1. `count` includes gap cells.

| Layout | Rule |
|---|---|
| `row` | Lines of 12. `cols = min(count, 12)`. Cell *i* (from 0) = `(2 * (i // 12) + 1, i % 12 + 1)`. A walkway on the row below each line, across all columns. |
| `aisle` | Two lines that face an aisle. `top = ceil(count / 2)`. `cols = top`. Cells `1..top` = `(1, i)`. The others = `(3, i - top)`. An aisle on row 2 across all columns. |
| `courtyard` | A U shape, numbered clockwise from the top left. `side = min(count // 3, (count - 3) // 2)`. `top = count - 2 * side`. `cols = top`. `rows = side + 1`. Cells `1..top` = `(1, i)`. The next `side` go down the right: `(1 + j, top)`. The last `side` go up the left: `(rows - j + 1, 1)`. A yard at `(2, 2)` with `row_span = side` and `col_span = top - 2`. With `count < 5`, use the `row` rule. |

Checks: `courtyard` with 20 gives 8 across the top and 6 down each side. With 6 it gives 4 across the top and 1 down each side. `top` is never less than 3.

Templates:

- `templates/locations/_barn_plan.html` — a CSS grid from `plan_cells`. Shown at `lg` and wider. The grid sits in its own `overflow-x: auto` container.
- `templates/locations/_barn_list.html` — the same cells as rows in order. Shown below `lg`. A gap is a thin divider row with its label.
- A tray below both: **"In this barn, no stable"**, with the horses that have an open placement here and no stable. In phase 1 this is every horse in the barn.

`LocationDetailView` renders these on the `current` tab when `location.is_barn`.

### 5.6 UI rules for the plan

These apply to phases 1 to 3. Check each one in review.

| Rule | Detail |
|---|---|
| Cells are buttons | Each stable is a real `<button>` (or a link with no JavaScript). `aria-label="Stable 7, Marram"` or `"Stable 7, empty"`. A visible focus ring. |
| Touch size | A cell is 44 × 44 CSS pixels or more. Target 64 wide by 56 high. |
| State is not colour only | Empty = dashed border and the word "Empty". Out of use = hatch and the words "Out of use". Occupied = filled tint and the horse name. A gap = no border, muted small-caps label. |
| Red means over capacity only | The capacity ring keeps its three states. No cell is red. A horse with an attention item gets the saddle-rust dot, as elsewhere in the app. |
| Long names | Cut with an ellipsis in the cell. The full name is in the `title` and in the panel. Two horses in a cell: the first name, then "+1". |
| Brand tokens only | No new colour. The hatch uses `sage-100`. |
| Empty states | A barn with no stables: one line of text and an "Add stables" button. An empty tray is not drawn. |
| Reduced motion | No animation on a cell change when `prefers-reduced-motion` is set. |
| Print | A print stylesheet for the list view: barn name, date, one line for each stable (label, horse, owner). No buttons. |

### 5.7 Make a barn

`LocationForm` gets four fields, shown only when the flag is on:

| Field | Rule |
|---|---|
| `location_type` | Field or Barn. On create only. On edit it is read-only text (see 5.9 for conversion). |
| `layout` | Required for a barn. Three radio cards, each with a small drawing of the shape. |
| `stable_count` | 0 to 200. Create only. |
| `first_number` | Default 1. Create only. |

For a barn the form hides `usage` and saves `usage = horses`. With `stable_count > 0` it hides `capacity`.

On save, create the stables in one `bulk_create` inside the same transaction: labels `first_number`, `first_number + 1`, ... and positions `1..n`. Then call `sync_barn_capacity`.

### 5.8 Manage stables

All in the pop-up sheet. All need `locations` at full access.

| Action | URL | Rule |
|---|---|---|
| Edit a cell | `locations/<pk>/stables/<stable_pk>/edit/` | `label`, `note`, `in_use`. A duplicate label in the barn is a form error. |
| Add stables | `locations/<pk>/stables/add/` | A count. New stables go at the end. New labels continue from the highest numeric label. |
| Add a gap | `locations/<pk>/stables/add-gap/` | A label ("Tack room") and "after which cell". Inserts at that position and renumbers the cells after it with one `bulk_update`. |
| Remove a cell | `locations/<pk>/stables/<stable_pk>/delete/` | Phase 1: delete, then renumber. From phase 2a: a stable with stay history cannot be deleted. The refusal text offers "Set out of use". |
| Change layout | the location edit form | Redraws the plan. No data changes. |

A gap lets the plan match the real building with no drag tool: a tack room in the corner pushes the stables after it one cell on.

### 5.9 Convert an existing location to a barn

Recon row 8: live data probably has barns saved as ordinary locations.

Add a "Convert to barn" action on the edit form of a field-type location. It asks for the layout, the number of stables and the first number. In one transaction it:

1. Sets `location_type = 'barn'`, `layout`, `usage = 'horses'`.
2. Closes the open `LocationUsagePeriod` with `end_date = today - 1`. If that period started today, delete it. Past periods stay as history.
3. Creates the stables and calls `sync_barn_capacity`.

A location with a boundary **can** be converted. The outline stays. The area chip is hidden for a barn (5.4).

There is no barn-to-field conversion in this plan (decision 9). The confirmation text must say so.

### 5.10 Other screens in phase 1

- **Locations page, cards view.** When the flag is on and a site has barns **and** fields, show two groups under the site heading: `Barns`, then `Fields`. A site with one type has no group heading. A barn card shows the name, the ring, and one line: `20 stables · 3 out of use`. It has no usage badge and no Feed Out action. This view must work with `LOCATION_MAPS_ENABLED` off.
- **Settings page** (`core/views/settings.py`, `_location_groups`). Show "Barn" beside a barn's name.
- **Django admin.** `LocationAdmin` gets a `location_type` list filter and a `Stable` inline.
- **`core/features.py`.** The `locations` description becomes "Fields, barns and stables, placements, moves and usage changes."
- **Icon.** Add one barn icon to the sprite that `#i-...` icons come from.

### 5.11 Definition of done

- [ ] With `BARNS_ENABLED` off, no template or form shows a barn control. The existing test suite passes with no edits.
- [ ] A barn made with 20 stables has `capacity = 20`. Set one stable out of use and it has 19. Add a gap and it still has 19.
- [ ] The last horse leaves a barn. The barn's `usage` stays `horses`. No `LocationUsagePeriod` row exists for the barn.
- [ ] A horse arrives at an empty barn. No `LocationUsagePeriod` row is made.
- [ ] `plan_cells` has a unit test for each layout at counts 1, 4, 5, 6, 7, 12, 13, 20, 21 and 30. Every cell gets a different position. No cell is inside a feature. `top >= 3` for every courtyard of 5 or more.
- [ ] Remove a cell from the middle of a 20-cell barn on PostgreSQL. No integrity error.
- [ ] A barn with horses shows "In use", not "Grazing", on the phone map and in the preview panel.
- [ ] A converted location keeps its placements, its past usage periods and its boundary.
- [ ] A role with Locations at view-only sees the plan and no write control. Every new POST refuses that role.
- [ ] The Feed Out form does not list a barn. The Usage tab does not list a barn. `?tab=usage` on a barn shows the plan.
- [ ] The horse list shows a barn group with no land-use strip.
- [ ] The vocabulary lint and the HTMX lint pass.

---

## 6. Phase 2a — Horses in stables

**Result:** an operator can put a horse in a stable, move it, and swap two horses. The horse page and the horse list show the stable.

### 6.1 Schema

```python
class StableStay(models.Model):
    """A horse in a stable for a span of days. Mirrors Placement."""
    horse = models.ForeignKey(Horse, on_delete=models.CASCADE, related_name='stable_stays')
    stable = models.ForeignKey(Stable, on_delete=models.PROTECT, related_name='stays')
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    created_at

    # unique (horse) where end_date is null     -> stablestay_one_open_per_horse
    # check end_date is null or end_date >= start_date
    # index (stable, end_date)
```

**There is no "one open stay for each stable" constraint** (decision 8). `clean()` refuses a stable that is a gap.

### 6.2 The service

`core/stables.py` gets `StableService`. Dates follow the placement convention: the old stay ends on `day - 1`, the new stay starts on `day`.

| Method | Behaviour |
|---|---|
| `assign(horse, stable, day, *, share=False)` | The horse must have an open placement at `stable.location`. The stable must be in use and not a gap. If the stable has an open stay and `share` is false, raise `ValidationError`. Close the horse's open stay, if any, then open the new one. If the open stay started on `day`, replace it. Do not leave a stay of zero days. |
| `vacate(horse, day)` | Close the open stay. The horse stays in the barn with no stable. |
| `swap(horse_a, horse_b, day)` | The two horses must have open stays in the same barn. One transaction. |
| `undo(stay)` | Only for the horse's latest stay, and only if it is open. Delete it and re-open the stay it closed, if that stay ended the day before. Used by the Undo button. |
| `reconcile(horse)` | See 6.3. |

**Dates.** Refuse a future date with `_refuse_future_date`. Refuse a date before the start of the open placement. Refuse a date before the start of the horse's open stay.

**Locks.** The placement services use no row locks (recon row 13), so do not copy a pattern from them. In `assign` and `swap`, inside `transaction.atomic`, lock the target `Stable` row with `select_for_update()` and then read its open stays. This stops two operators from filling one empty stable at the same moment with no warning. The constraint `stablestay_one_open_per_horse` covers two writes for one horse: catch `IntegrityError` in the view and show the same "refresh and check" text that `horse_move` uses.

### 6.3 Keep stays in step with placements

Rule: **a horse's open stay must be in the location of its open placement.**

`reconcile(horse)` enforces it. Call it at the end of `Placement.save()` and `Placement.delete()`:

| Placement event | Stay result |
|---|---|
| Closed (departure, or the first half of any move) | Close the open stay with the placement's `end_date`. |
| Opened in the **same** location, starting the day after a stay ended (3.4: a rate change, an owner change, foaling) | **Re-open that stay.** Set `end_date = None` on the same row. The stable history then shows one stay with no break. |
| Opened in a **different** location | Nothing more to do. The stay closed with the old placement. |
| An open placement corrected to another location | Close the open stay on the day before the placement starts. If that is before the stay's start, delete the stay. |
| Re-opened (`cancel_departure`) | Re-open the stay that ended on the same `end_date`. |
| Start date of an open placement edited to a later date | Move the open stay's start to that date if it is now earlier. |
| Deleted | If no open placement remains at the stay's barn, delete the open stay. |

Two guards on every re-open: the stable must be in use, and if another horse now has an open stay in it, do not re-open. The horse returns to the tray.

`Placement.save()` is called more than one time in a move. `reconcile` must be safe to call again with no change.

A stable with an open stay cannot be set out of use. The form error says which horse to move first.

### 6.4 More than one horse in one stable

The plan and the list show a stable with an open stay as occupied. When an operator puts another horse in it, the form shows:

> Stable 7 has Marram in it. Put the two horses in this stable?

with a checkbox that posts `share=1`. The cell then shows each name. This is the only way an operator makes a shared stable.

There is no limit on the number of horses in one stable. For a stable that already has two or more horses, the text names each of them.

**Foaling.** When the foaling service places the foal in the mare's location (`place_foal`), and the mare has an open stay, call `StableService.assign(foal, mare_stable, foal_dob, share=True)`. No confirmation: a foal at foot is always with the mare. Weaning moves the foal with `move_horse`, and `reconcile` closes its stay.

### 6.5 Stable pickers on the forms

| Form | Change |
|---|---|
| `MoveHorseForm` (`horse_move`) | A `new_stable` field, optional, directly below the location select. Not inside "More options". |
| `SingleArrivalForm` and `NewArrivalForm` (one horse) | The same `stable` field. For a racing yard, "arrive into Stable 7" is the usual flow. |
| `ArrivalForm` (several horses, `log_arrival`) | No picker (decision 10). The horses go to the tray. |
| Bulk action form, action `move` | No picker. The horses go to the tray. |

When the location select changes, an `hx-get` to `locations/stable-options/?location=<pk>` returns the picker: the stables in use in that barn, each marked empty or with the horse's name. For a field it returns nothing. With JavaScript off, the picker is absent and the horse goes to the tray.

Server-side, `clean()` checks that the stable belongs to the chosen location and is in use. The view calls the placement service and then `StableService.assign` in one transaction.

`new_arrival` accepts `?location=<pk>&stable=<pk>` so "Arrive a horse here" on an empty stable can fill the form in.

### 6.6 The barn page

| Part | Change |
|---|---|
| Plan and list | An occupied cell shows the horse name. Tap a cell to open the stable panel (`locations/<pk>/stables/<stable_pk>/`, an HTMX partial). |
| Stable panel, occupied | Move to another stable · Swap with another horse · Take out of the stable · Open horse · Stable history |
| Stable panel, empty | Put a horse here (the horses in the tray first, then the other horses in this barn) · Arrive a horse here · Set out of use |
| Tray | Tap a horse, then tap an empty stable. A bar shows above the plan: **"Marram selected. Tap an empty stable."** with a Cancel button. Each tray horse also has a "Choose a stable" button, for keyboard and screen-reader use. |
| Date | A stable change uses today with one tap. A "Pick a date" chip opens a date input, as the move form does. |
| After a change | Swap only the plan container. The success message carries an **Undo** button (`core/views/_undo.py`) that posts to `stables/stays/<pk>/undo/`. |
| Deep link | `locations/<pk>/?stable=<stable_pk>` opens the page with that cell highlighted and its panel open. |
| Attention | A cell whose horse has an attention item shows the saddle-rust dot. Read the items from the same source the Yard board uses. Do not add a query for each cell. |

### 6.7 The horse page and the horse list

These are in phase 2a because the horse list is the screen staff use most (decision 14).

| Screen | Change |
|---|---|
| Horse page (`horse_detail.html`, `partials/preview.html`, `partials/quick_view.html`) | The location line reads `Top Barn · Stable 7`. "Stable 7" links to the deep link in 6.6. A "Change stable" action sits beside "Move". |
| Horse page timeline | Stable stays are their own rows ("Moved to Stable 9"). Do not merge them into placement rows. |
| Horse list, grouped by location | A barn group gets a **Stable** column, and its default sort is stable order (`position`), then name for the tray horses. The group header shows the ring. No land-use strip (5.3). |
| Horse list, flat rows and other groups | Where the location name prints, print `Top Barn · 7`. |
| Movement table (`placements/_movement_table.html`) | No change. It lists placements. |
| Quick find | A horse result shows `Top Barn · 7`. |
| Location delete blockers | Add stay history to `Location.delete_blockers()`. |

The stable label for a list of horses must come from one annotation or one prefetch, not from a query for each row. `Horse` has no custom queryset today. Add a helper, `core.stables.annotate_stable(queryset)` (a `Subquery` on the open stay), and use it in the horse list, the quick find and the farrier rows.

### 6.8 Data check

Add to `core/data_quality.py`: an open stay whose stable is not in the location of the horse's open placement, or whose horse has no open placement. This must always be empty.

### 6.9 Definition of done

- [ ] `test_placement_lifecycle.py` passes with no edits.
- [ ] A horse with a stable departs. Its stay closes on the departure date. Undo the departure and the stay re-opens.
- [ ] A horse with a stable moves to another barn. The old stay closes the day before the move.
- [ ] **A horse with a stable gets a new rate through the move form, with the same location. It keeps its stable, and it has one `StableStay` row.**
- [ ] **A mare with a stable foals with a rate change and `place_foal`. The mare keeps her stable. The foal has an open stay in the same stable.**
- [ ] A horse moves from Stable 7 to Stable 9 in the same barn. The count of `Placement` rows does not change.
- [ ] Generate an invoice for a horse before and after a stable change in the billing period. The two invoices have the same lines and the same total.
- [ ] A second horse cannot go into an occupied stable without `share`.
- [ ] Two `assign` calls for two horses and one empty stable, run together: the second one fails or asks for `share`. Test with two threads on PostgreSQL, or with a mocked lock on SQLite.
- [ ] A stable with a horse in it cannot be set out of use.
- [ ] A role with Horses at full and Locations at view can change a stable and cannot add one. A role with Locations at full and Horses at view can add a stable and cannot change a horse's stable.
- [ ] Undo after a stable move puts the horse back and leaves no extra row.
- [ ] A barn with 22 horses and 20 stables shows `22/20` in red.
- [ ] The query count of the barn page, and of the horse list grouped by location, does not grow with the number of stables. Add both to `test_query_counts.py`.

---

## 7. Phase 2b — Rates and work lists

**Result:** the app suggests the correct rate when a horse moves between a field and a barn, and shows a horse that is on the wrong type of rate. The farrier page and the activity feed know about stables. **The flag goes on in production after this pull request.**

### 7.1 Schema

```python
class RateType(models.Model):
    class AppliesTo(models.TextChoices):
        ANY = 'any', 'Fields and barns'
        FIELD = 'field', 'Fields'
        BARN = 'barn', 'Barns'

    applies_to = models.CharField(max_length=10, choices=AppliesTo.choices, default=AppliesTo.ANY)
```

Edit it on the rate type form in Settings. The default `any` means the rate type never causes a suggestion and never causes an attention item (decision 11).

Define one helper and use it everywhere in this phase:

```python
def rate_mismatch(rate_type, location) -> bool:
    """True when the rate is for the other type of location."""
    return rate_type.applies_to not in ('any', location.location_type)
```

### 7.2 Rate suggestion on the move form

When the location select changes, the same HTMX response that carries the stable picker (6.5) carries a rate hint. Compute it in a pure function:

```python
def suggest_rate(horse, current_placement, new_location) -> RateSuggestion | None
```

1. No current placement: no suggestion.
2. `rate_mismatch(current rate, new_location)` is false: no suggestion.
3. Otherwise find a candidate, in this order. Each candidate must be active and must not itself be a mismatch for the new location.
   1. The rate type of this horse's most recent placement in a location of the new type.
   2. The rate type that the same owner's other horses have now in locations of the new type, if they all have the same one.
   3. The only active rate type with `applies_to` equal to the new type.
   4. No candidate.

The form then:

- **Opens the "More options" block.** The rate select is inside it and it is closed by default.
- Shows a notice above the rate select:

  > This horse moves from a field to a barn. It is on **Field care**. Change the rate?

- With a candidate: the select is preset to it, and the notice says `Changed to Box care. Select "Keep the current rate" to keep Field care.`
- With no candidate: the select is not preset. The notice lists the rate types for the new type first.

Rules:

- The notice shows rate **names** only. No price, for any role.
- Never preset a rate when the operator has already changed the select in this form.
- After the save, the success message names the rate when it changed: `Marram moved to Top Barn. Rate changed to Box care.`
- If the save leaves a mismatch (the operator kept the current rate, or JavaScript was off), add a second, warning message: `Marram is in a barn on Field care.` Do not block the save.

### 7.3 The other paths

| Path | Change |
|---|---|
| Arrival forms (`horse_arrive`, `new_arrival`, `log_arrival`) | The rate is already a required choice. Order the rate select so the rate types for the chosen location's type come first. If the chosen rate is a mismatch, show the warning message after the save. |
| Bulk action form, action `move` | One rate for all horses. After the location is chosen, show a notice: `6 of these horses are on a field rate.` The operator can set one rate for all, or leave it. After the save, the summary message names the horses that still have a mismatch. |
| Foaling, weaning, `PlacementUpdateView`, Django admin | No suggestion. The attention item in 7.4 catches them. |

### 7.4 Attention item: wrong type of rate

This is the safety net for 3.7. Add one check to `core/dashboard/attention.py`, in the pattern of the checks there:

- **Who:** a horse with an open placement where `rate_mismatch(placement.rate_type, placement.location)` is true.
- **Text:** `In a barn on Field care` or `In a field on Box care`.
- **Action:** a link to the move form for that horse.
- **Who sees it:** roles with `horses` at full. The text has no price.

Add the same condition to `core/data_quality.py` as a count.

### 7.5 Farrier page

`health/farrier.py`, `horse_rows()`: add the stable label and the stable `position` with `Subquery`, as it does for the location name. `templates/farrier/_horse_row.html` prints `Top Barn · 7`.

When the location filter is a barn, order the rows by stable `position`, then name. The farrier walks the barn in that order. With no filter, keep the current order.

### 7.6 Activity feed

`core/dashboard/activity.py` builds entries from placements. Add stable stays: `Moved to Stable 9 in Top Barn`, `Put in Stable 7 in Top Barn`. Batch them as it batches arrivals: several horses assigned in one barn in a short time are one entry.

A placement change in the same location (3.4) must not print "Moved from Top Barn to Top Barn". Print `Rate changed to Box care` when the rate changed, and nothing when only the owner changed.

### 7.7 Definition of done

- [ ] `suggest_rate` has a unit test for each branch in 7.2, and for an inactive candidate.
- [ ] The move form opens "More options" when it shows a suggestion.
- [ ] A move in which the operator keeps the current rate saves the current rate and shows the warning message.
- [ ] A role that cannot see charges gets the suggestion with no price in the response body.
- [ ] A horse placed in a barn on a field rate through the Django admin shows on the attention list.
- [ ] A yard where every rate type is `any` sees no suggestion, no warning and no attention item.
- [ ] The farrier page, filtered to a barn, lists horses in stable order.
- [ ] A rate change at foaling prints no "moved" entry in the activity feed.
- [ ] Generate an invoice for a horse that moved field → barn with a rate change. It has two lines, one for each rate, and the days add up to the period.

---

## 8. Phase 3 — Day turnout

**Result:** a horse that lives in a barn can have a standing turnout location. The field shows its turnout horses and does not go to "Rested" while it has them.

### 8.1 Schema

```python
class Turnout(models.Model):
    """A standing arrangement: this horse goes out in this location."""
    horse = models.ForeignKey(Horse, on_delete=models.CASCADE, related_name='turnouts')
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='turnouts')
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    created_at

    # unique (horse) where end_date is null     -> turnout_one_open_per_horse
    # check end_date is null or end_date >= start_date
    # index (location, end_date)


class Location(models.Model):
    class Usage(models.TextChoices):
        ...
        TURNOUT = 'turnout', 'Turnout'      # new

    RESIDENT_USAGES = (Usage.HORSES, Usage.MIXED)                    # from phase 1
    HORSE_USAGES = (Usage.HORSES, Usage.MIXED, Usage.TURNOUT)        # new
```

`Turnout.clean()` refuses a `location` that is a barn.

**This is not a daily log.** There is no "out" or "in" event and no time of day. Do not add them.

### 8.2 Rules

- A horse can have a turnout only while its open placement is in a barn.
- `TurnoutService.start(horse, location, day)`, `.change(horse, location, day)` and `.stop(horse, day)` follow the same date rules as stable stays (6.2).
- Extend the reconcile function from 6.3. A turnout follows the same table as a stay, with one difference in the test: the horse's home must be **a barn**, not a specific location. So a horse that moves from one barn to another keeps its turnout. A horse whose home becomes a field loses it.
- `Location.archive_blockers()` adds open turnouts. `Location.delete_blockers()` adds turnout history.
- Access: `horses` at full and `locations` at view (3.10).

### 8.3 The Turnout land use

Decision 12. A field that only turnout horses use has the usage `turnout`. This keeps the finance chart correct and gives the usage history a true record.

A **resident** is a horse with an open placement on the field. Residents win over turnout horses.

| Event | Automatic usage change |
|---|---|
| First turnout starts on a field with no residents | → `turnout` |
| A resident arrives on a field whose usage is an automatic `turnout` | → `horses` |
| The last resident leaves, and open turnouts remain | → `turnout`, not `rested` |
| The last turnout ends, and no residents remain | → `rested` from the next day |
| Any event on a field with a manual usage (`mixed`, `hay`, a manual `turnout`) | No change, as now |

In code:

- `LocationUsageService._is_empty(location)` returns false when the location has an open turnout. Add `_has_residents(location)`.
- `horses_arrived` fires when the field was empty **or** its open period is an automatic `turnout`.
- `rest_if_empty` sets `turnout` when residents are gone and turnouts remain.
- All changes go through `_set_usage_auto`, so they have `source = AUTO` and can be undone by the existing undo functions. Extend `undo_auto_rest` and `undo_arrival` tests to cover a `turnout` period before a rest.

Where the usage is read:

| Place | Use |
|---|---|
| `board.py` and `location_preview` ("holds horses", ring shown) | `HORSE_USAGES` |
| `core/views/finances.py` (site capacity chart) | `RESIDENT_USAGES`. A turnout field adds no capacity. |
| `USAGE_COLORS` in `core/views/locations.py` | Add a colour for `turnout` from the brand tokens. |
| Usage tab, horse list use-case filter, `LocationUsageForm` | They read `Usage.choices`. Check that "Turnout" shows and can be chosen by hand. |
| `board.rest_state()` | A field with turnout horses and no residents returns `grazing` with the label "Turnout". |

### 8.4 Counts

Two numbers for a field: **residents** (open placements) and **turnout horses** (open turnouts).

Add `LocationQuerySet.with_occupancy()`. It annotates `horse_count` (residents, the same rule as today) and `turnout_count`. Use it in `LocationListView`, `board.sites_overview`, `location_preview` and `core/views/settings.py`, which each write the annotation by hand today.

- The ring on a **field** shows `residents + turnout` against capacity.
- The ring on a **barn** shows residents only.
- Site totals count residents only. A turnout horse is already counted in its barn.

### 8.5 Screens

| Screen | Change |
|---|---|
| Horse page | Below the location line: `Out by day in Back Paddock`. Actions: Set turnout location · Change · Stop turnout. |
| Barn page | Select several horses on the plan or the list, then **Set turnout** or **Stop turnout** for all of them. A cell with a turnout shows a small "out by day" mark with a text alternative. |
| Field page and card | Two groups: `4 horses live here` and `3 horses out by day from Top Barn`. |
| Horse list, grouped by location | A turnout horse is listed under its barn, with an "out by day" tag. A field group header shows `+3 out by day` beside the ring, so the ring and the rows below it agree. |
| Turnout picker | Fields in the same site first, then the other sites. Barns are not listed. |

### 8.6 Data check

Add to `core/data_quality.py`: an open turnout for a horse whose open placement is not in a barn, or who has no open placement. This must always be empty.

### 8.7 Definition of done

- [ ] A field with no residents and one turnout horse has `usage = turnout`.
- [ ] A resident arrives on that field. It goes to `horses`. The resident leaves. It goes back to `turnout`.
- [ ] Stop the last turnout on a field with no residents. The field goes to `rested` from the next day.
- [ ] A turnout horse departs. The turnout closes on the departure date.
- [ ] A horse moves from one barn to another barn. Its turnout stays open.
- [ ] A horse moves from a barn to a field. Its turnout closes.
- [ ] A field with open turnouts cannot be archived.
- [ ] A site with one 20-stable barn and one turnout field of capacity 6 shows a capacity of 20 on the finance chart.
- [ ] Site horse totals do not count a turnout horse two times.
- [ ] `test_location_usage.py` passes with no edits.
- [ ] An invoice for a horse is the same before and after a turnout is set.

---

## 9. Phase 4 — The other screens

Small, separate changes. Each can ship alone, in any order.

| Screen | Change |
|---|---|
| Yard board | A barn tile gets one line below the ring: `17 in stables · 3 no stable`. Tiles keep their order rule. |
| Map | A barn with a point draws a square marker with the barn icon, not a circle. The rule "boundary, else point" does not change. |
| Search | A query that matches a barn name plus a stable label ("top barn 7") returns the horse in it. |
| CSV export | Two columns on the horse export: `Stable`, `Turnout location`. |
| Django admin | Register `StableStay` and `Turnout`. |
| Product analytics | One event for each of: barn created, stable assigned, turnout set. No horse or owner names in the event. Follow `core/analytics.py`. |

---

## 10. Out of scope

Do not build these. Each needs a decision from the owner first.

- A record of each day a horse goes out and comes in.
- A drag tool to place stables on the plan.
- A price that depends on the stable.
- "Away today" (a horse at the races keeps its stable).
- A stable door card to print. (The plain barn list in 5.6 is in scope.)
- An isolation flag on a stable or a barn.
- Feed and rug notes for each stable.
- A `Site` table.
- CSV import of stables or stays.
- Barn-to-field conversion.
- Audit history for stays. Placements have none today (`core/audit.py` tracks invoices, payments, owners and roles).

---

## 11. Testing

### 11.1 Pure functions to unit test

- `plan_cells(layout, count)` — phase 1.
- Next label for "Add stables" (numeric labels, mixed labels, no stables) — phase 1.
- `rate_mismatch(rate_type, location)` and `suggest_rate(...)` — phase 2b.

### 11.2 New test files

| File | Phase | Covers |
|---|---|---|
| `core/tests/test_barns.py` | 1 | Make, edit, convert, capacity sync, gaps, usage rules off, flag off. |
| `core/tests/test_barn_plan.py` | 1 | `plan_cells`, the plan and list templates, role gating, board labels. |
| `core/tests/test_stable_stays.py` | 2a | `StableService`, every row of the table in 6.3, sharing, foaling, undo, access split. |
| `core/tests/test_move_rate_suggestion.py` | 2b | `suggest_rate`, the move form hint, price hidden, the attention item, bulk move. |
| `core/tests/test_turnout.py` | 3 | `TurnoutService`, every row of the table in 8.3, counts, archive blockers. |

Add every new URL to `core/tests/test_feature_enforcement.py` and `test_url_gating.py`.

The test settings skip migrations. Check each migration by hand against a copy of production data: `python manage.py migrate` then `python manage.py check`, and run the schema check page.

### 11.3 Manual checks

- Make a barn of each layout with 6, 20 and 30 stables. Add a gap. Look at the plan at 1280 px and the list at 390 px.
- Use the keyboard only: reach a stable, open its panel, assign a horse, undo.
- Print the barn list.
- Phase 2b: move a horse field → barn → field and read each message. Do it one time with JavaScript off.
- Phase 3: set a turnout, then open the field page, the horse list and the finance chart.
- Every phase: repeat the main flow with `LOCATION_MAPS_ENABLED` off.

---

## 12. Ship order and gates

| Step | Gate |
|---|---|
| Phase 1 merged. Flag off in production. | CI green. |
| Phase 2a merged. Flag off in production. | The invoice check in 6.9 is shown in the pull request with real output. |
| Phase 2b merged. | The invoice check in 7.7 is shown in the pull request with real output. |
| **Flag on in production.** | The owner sets `applies_to` on the rate types, converts one real barn (5.9), and confirms the plan matches the building. |
| Phase 3 merged. | The owner confirms the field counts and the finance chart on one real site. |
| Phase 4. | Any order, after the flag is on. |
| Flag removed. | The owner asks for it. |

---

## 13. What changed in revision 3

A review of revision 2 against the codebase. Each item is corrected in the section named.

**Errors in revision 2**

| # | Error | Section |
|---|---|---|
| 1 | A rate change or an owner change is a move to the same location. Revision 2 closed the stable stay on every move, so the horse lost its stable. | 3.4, 6.3 |
| 2 | The courtyard rule gave a top row of 2 for 6 stables, with no yard. | 5.5 |
| 3 | A unique rule on `position` made renumbering fail on PostgreSQL. | 5.1 |
| 4 | It said the placement services lock rows. They do not. Two operators could fill one stable with no warning. | 6.2 |
| 5 | It named `_location_where.html` as the horse page location line. That file is the coordinate block of the location form. | 4 (row 18), 6.7 |
| 6 | It said the board code needed no change. A barn showed as "Grazing", drew as a circle up to 70 m, and offered rested fields. | 5.4 |
| 7 | The column name `kind` is used for three other things in the code. | 5.1 (`location_type`) |

**Screens revision 2 missed**

| # | Screen | Section |
|---|---|---|
| 1 | The horse list, grouped by location | 5.3, 6.7, 8.5 |
| 2 | The bulk move | 6.5, 7.3 |
| 3 | The arrival forms for one horse | 6.5 |
| 4 | Foaling: the foal joins the mare's stable | 6.4 |
| 5 | The farrier page | 7.5 |
| 6 | The Feed and Usage tabs of the location page | 5.3 |
| 7 | The Settings page location list | 5.10, 8.4 |
| 8 | The activity feed | 7.6 |
| 9 | Access: moves are gated on `horses`, not `locations` | 3.10 |

**Billing risk**

The rate suggestion covered one form, and the rate field on that form is hidden by default. Foaling, bulk moves, placement edits and the admin site had no check. Section 3.7 adds a second principle, and 7.4 adds an attention item that catches every path.

**Additions**

Gap cells and wrapped rows (5.1, 5.5). UI rules (5.6). Undo, deep links and the selection bar (6.6). A second source for the rate suggestion (7.2). The Turnout land use (8.3). Data checks moved into the phase that makes the data (6.8, 8.6). The flag goes on after phase 2b and is temporary (4.1, 12).
