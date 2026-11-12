# Yardway — barns and stables build plan

A four-phase plan to add barns, numbered stables and day turnout to Yardway.

Written for an AI coding agent. Read all of it before you write any code.

**Revision 1.** Written on 2026-11-12 from a scoping session with the owner. Section 2 holds
the owner's decisions. Section 4 holds the recon answers, checked against the codebase on the
same date.

**Revision 2.** 2026-11-12. The owner confirmed the four points in section 12. More than one
horse can share a stable, with no fixed limit (6.4).

---

## 0. How to use this document

**Work one phase at a time.** Each phase is a separate branch and a separate pull request.

**Stop at the end of each phase.** Report what you built. Wait for approval. Do not start the next phase.

**Each phase ships on its own.** Phase 1 is useful without phase 2. Never build a dependency on a later phase.

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
- Edit forms open in the shared pop-up sheet (`PopupFormMixin`, `is_popup_request`, `popup_saved_response`). Use it for every new form. Do not add a new modal.
- Use `Location.objects.active()` everywhere. Archived locations must never appear in a picker or on a plan.
- Gate every new page, tab and endpoint on the `locations` feature area (`FeatureAccessMixin`, `feature_required`, `feature_access.locations` in templates). View-only roles see the plan but no write controls. Do not add a new feature key.
- The template vocabulary lint (`core/tests/test_vocabulary_and_nav.py`) fails on phrases such as "this field" or "the field" in a template. "Location" is the word for the record. `Fields` as a group heading passes. `Turnout location` is the label for a turnout field.
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

---

## 3. Non-negotiable principles

### 3.1 A barn is a location

A barn is a `Location` row with `kind = 'barn'`. It is not a new top-level model.

Placements, moves, arrivals, departures, pickers, search, the Yard board, archive rules and invoices all work on `Location` today. They must continue to work for a barn with no change to their queries.

### 3.2 The placement is the home, and only the placement sets the price

`Placement.location` says where the horse lives. `Placement.rate_type` says what the owner pays. Stables and turnout are extra records beside the placement.

**No code in `invoicing/` or `billing/` reads a stable or a turnout.** If a change in this plan makes an invoice differ, the change is wrong.

### 3.3 A stable change is not a placement change

A horse that moves from Stable 7 to Stable 9 in the same barn keeps its placement. No placement is closed and none is opened. Only a `StableStay` row changes.

### 3.4 One function keeps the three records in step

A stable stay and a turnout are valid only while the horse has an open placement in the correct kind of location. `Placement.save()` and `Placement.delete()` are the choke point for every placement write (services, forms, Django admin). They call one reconcile function. Do not patch each caller.

### 3.5 Barns have no usage history

Field rules do not apply to a barn. A barn's `usage` is always `horses`. A barn never has a `LocationUsagePeriod` row. An empty barn is empty, not rested.

### 3.6 Never change a rate silently

The app can preselect a rate and must show that it did. The operator confirms with the Save button. There is no server-side rate change that the form did not show.

### 3.7 The plan is drawn, not stored

A stable stores its order in the barn (`position`). The plan is computed from the barn's layout and the ordered stables. Change the layout and the plan redraws. Do not store rows, columns or pixel positions.

### 3.8 The phone gets a list

The plan needs width. Below the `lg` breakpoint, show the stables as a list in number order. The list and the plan read the same context and offer the same actions.

---

## 4. Phase 0 — Recon (done)

Findings from the repository on 2026-11-12.

| # | Question | Answer |
|---|---|---|
| 1 | Where does the code assume a field? | `LocationUsageService` in `core/services.py`. `horses_arrived`, `rest_if_empty`, `undo_auto_rest`, `undo_arrival`, `clear_auto_rest_from` and `align_auto_rest_start` change `Location.usage` when a location fills or empties. `Placement.save()` and `Placement.delete()` call them. |
| 2 | What reads `capacity`? | The ring partial `templates/horses/_capacity_ring.html`, `core/dashboard/board.py`, `core/views/locations.py` (`location_preview`, list view), `core/views/finances.py` (site capacity chart) and `core/admin.py`. All read the stored column. |
| 3 | What decides "this location holds horses"? | `usage in (HORSES, MIXED)` in `board.py`, `finances.py` and `location_preview`. A barn with `usage = horses` passes all three with no change. |
| 4 | Can a move change the rate? | Yes. `MoveHorseForm.new_rate_type` is optional. `PlacementService.move_horse` keeps the current rate when it is empty. The view is `horse_move` in `core/views/horses.py`. |
| 5 | How does the rate type look? | `core.RateType`: `name`, `daily_rate`, `is_active`, plus dated prices in `RatePrice`. It has no link to a kind of location. |
| 6 | Where are locations listed and counted? | `LocationListView` annotates `horse_count` and groups by site. `board.sites_overview` does the same for the dashboard. `finances.py` does it a third time. |
| 7 | What else points at a location? | `billing.FeedOut.location` (feed delivered to a field), `health` foaling forms (`foal_location`), `DashboardPreference.pinned_location`, `LocationUsagePeriod`, `LocationBoundaryHistory`. |
| 8 | Do barns exist in live data already? | Probably. The vocabulary test names "Big Barn" and "Stables Block A" as real locations. Phase 1 must convert an existing location to a barn (section 5.7). |
| 9 | Feature flags | One precedent: `LOCATION_MAPS_ENABLED`, an env setting read through `django-environ` and exposed by a context processor. |
| 10 | Role access | `core/features.py`. The `locations` area description already says "Fields and stables". |

### 4.1 The feature flag

Add to `settings.py`:

```python
BARNS_ENABLED = env.bool('BARNS_ENABLED', default=False)
```

Expose it in the context processor as `barns_enabled`. Document it in `.env.example`.

With the flag off: the location form has no "Barn" option, no barn page renders its plan, and no stable or turnout control shows. Migrations run in every environment regardless of the flag. Existing rows get `kind = 'field'`, so behaviour is the same.

Set the flag on in `test_settings.py`.

---

## 5. Phase 1 — Barns and stables

**Result:** an operator can make a barn, see its plan, and manage its stables. Horses go into a barn as they go into any location. No horse has a stable yet.

### 5.1 Schema

Migration `0056`. All changes are additive.

```python
class Location(models.Model):
    class Kind(models.TextChoices):
        FIELD = 'field', 'Field'
        BARN = 'barn', 'Barn'

    class Layout(models.TextChoices):
        COURTYARD = 'courtyard', 'Courtyard'
        AISLE = 'aisle', 'Centre aisle'
        ROW = 'row', 'Row'

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.FIELD, db_index=True)
    layout = models.CharField(max_length=10, choices=Layout.choices, blank=True, default='')
    # constraint location_layout_only_on_barn: Q(kind='barn') | Q(layout='')


class Stable(models.Model):
    """One numbered stable in a barn."""
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='stables')
    label = models.CharField(max_length=20)           # "7", "Foaling"
    position = models.PositiveSmallIntegerField()     # order in the barn, from 1
    in_use = models.BooleanField(default=True)        # False = out of use
    note = models.CharField(max_length=200, blank=True)
    created_at / updated_at

    # ordering ['location', 'position']
    # unique (location, label); unique (location, position)
```

Add `LocationQuerySet.fields()` and `.barns()`. Add `Location.is_barn`.

`Stable.clean()` refuses a location that is not a barn.

### 5.2 Barn capacity

Keep `Location.capacity` as the one stored number. Every reader in recon row 2 then works with no change.

- A barn **with stables**: `capacity` = the number of stables with `in_use = True`. Write it in one function, `core.stables.sync_barn_capacity(barn)`. Call it after every stable create, delete and `in_use` change. The location form shows the number as read-only text.
- A barn **with no stables**: the operator types the capacity, as for a field. This lets a yard start with a barn and add stables later.

### 5.3 Switch the field rules off for barns

In `LocationUsageService`, return at once for a barn in `_set_usage_auto`, `undo_auto_rest`, `undo_arrival`, `clear_auto_rest_from` and `align_auto_rest_start`. `set_usage` with a manual source raises `ValidationError("A barn has no usage to change.")`.

Then:

- `set_location_usage` view: 404 for a barn. Hide the "Change usage" control on a barn.
- Usage tab of `LocationListView`: fields only.
- `FeedOut` forms in `billing/forms.py`: fields only in the location choices. Hide "Feed Out" on a barn card.
- Foaling forms (`foal_location`): no change. A mare can foal in a barn.
- Location pickers (`get_grouped_location_choices`): no change. Barns show by name under their site.

### 5.4 The plan

A new module, `core/stables.py`, holds pure functions. No ORM calls in the maths.

```python
def plan_cells(layout: str, count: int) -> dict:
    """{'cols': int, 'rows': int,
        'cells': [(row, col), ...],        # one for each position 1..count
        'features': [{'kind': 'yard'|'aisle'|'walkway',
                      'row', 'col', 'row_span', 'col_span'}]}"""
```

Rows and columns start at 1. Position 1 is the first cell.

| Layout | Rule |
|---|---|
| `row` | One line. `cols = count`. Cell *i* = `(1, i)`. A walkway on row 2 across all columns. |
| `aisle` | Two lines that face an aisle. `top = ceil(count / 2)`. `cols = top`. Positions `1..top` = `(1, i)`. The others = `(3, i - top)`. An aisle on row 2 across all columns. |
| `courtyard` | A U shape, numbered clockwise from the top left. `side = count // 3`. `top = count - 2 * side`. `cols = top`. `rows = side + 1`. Positions `1..top` = `(1, i)`. The next `side` go down the right: `(1 + j, top)`. The last `side` go up the left: `(rows - j + 1, 1)`. A yard at `(2, 2)` with `row_span = side` and `col_span = top - 2`. With `count < 5`, use the `row` rule. |

Check: `courtyard` with 20 gives 8 across the top and 6 down each side.

Templates:

- `templates/locations/_barn_plan.html` — a CSS grid from `plan_cells`. One cell for each stable: label, state, and (from phase 2) the horse name. Shown at `lg` and wider. The grid sits in its own `overflow-x: auto` container.
- `templates/locations/_barn_list.html` — the same stables as rows in `position` order. Shown below `lg`.
- A tray below both: **"In this barn, no stable"**, with the horses that have an open placement here and no stable. In phase 1 this is every horse in the barn.

Stable states in phase 1: `empty` and `out of use`. Use the existing tokens. Out of use is a muted hatch. Do not add a new colour.

`LocationDetailView` renders these in place of the usage, rest and feed-out panels when `location.is_barn`.

### 5.5 Make a barn

`LocationForm` gets four fields, shown only when the flag is on:

| Field | Rule |
|---|---|
| `kind` | Field or Barn. On create only. On edit it is read-only text (see 5.7 for conversion). |
| `layout` | Required for a barn. |
| `stable_count` | 0 to 200. Create only. |
| `first_number` | Default 1. Create only. |

For a barn the form hides `usage` and saves `usage = horses`. With `stable_count > 0` it hides `capacity`.

On save, create the stables in one `bulk_create` inside the same transaction: labels `first_number`, `first_number + 1`, ... and positions `1..n`. Then call `sync_barn_capacity`.

### 5.6 Manage stables

All in the pop-up sheet, all on the `locations` feature at full access.

| Action | URL | Rule |
|---|---|---|
| Edit a stable | `locations/<pk>/stables/<stable_pk>/edit/` | `label`, `note`, `in_use`. A duplicate label in the barn is a form error. |
| Add stables | `locations/<pk>/stables/add/` | A count. New stables take the next positions. New labels continue from the highest numeric label. |
| Remove a stable | `locations/<pk>/stables/<stable_pk>/delete/` | Phase 1: delete, then close the gap in `position`. From phase 2: a stable with stay history cannot be deleted. Offer "Set out of use" in the refusal text. |
| Change layout | the location edit form | Redraws the plan. No data changes. |

### 5.7 Convert an existing location to a barn

Recon row 8: live data probably has barns saved as ordinary locations.

Add a "Convert to barn" action on the edit form of a field-kind location. It asks for the layout, the number of stables and the first number. In one transaction it:

1. Sets `kind = 'barn'`, `layout`, `usage = 'horses'`.
2. Closes the open `LocationUsagePeriod` with `end_date = today - 1`. If that period started today, delete it. Past periods stay as history.
3. Creates the stables and calls `sync_barn_capacity`.

Refuse the conversion if the location has a boundary. A drawn field is not a building. There is no barn-to-field conversion in this plan.

### 5.8 The Locations page

In the cards view, when the flag is on and a site has barns **and** fields, show two groups under the site heading: `Barns`, then `Fields`. A site with one kind has no group heading.

A barn card shows the name, the ring, and one line: `20 stables · 3 out of use`. It has no usage badge and no Feed Out action.

The map view: no change in this phase. A barn with a point draws as a circle, as any location with no boundary does.

### 5.9 Definition of done

- [ ] With `BARNS_ENABLED` off, no template or form shows a barn control. The existing test suite passes with no edits.
- [ ] A barn made with 20 stables has `capacity = 20`. Set one stable out of use and it has 19.
- [ ] The last horse leaves a barn. The barn's `usage` stays `horses`. No `LocationUsagePeriod` row exists for the barn.
- [ ] A horse arrives at an empty barn. No `LocationUsagePeriod` row is made.
- [ ] `plan_cells` has a unit test for each layout at counts 1, 4, 5, 6, 7, 20 and 21. Every position gets a different cell. No cell is inside a feature.
- [ ] A converted location keeps its placements and its past usage periods.
- [ ] A role with Locations at view-only sees the plan and no write control. Every new POST refuses that role.
- [ ] The Feed Out form does not list a barn. The Usage tab does not list a barn.
- [ ] The Yard board tile and the finances capacity chart show a barn with the correct numbers, with no code change in `board.py` or `finances.py`.
- [ ] The vocabulary lint passes.

---

## 6. Phase 2 — Horses in stables

**Result:** an operator can put a horse in a stable, move it, and swap two horses. The horse page shows the stable. The move form suggests the rate.

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


class RateType(models.Model):
    class AppliesTo(models.TextChoices):
        ANY = 'any', 'Fields and barns'
        FIELD = 'field', 'Fields'
        BARN = 'barn', 'Barns'

    applies_to = models.CharField(max_length=10, choices=AppliesTo.choices, default=AppliesTo.ANY)
```

**There is no "one open stay for each stable" constraint.** A mare and her foal share a stable, and the app has breeding records. See 6.4.

### 6.2 The service

`core/stables.py` gets `StableService`. Dates follow the placement convention: the old stay ends on `day - 1`, the new stay starts on `day`. Refuse a future date with `_refuse_future_date`.

| Method | Behaviour |
|---|---|
| `assign(horse, stable, day, *, share=False)` | The horse must have an open placement at `stable.location`. The stable must be `in_use`. If the stable has an open stay and `share` is false, raise `ValidationError`. Close the horse's open stay, if any, then open the new one. If the open stay started on `day`, replace it. Do not leave a stay of zero days. |
| `vacate(horse, day)` | Close the open stay. The horse stays in the barn with no stable. |
| `swap(horse_a, horse_b, day)` | The two horses must have open stays in the same barn. One transaction. |
| `reconcile(horse)` | See 6.3. |

All methods run in `transaction.atomic` and lock the horse row with `select_for_update`, as the placement services do.

### 6.3 Keep stays in step with placements

Rule: **a horse's open stay must be in the location of its open placement.**

`reconcile(horse)` enforces it. Call it at the end of `Placement.save()` and `Placement.delete()`:

| Placement event | Stay result |
|---|---|
| Closed (departure, or the first half of a move) | Close the open stay with the placement's `end_date`. |
| Open placement now in a different location (a move, or a correction of an open placement) | Close the open stay on the day before the new placement starts. If that is before the stay's start, delete the stay. |
| Re-opened (a departure undone) | Re-open the stay that ended on the same `end_date`, if its stable is in use and has no open stay. Otherwise the horse returns to the tray. |
| Deleted | If no open placement remains at the stay's barn, delete the open stay. |

`Placement.save()` is called many times in one move. `reconcile` must be safe to call again with no change.

A stable with an open stay cannot be set out of use. The form error says which horse to move first.

### 6.4 More than one horse in one stable

The plan and the list show a stable with an open stay as occupied. When an operator puts a second horse in it, the form shows:

> Stable 7 has Marram in it. Put the two horses in this stable?

with a checkbox that posts `share=1`. The cell then shows each name. This is the only way to make a shared stable.

There is no limit on the number of horses in one stable. For a stable that already has two or more horses, the text names each of them.

### 6.5 Screens

| Screen | Change |
|---|---|
| Barn plan and list | An occupied cell shows the horse name. Tap a cell to open the stable panel (`locations/<pk>/stables/<stable_pk>/`, an HTMX partial): horse, days in the stable, actions. |
| Stable panel, occupied | Move to another stable · Swap with another horse · Take out of the stable · Open horse · Stable history |
| Stable panel, empty | Put a horse here (a picker of the horses in the tray, then all other horses in this barn) · Set out of use |
| Tray | Tap a horse, then tap an empty stable. Also a "Choose a stable" button for each horse, for keyboard and screen-reader use. |
| Horse page | The location line reads `Top Barn · Stable 7`. Change `_location_where.html` and the horse preview pane. Add stable stays to the movement history as their own rows. Do not merge them into placement rows. |
| Move form (`horse_move`) | See 6.6 and 6.7. |
| Location delete blockers | Add stay history to `Location.delete_blockers()`. |

The arrival form for several horses (`ArrivalForm`) gets no stable picker. Horses arrive into the tray. The operator assigns them on the plan.

### 6.6 Stable picker on the move form

`MoveHorseForm` gets `new_stable`, optional. When the location select changes, an `hx-get` to `locations/stable-options/?location=<pk>` returns the picker: the stables in use in that barn, each marked empty or with the horse's name. For a field it returns nothing.

Server-side, `clean()` checks that the stable belongs to `new_location` and is in use. `horse_move` calls `PlacementService.move_horse` and then `StableService.assign` in one transaction.

### 6.7 Rate suggestion

Each rate type says where it applies (`applies_to`, edited on the rate type form in Settings). The default `any` means the rate type never causes a suggestion.

When the location select changes, the same HTMX response carries a rate hint. Compute it in a pure function:

```python
def suggest_rate(horse, current_placement, new_location) -> RateSuggestion | None
```

1. No current placement, or the new location has the same `kind` as the current one: no suggestion.
2. The current rate's `applies_to` is `any` or equals the new kind: no suggestion.
3. Otherwise the current rate is for the other kind. Find a candidate, in this order:
   1. The rate type of this horse's most recent placement in a location of the new kind, if it is active.
   2. The only active rate type with `applies_to` equal to the new kind.
   3. No candidate.

The form then shows a notice above the rate select:

> This horse moves from a field to a barn. It is on **Field care**. Change the rate?

- With a candidate: the select is preset to it, and the notice says `Changed to Box care. Select Field care to keep the current rate.`
- With no candidate: the select is not preset. The notice lists the rate types for the new kind first.

Never preset a rate when the operator has already changed the select in this form.

After the save, the success message names the rate when it changed: `Marram moved to Top Barn. Rate changed to Box care.`

### 6.8 Definition of done

- [ ] `test_placement_lifecycle.py` passes with no edits.
- [ ] A horse with a stable departs. Its stay closes on the departure date. Undo the departure and the stay re-opens.
- [ ] A horse with a stable moves to another barn. The old stay closes the day before the move.
- [ ] A horse moves from Stable 7 to Stable 9 in the same barn. The count of `Placement` rows does not change.
- [ ] Generate an invoice for a horse before and after a stable change in the billing period. The two invoices have the same lines and the same total.
- [ ] A second horse cannot go into an occupied stable without `share`.
- [ ] A stable with a horse in it cannot be set out of use.
- [ ] `suggest_rate` has a unit test for each branch in 6.7.
- [ ] A move in which the operator keeps the current rate saves the current rate.
- [ ] A barn with 22 horses and 20 stables shows `22/20` in red.
- [ ] The query count of the barn page does not grow with the number of stables. Add it to `test_query_counts.py`.

---

## 7. Phase 3 — Day turnout

**Result:** a horse that lives in a barn can have a standing turnout location. The field shows its turnout horses and does not go to "Rested" while it has them.

### 7.1 Schema

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
```

`clean()` refuses a `location` that is a barn.

**This is not a daily log.** There is no "out" or "in" event and no time of day. Do not add them.

### 7.2 Rules

- A horse can have a turnout only while its open placement is in a barn.
- `TurnoutService.start(horse, location, day)`, `.change(horse, location, day)` and `.stop(horse, day)` follow the same date convention as stable stays.
- Extend the reconcile function from 6.3: when the placement closes, or the horse's home becomes a field, close the open turnout on the same date as the stay.
- `Location.archive_blockers()` adds open turnouts.
- `Location.delete_blockers()` adds turnout history.

### 7.3 The field must know it is in use

`LocationUsageService._is_empty(location)` must return false when the location has an open turnout. Then:

- The first turnout onto an empty field calls `horses_arrived`. The field goes to `horses`, as it does for an arrival.
- The last turnout that ends on a field with no residents calls `rest_if_empty`.
- A manual usage on an occupied field (for example `mixed`) is not changed, as now.

### 7.4 Counts

Two numbers for a field: **residents** (open placements) and **turnout horses** (open turnouts).

Add `LocationQuerySet.with_occupancy()`. It annotates `horse_count` (residents, the same rule as today) and `turnout_count`. Use it in `LocationListView`, `board.sites_overview` and `location_preview`, which each write the annotation by hand today.

- The ring on a **field** shows `residents + turnout` against capacity.
- The ring on a **barn** shows residents only.
- Site totals and the finances capacity chart count residents only. A turnout horse is already counted in its barn.

### 7.5 Screens

| Screen | Change |
|---|---|
| Horse page | Below the location line: `Out by day in Back Paddock`. Actions: Set turnout location · Change · Stop turnout. |
| Barn page | "Set turnout" for several horses: select horses on the plan or the list, then choose a location. A cell with a turnout shows a small "out by day" mark. |
| Field page and card | Two groups: `4 horses live here` and `3 horses out by day from Top Barn`. |
| Turnout picker | Fields in the same site first, then the other sites. |

### 7.6 Definition of done

- [ ] A field with no residents and one turnout horse has `usage = horses`.
- [ ] Stop the last turnout on that field. The field goes to `rested` from the next day.
- [ ] A turnout horse departs. The turnout closes on the departure date and the field rests if it is now empty.
- [ ] A horse moves from a barn to a field. Its turnout closes.
- [ ] A field with open turnouts cannot be archived.
- [ ] Site horse totals do not count a turnout horse two times.
- [ ] `test_location_usage.py` passes with no edits.
- [ ] An invoice for a horse is the same before and after a turnout is set.

---

## 8. Phase 4 — The other screens

Small, separate changes. Each can ship alone.

| Screen | Change |
|---|---|
| Yard board | A barn tile gets one line below the ring: `17 in stables · 3 no stable`. Tiles keep their order rule. |
| Map | A barn with a point draws a square marker, not a circle, so the eye can tell a building from a field. The rule "boundary, else point" does not change. |
| Horse list and quick find | Where a location shows for a horse, show `Top Barn · 7`. |
| Search | A query that matches a barn name plus a stable label ("top barn 7") returns the horse in it. |
| CSV export | Two columns on the horse export: `Stable`, `Turnout location`. |
| Data quality (`core/data_quality.py`) | Two checks: an open stay in a location that is not the horse's home, and an open turnout for a horse whose home is not a barn. These must always be empty. |
| Django admin | Register `Stable`, `StableStay` and `Turnout`, read-only for the date fields. |

---

## 9. Out of scope

Do not build these. Each needs a decision from the owner first.

- A record of each day a horse goes out and comes in.
- A drag tool to place stables on the plan.
- A price that depends on the stable.
- "Away today" (a horse at the races keeps its stable).
- A stable door card to print.
- An isolation flag on a stable or a barn.
- Feed and rug notes for each stable.
- A `Site` table.
- CSV import of stables or stays.
- Barn-to-field conversion.

---

## 10. Testing

### 10.1 Pure functions to unit test

- `plan_cells(layout, count)` — phase 1.
- `suggest_rate(horse, current_placement, new_location)` — phase 2.
- Next label for "Add stables" (numeric labels, mixed labels, no stables) — phase 1.

### 10.2 New test files

| File | Phase | Covers |
|---|---|---|
| `core/tests/test_barns.py` | 1 | Make, edit, convert, capacity sync, usage rules off, flag off. |
| `core/tests/test_barn_plan.py` | 1 | `plan_cells`, the plan and list templates, role gating. |
| `core/tests/test_stable_stays.py` | 2 | `StableService`, reconcile with every placement event in 6.3, sharing. |
| `core/tests/test_move_rate_suggestion.py` | 2 | `suggest_rate`, the move form hint, no silent change. |
| `core/tests/test_turnout.py` | 3 | `TurnoutService`, field usage, counts, archive blockers. |

Add to `core/tests/test_feature_enforcement.py` and `test_url_gating.py` for every new URL.

### 10.3 Manual checks for each phase

- Make a barn of each layout with 6, 20 and 21 stables. Look at the plan at 1280 px and the list at 390 px.
- Use the keyboard only: reach a stable, open its panel, assign a horse.
- Phase 2: move a horse field → barn → field and read each success message.
- Phase 3: set a turnout, then open the field page and the Yard board.

---

## 11. Ship order and gates

| Step | Gate |
|---|---|
| Phase 1 merged, flag off in production | CI green. |
| Flag on in production | The owner converts one real barn (5.7) and confirms the plan matches the building. |
| Phase 2 merged | The invoice check in 6.8 is shown in the pull request with real output. |
| Phase 3 merged | The owner confirms the field counts on one real site. |
| Phase 4 | Any order, after phase 2. The turnout column and the turnout data check wait for phase 3. |

---

## 12. Points the owner confirmed

Choices this document made first. The owner confirmed all four on 2026-11-12. Do not reopen them.

1. **More than one horse can share a stable** after a confirmation (6.4). Reason: a mare and her foal. There is no fixed limit.
2. **A location's kind is fixed**, with one conversion: field to barn (5.7). Reason: live data probably has barns saved as ordinary locations.
3. **Several horses that arrive together go to the tray** and get stables on the plan afterwards (6.5). Reason: a stable picker for each horse makes the arrival form long.
4. **A rate type with no "applies to" setting never causes a suggestion** (6.7). Reason: existing yards see no new prompt until they set it.
