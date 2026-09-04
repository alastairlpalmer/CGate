# Yardway — location mapping build plan

A four-phase plan to add coordinates, location awareness and field boundaries to Yardway.

Written for an AI coding agent. Read all of it before you write any code.

**Revision 2.** Reviewed against the codebase on 2026-09-04. Section 3 holds the recon
answers. Every place where the first draft did not match the code is corrected in place
and listed in section 11.

---

## 0. How to use this document

**Work one phase at a time.** Each phase is a separate branch and a separate pull request.

**Stop at the end of each phase.** Report what you built. Wait for approval. Do not start the next phase.

**Each phase ships on its own.** Phase 1 is useful without phase 2. Phase 3 is useful without phase 4. Never build a dependency on a later phase.

If this document conflicts with the existing codebase, follow the codebase and report the conflict.

**Codebase rules that apply to every phase.**

- All code lives under `horse_management/`. Run every command from that directory.
- Before you push, run the same checks CI runs:

  ```bash
  ruff check .
  python manage.py makemigrations --check --dry-run
  DJANGO_SETTINGS_MODULE=horse_management.test_settings python manage.py test --parallel auto
  ```

- Every page is an HTMX swap of `#main-content` (`hx-boost` on `<body>`). Scripts in `<head>` survive a swap. Scripts inside swapped content run again. So any map or location code must run on both `DOMContentLoaded` and `htmx:afterSwap`, and it must be safe to run twice. Copy the Chart.js lazy loader in `templates/base.html`.
- JS libraries are vendored into `static/js` and served by WhiteNoise with `{% static_v %}` cache busting. Do not load libraries from a CDN.
- Use `Location.objects.active()` everywhere. Archived locations must never appear on a map or in a suggestion.
- Gate every new page, tab and endpoint on the `locations` feature area (`FeatureAccessMixin`, `feature_access.locations` in templates). A role with Locations hidden must see none of this.
- Pass data from Django to JS with `{{ data|json_script:"id" }}`, as `location_detail.html` does.

---

## 1. Product context

Yardway manages horses on farms and yards.

The domain model has two levels:

- **Site** — a farm or property. Example: `Somerford`. **In the code a site is a text value, not a table.** `Location.site` is a `CharField`. The list of sites is the distinct values of that column. The site picker, the dashboard site switch, archive and delete actions, search and grouping all work on that string.
- **Location** — a field, pen, paddock or barn inside a site. Example: `Grain store field`. Model `core.Location`, table `core_location`, integer primary key.

Each location holds horses. Each location has a capacity. The UI already shows a ring badge like `14/12`, meaning 14 horses in a location with a capacity of 12. Over-capacity badges show in red. The badge is the partial `templates/horses/_capacity_ring.html`.

The `Locations` page (`templates/locations/location_list.html`) groups location cards by site under a segmented control with two tabs, `Locations` and `Usage`. Each card shows the name, a usage badge (`Horses`, `Rested`, ...), the first two lines of `description`, the capacity ring, and actions (`View horses`, `Feed Out`, `Edit`). `Edit` opens the form in the shared pop-up sheet.

The home dashboard (`templates/dashboard.html`) already has a **Yard board** widget: one band per site, one tile per location, built by `core/dashboard/board.py`. It has a site switch. The chosen site is saved on `DashboardPreference.site`.

**The problem.** Operators work in the yard on a phone. To record a movement or a treatment they must find the right location by name from a long list. Google Maps links are pasted into the free-text `description` field, where the app shows them as plain text. The app does not know where anything is.

**The goal.** The app should know where each location is. When an operator stands in a field, the app should offer that field. Later, the app should draw the site so operators can see how the fields fit together.

---

## 2. Non-negotiable principles

Follow these in every phase. They are the result of a design review and are not open to reinterpretation.

### 2.1 Never guess the operator's location for them

GPS on a phone is accurate to about 5–20 metres. Adjacent pens and barns are closer together than that.

**The app must never auto-select a location.** It may only suggest one. The operator confirms with a tap.

If the app silently picks the wrong field, an operator records a treatment against the wrong horse group. That corrupts a medical record. This is worse than having no feature.

### 2.2 Circles first, shapes later

A location may have a point (`latitude`, `longitude`) but no boundary polygon. This will be true for most locations for a long time, and permanently for some.

**Every map rendering path must handle both.** Write this rule once, at the start:

```
if (location.boundary) render polygon
else render circle sized by capacity
```

Do not build any map feature that assumes a boundary exists. If you do, phase 4 becomes a rewrite instead of a data change.

### 2.3 One background colour

The map has a single flat background. No satellite imagery. No tile layer. No grid.

Reasons: it matches the existing cream and green interface; it loads instantly; it costs nothing; and it keeps the boundary lines and badges readable.

### 2.4 Colour encodes capacity and nothing else

Use the three states the capacity ring already has. Do not invent a second rule.

| State | Rule in `_capacity_ring.html` | Colour |
|---|---|---|
| Over capacity | `availability <= 0` | `#C0392B`, text `text-error-red` |
| Nearly full | `availability <= 2` | `#3D5A63` |
| Space left | otherwise | `#6A8990` |

Do not colour by grass type, rest status, ownership or anything else. If colour carries several meanings, the red over-capacity signal stops standing out and the feature loses its main value.

### 2.5 Two tap targets per card, not three

On the dashboard card there are exactly two destinations:

1. **The badge** → the horse list for that location.
2. **Everywhere else on the card** → the map tab.

Do not add a third target for "inside the shape but not on the badge". The shapes are too small and too close at card size. Mis-taps will follow.

Badges must be at least 44 × 44 CSS pixels. The ring partial renders at 40 px on the Yard board. On the map, and on the Locations page, use 44 px.

### 2.6 Behaviour splits on accuracy, layout splits on width

There is no desktop path and no mobile path in this feature's logic.

- **Behaviour** is decided by `coords.accuracy`. Over 100 metres means no location answer, whatever the device.
- **Layout** is decided by a CSS breakpoint, as it is everywhere else in the app.

Keep these two apart in the code. Never write a user-agent or screen-width check to decide what location data to use.

This produces the intended result without device detection. A desktop browser locates by wifi or IP, fails the accuracy gate, and falls back to the last opened site with no location highlight. A phone outdoors passes the gate and gets the nearest location. A yard tablet gets whichever is true at that moment, which is correct in both cases and is the case a device check would get wrong.

### 2.7 Never block a render on GPS

The dashboard renders immediately. The location chip mounts asynchronously and appears if and when it has an answer.

### 2.8 Never show a location error

If GPS is unavailable, denied, slow or inaccurate, render nothing. No error message, no empty state, no spinner that outlives the timeout. The dashboard simply looks normal.

An operator in a barn with no signal should see a normal app, not a broken one.

---

## 3. Phase 0 — Recon (done)

Findings from the repository on 2026-09-04.

| # | Question | Answer |
|---|---|---|
| 1 | Framework, language, package manager | Django 5.2, Python 3.11, pip (`horse_management/requirements.txt`). Frontend is Django templates + HTMX + Alpine.js + Tailwind. npm is used only to build the CSS. There is no JS bundler. |
| 2 | Database and migrations | SQLite in dev, PostgreSQL in prod (`DATABASE_URL`). Django migrations. CI fails on a missing migration. |
| 3 | ORM | Django ORM. |
| 4 | Site and Location models | **No Site model exists.** `core.Location` (`core_location`, integer PK) has `name`, `site` (CharField 100), `usage`, `description`, `capacity`, `is_archived`, `archived_at`, timestamps. Sites are distinct `site` strings. `DashboardPreference.site` (CharField, blank = all sites) stores the dashboard's chosen site per user. |
| 5 | Where the Google Maps link lives | `Location.description`, a free-text `TextField`. No dedicated column. Rendered as plain text, not as a link, on both the list and the detail page. |
| 6 | How many locations, how many with a link | Not countable from the repo; this environment has no database. Run the command under this table in the Railway shell. |
| 7 | Tests | Django test runner, 913 tests, in-memory SQLite, migrations skipped (`horse_management/test_settings.py`). CI also runs `ruff check .` and `makemigrations --check`. **There is no JS test runner.** |
| 8 | Feature flags | None. Two related systems exist: per-role feature access (`core/features.py`: hidden / view / full per area) and the per-user dashboard widget registry (`core/dashboard_widgets.py`). Decision: one env setting `LOCATION_MAPS_ENABLED` (see 3.1) plus a registry widget for the dashboard card. |
| 9 | HTTPS | Yes in production (Railway; Vercel at the edge). `SECURE_PROXY_SSL_HEADER` is set. Dev runs on plain `http://127.0.0.1:8000`, which browsers treat as secure. A phone on a LAN IP is **not** a secure context. |
| 10 | Map library | None. No Leaflet, Turf or Mapbox anywhere. |

Command for question 6:

```bash
python manage.py shell -c "from core.models import Location; q=Location.objects.active(); print(q.count(), q.filter(description__iregex=r'goo\.gl|google\.[a-z.]+/maps').count())"
```

### 3.1 The feature flag

Add to `settings.py`, read through `django-environ` like every other setting:

```python
LOCATION_MAPS_ENABLED = env.bool('LOCATION_MAPS_ENABLED', default=False)
```

Expose it in a context processor as `location_maps_enabled`. Gate the map tab, the chip and the dashboard card on it. Default off in every environment until phase 2 is approved. Document it in `.env.example`.

The dashboard card is also a widget in `core/dashboard_widgets.py`, so each user can switch it off in Settings.

---

## 4. Phase 1 — Coordinates

**Goal:** every location has a latitude and longitude. Nothing user-visible changes yet, apart from the edit form.

**Estimate:** 2–3 days.

### 4.1 Schema

Add to `core.Location`:

| Field | Django type | Null | Notes |
|---|---|---|---|
| `latitude` | `DecimalField(max_digits=9, decimal_places=6)` | yes | WGS84. Range −90 to 90. |
| `longitude` | `DecimalField(max_digits=9, decimal_places=6)` | yes | WGS84. Range −180 to 180. |
| `boundary` | `JSONField` | yes | A GeoJSON **geometry** object, not a Feature. Unused until phase 4. |
| `boundary_source` | `CharField(choices=BoundarySource)` | blank | `landapp`, `manual`, `import`. Use a `TextChoices` class like `LocationUsagePeriod.Source`. Unused until phase 4. |
| `boundary_updated_at` | `DateTimeField` | yes | Unused until phase 4. |

**There is no `sites` table. Do not create a Site model in this feature.** Converting `Location.site` to a foreign key touches the site picker, the dashboard site switch, archive, restore and delete actions, search, grouping and the CSV import. That is a separate project.

Instead add one small model in `core/models.py`:

```python
class SiteSettings(models.Model):
    """Per-site values keyed on the site name string used by Location.site."""
    site = models.CharField(max_length=100, unique=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    radius_m = models.PositiveIntegerField(default=1500)  # "on this site" distance
```

Look it up by the site string. Known limit: if someone renames a site by editing each location, this row goes stale. Accept that for now and note it in the model docstring.

Notes:

- `DecimalField(9, 6)` gives about 11 cm of precision. That is more than enough and avoids floating point drift.
- `JSONField` is `jsonb` on PostgreSQL and works on the SQLite test database. Do **not** add PostGIS in this phase. A JSON column and application-side maths are enough until you have spatial queries to run.
- Keep the maps link inside `description`. Do not strip it out. It is the audit trail if the migration goes wrong.

### 4.2 Validation rules

Apply in `Location.clean()`, and also as database `CheckConstraint`s in `Meta.constraints`, so the rule holds for scripts as well as forms:

- `latitude` between −90 and 90.
- `longitude` between −180 and 180.
- Both null, or both set. Never one without the other.
- Reject exactly `0, 0`. This is the null island and always means a parsing failure.
- If `SiteSettings` for the parent site has coordinates, warn (do not block) when the location is more than 10 km from the site centre. This catches a swapped latitude and longitude, which is the most common data entry error. Django forms have no warning level, so queue `messages.warning(...)` in the view after a successful save. The pop-up sheet returns `204` + `HX-Trigger: popup:saved` and the page shows queued messages as toasts, so the warning is visible.

Apply the same rules to `SiteSettings`.

### 4.3 Backfill script for the existing Google Maps links

Write a management command, `core/management/commands/backfill_location_coords.py`. Copy the conventions of `backfill_location_usage.py`: **dry run by default**, `--write` to save, and it must be **idempotent** (skip any location that already has coordinates).

Steps per active location:

1. Extract a URL from `description`. The field is free text — some entries have a prefix like `next to grain store https://maps.app.goo.gl/...`. Use a URL regex, take the first match.
2. If the URL is a `maps.app.goo.gl` or `goo.gl/maps` short link, follow HTTP redirects (maximum 5) to get the full URL. Use `requests` (already a dependency) with a 10 s timeout.
3. Parse coordinates from the resolved URL. Handle these forms, in order:
   - `@<lat>,<lng>,<zoom>z` in the path
   - `?q=<lat>,<lng>` or `&query=<lat>,<lng>`
   - `!3d<lat>!4d<lng>` in the data parameter
4. If no coordinates are found, do not fail the run. Record the location as unresolved.
5. Validate the result against section 4.2 before writing.

Put the parser in its own module, `core/geo.py`, so the edit form (4.4) and the tests share it.

Rate limit the redirect requests. One per second is sufficient and polite.

**Output a report** with three lists: resolved, unresolved, and rejected by validation. Print counts. The unresolved list is the manual work queue.

Expect roughly 10–30% to fail. Short links can expire, and some resolve to a place identifier rather than a coordinate. That is normal. Do not try to make the script perfect.

Run it in production from the Railway shell, the same way `check_analytics` is run.

### 4.4 Coordinate picker in the Edit Location form

The form is `core.forms.LocationForm`, rendered by `templates/locations/location_form.html`, and it opens **inside the pop-up sheet** (`PopupFormMixin`, target `#popup-body`). Everything below must work in that sheet, on a phone.

Add a field group with three input methods. All three write to the same two values.

1. **Paste coordinates.** A single text input accepting `52.1234, -1.2345`. Parse and split. Accept a space or comma separator. Show the parsed values back to the user.
2. **Paste a Google Maps link.** Same parser as the backfill script (`core/geo.py`), run server-side. Show the parsed values back.
3. **Drag a pin.** A small Leaflet map. This one may use a real tile layer, because here the operator needs to recognise the ground. Use OpenStreetMap tiles with the required attribution. Centre it on the `SiteSettings` coordinates if present, otherwise on the last saved location in the same site.

Show the current values as plain text with a clear button. Coordinates must be editable and removable.

**Leaflet.** Vendor `leaflet.js` and `leaflet.css` (and its marker images) into `static/js` and `static/css`. Lazy-load them with a loader in `templates/base.html` that mirrors the Chart.js loader: it watches for a `[data-map]` element on `DOMContentLoaded` and `htmx:afterSwap`, injects the script once, and dispatches `leaflet:ready`. Register the picker as an Alpine component (`Alpine.data('coordPicker', ...)`) in a new `static/js/coord_picker.js`, in the same way `segmented` is registered. After the sheet finishes opening, call `map.invalidateSize()`, or the tiles render at zero width.

Add a `SiteSettings` form on the site header of the Locations page (`Edit site`), with the same three methods and a `radius_m` input. Without it nobody can enter a site centre.

### 4.5 Definition of done

- [ ] Migration applies and rolls back cleanly. `makemigrations --check` passes.
- [ ] Validation rejects out-of-range values, `0,0`, and half-set pairs, both in `clean()` and at the database.
- [ ] Backfill command runs in dry-run mode and prints a report without writing.
- [ ] Backfill command is safe to run twice.
- [ ] The edit form saves coordinates by all three input methods, inside the pop-up sheet, on a phone.
- [ ] A site centre and radius can be set.
- [ ] The `Locations` page is visually unchanged.
- [ ] Unit tests for `core/geo.py` cover all four URL forms plus a malformed input.
- [ ] Leaflet is not loaded on any page without a `[data-map]` element.

Optional, cheap: once coordinates exist, show an `Open in Maps` link on the location card built from `latitude`/`longitude`. Today the pasted link is not clickable.

---

## 5. Phase 2 — Nearest location chip

**Goal:** an operator opening the dashboard on a phone in a field sees a one-tap route to that field.

**Estimate:** 2 days.

**Depends on:** phase 1.

### 5.0 Where the code lives

- `static/js/geo.js` — pure functions: `haversineMetres`, `resolveLadder`, `passesAccuracyGate`, `formatDistance`. Written as a classic browser script that assigns `window.YardwayGeo`, with a `module.exports` guard so Node can import it.
- `static/js/tests/geo.test.js` — tests run with `node --test static/js/tests`. Node is built in; no npm package is needed. Add the step to `.github/workflows/ci.yml` with `actions/setup-node` (Node 22, matching `railpack.json`).
- `static/js/near_you.js` — the Alpine component that owns permissions, position, and the chip.
- Location data: the dashboard view already resolves the site and the Yard board. Add the active locations with coordinates (`pk`, `name`, `site`, `latitude`, `longitude`) and the `SiteSettings` rows to the context, and emit them with `json_script`. No new endpoint. No fetch.

### 5.1 Getting the position

Use the browser geolocation API. Do not add a dependency.

```
navigator.geolocation.getCurrentPosition(onSuccess, onError, {
  enableHighAccuracy: true,
  timeout: 3000,
  maximumAge: 60000
})
```

**Permission handling.** Do not call `getCurrentPosition` on first page load. That triggers a permission prompt with no context, and most people decline it.

Instead:

1. On mount, call `navigator.permissions.query({ name: 'geolocation' })`. This does not prompt.
2. If the state is `granted`, request the position straight away.
3. If the state is `prompt`, render a small quiet button: `Use my location`. Request the position when tapped.
4. If the state is `denied`, render nothing. Do not ask again in this session.
5. If the Permissions API is unavailable, fall back to the button in step 3.

Remember a successful grant in `localStorage` so the button does not reappear. Wrap every `localStorage` call in `try/catch`.

### 5.2 The accuracy gate

The success callback returns `coords.accuracy` in metres.

**If `accuracy > 100`, discard the reading and render nothing.**

This one rule replaces all device detection. Desktop machines locate by wifi or IP address and typically report accuracy in the hundreds or thousands of metres, so they fail the gate automatically. A phone indoors also fails, which is the correct outcome.

Do not write any user-agent or screen-width check for this feature.

### 5.3 Distance calculation

Use the haversine formula. Compute on the client. Write it as a pure, tested function.

```
haversineMetres(lat1, lng1, lat2, lng2) -> number
```

Do not call the server. The locations are in the page (see 5.0).

Only consider active locations that have coordinates. Skip the rest silently.

### 5.4 The distance ladder

Evaluate in order. Stop at the first match.

| Step | Condition | Render |
|---|---|---|
| 1 | Exactly one location within `NEAR_RADIUS_M` | Chip: `<name> · <distance> m` |
| 2 | Two or more within `NEAR_RADIUS_M` | Chip showing the closest, plus a `Not here?` link revealing the next three |
| 3 | Within `radius_m` of any `SiteSettings` centre | Chip: `<site name> · <n> locations` |
| 4 | Anything else | Nothing |

`NEAR_RADIUS_M` defaults to 150. Make it a configuration constant, not a literal. It will need tuning per site later.

Step 3 skips sites with no `SiteSettings` row or no centre, and locations with a blank `site`.

Round the displayed distance. Under 1000 m show whole metres. Above that show one decimal in kilometres.

### 5.5 The "last used" fallback

Store the last opened location id and a timestamp in local storage. Set it from the location detail page and from the horse list when it is filtered to one location.

If the ladder returns nothing, and a location was opened within the last two hours, render a quieter chip: `Back to <name>`.

Use a different visual weight from the GPS chip so the two are not confused. This covers barns with no signal, and phones that stayed in a pocket.

### 5.6 Failure behaviour

| Situation | Behaviour |
|---|---|
| Permission denied | Render nothing. Do not retry this session. |
| Timeout after 3 s | Render nothing. Do not retry automatically. |
| Position error | Render nothing. Log to the console only. |
| Accuracy over 100 m | Render nothing. |
| No locations have coordinates | Render nothing. |
| Not a secure context | Render nothing. `console.warn` once, checked with `window.isSecureContext`. The server cannot know this. |

In every row the answer is the same. There is no error state in this feature.

### 5.7 Chip placement and target

Render the chip in the dashboard header, next to the site switch. It is one `<a>` to `{% url 'horse_list' %}?group_by=location&location=<pk>` — the same target the `View horses` action on the location card uses. Step 3 of the ladder links to `{% url 'dashboard' %}?site=<name>`.

Gate on `location_maps_enabled` and `feature_access.locations`.

### 5.8 Definition of done

- [ ] `haversineMetres` has unit tests, including antimeridian and equator cases, run by `node --test` in CI.
- [ ] The distance ladder has unit tests for all four steps.
- [ ] No permission prompt fires on page load.
- [ ] The chip never appears on a desktop browser.
- [ ] The dashboard renders fully before any location logic resolves.
- [ ] Tapping the chip opens that location's horse list.
- [ ] Turning location services off produces no visible change to the dashboard.
- [ ] The chip survives an `hx-boost` navigation away and back.

---

## 6. Phase 3 — Map tab and dashboard card

**Goal:** operators can see how the site fits together, and can see over-capacity fields at a glance.

**Estimate:** 3–4 days.

**Depends on:** phase 1. Independent of phase 2.

### 6.1 Libraries

**Browser:** Leaflet only, vendored and lazy-loaded as in 4.4. Do not add a tile layer. Create the map, add a `FeatureGroup` of shapes, and call `fitBounds` on that group. Leaflet works fine with no base layer and gives pan and zoom for free.

**Do not add Turf.** Everything Turf was going to do is geometry on the server. Add `shapely` to `requirements.txt` and use it in Python:

| Job | shapely call |
|---|---|
| Badge anchor inside a polygon | `shape(geometry).representative_point()` — same guarantee as `pointOnFeature` |
| Area | `shape(geometry).area` after projecting to metres (see 7.3) |
| Point in polygon | `polygon.contains(Point(lng, lat))` |
| Winding order | `shapely.geometry.polygon.orient(polygon, sign=1.0)` |
| Self-intersection | `polygon.is_valid`, `shapely.validation.explain_validity` |
| Simplify | `polygon.simplify(tolerance)` |

The server sends each location to the browser with a ready-made `anchor: [lat, lng]`. The browser never computes geometry.

### 6.2 One shared renderer

Build one renderer used in both places:

- `core/dashboard/board.py` → add `map_locations(site)` that reuses `sites_overview()` and adds `latitude`, `longitude`, `boundary`, `anchor` and `usage` per tile. It is the **only** place that shapes map data. Do not write a second query.
- `templates/partials/location_map.html` — one partial with a `[data-map]` container, a `json_script` payload, and the badge buttons layered above the map.
- `static/js/location_map.js` — one Alpine component, `Alpine.data('locationMap', ...)`, that reads the payload and draws it.

```
{% include 'partials/location_map.html' with payload=map_payload variant='full' %}
{% include 'partials/location_map.html' with payload=map_payload variant='compact' %}
```

`variant` controls interaction and density only:

| | `full` | `compact` |
|---|---|---|
| Pan and zoom | on | off |
| Location names | shown | nearest only |
| Locations included | all in site | nearest 4 |
| Height | fills tab | fixed, about 200 px |
| Background tap | none | opens map tab |

### 6.3 Rendering rule

For each location:

- If `boundary` is set → render a polygon from the GeoJSON geometry.
- Else if `latitude` and `longitude` are set → render a circle. Scale the radius by capacity, clamped between a minimum and maximum so a 2-horse pen is still tappable and a 40-horse field does not swamp the view.
- Else → do not render it. Do not draw a placeholder.

All three cases share the same badge, the same stroke weight and the same colour rules.

### 6.4 Badge placement

Place each badge at the server-supplied `anchor`. For a polygon that is `representative_point()`, not the centroid. A centroid can fall outside a concave shape. Many real fields are L-shaped or wrap around a barn. For circles the anchor is the centre.

**Collision.** If two badge centres are closer than twice the badge radius at the current zoom, offset the lower-count badge perpendicular to the line between them. Keep this simple. In `compact` variant there are only four locations, so collisions are rare.

### 6.5 Badge content and colour

The badge **is** `templates/horses/_capacity_ring.html`, rendered as a real `<button>` (or `<a>`) at 44 px. Its content and colours are already what the location card and the Yard board show. Do not create a second source of truth.

Follow the Yard board's `holds_horses` rule from `sites_overview()`: a rested, hay or arable field has no capacity ring; it shows the count or the usage badge, exactly as the board tile does. With no capacity set, show `<count>` in `board-count` style.

In this phase also switch the Locations list page from its inline copy of the ring to the partial, so the map is the second user of one rule and not a third copy.

### 6.6 Map tab

Add `Map` as a third item in the segmented control on `location_list.html`, beside `Locations` and `Usage`, reached by `?tab=map`. In `LocationListView.get_context_data`, add `'map'` to the guard that skips card grouping, and build `map_payload` there.

- `Locations` stays the default. Do not change it. Card lists are better for scanning and bulk actions.
- The map tab shows one site at a time. If the user has several sites, add a site selector matching the existing site headers, and default it to `DashboardPreference.site` when that is set.
- Tapping a shape or a badge opens that location.
- Show the site name and the horse count in a header, matching the existing `Somerford · 20 horses` treatment.

**Empty state.** If no location in the site has coordinates, use `includes/empty_state.html` with a short message and a link to the locations list: `Add coordinates to your locations to see them here`. Do not hide the tab. A hidden tab cannot teach anyone that the feature exists.

### 6.7 Dashboard card

Register it in `core/dashboard_widgets.py` as `{"key": "near_you", "name": "Near you", "group": "main", "feature": "locations"}`. It renders above the Yard board. Users can switch it off in Settings like any other zone.

**One slot. One card. Never two.** What changes is how the slot is filled, not how many cards appear.

Resolve two questions independently. Do not merge them into one rule — they compose, and merging them creates conflicts that are hard to reason about.

**Which site does the card show?** First match wins.

1. The site GPS places them on, if the accuracy gate passed
2. `DashboardPreference.site`, the site the dashboard's site switch is set to. This is already stored per user and it already acts as the pinned site. **Do not add a separate site pin.**
3. Their only site, if they have exactly one
4. Nothing — hide the card

**Which location is highlighted inside it?** First match wins.

1. The nearest location by GPS, if the accuracy gate passed
2. A location the user has pinned
3. The location they last opened (local storage, from 5.5)
4. Nothing — show the site with no highlight

Because step 1 of both lists depends on the accuracy gate, a desktop browser lands on "chosen site, no highlight" with no device check. See principle 2.6.

**Pinning a location.** Add `pinned_location = ForeignKey(Location, null=True, on_delete=SET_NULL)` to `DashboardPreference`. Add `Pin to dashboard` / `Unpin` to the location edit form. Show a small pin marker on the dashboard card so the state is visible and reversible.

**Contents.**

- Title row: `Near you`, then the highlighted location name and distance as a chip. With no highlight, show the site name instead.
- Map area: `compact` variant, cropped to the nearest four locations plus padding. With no highlight, fit the whole site.
- Footer row: `See all <n> locations →`.

**Tap behaviour.** Badge opens the horse list. Everything else on the card opens the map tab. Implement the badges as real buttons layered above the map, and put the background handler on the card container. Do not attach a handler to the shapes in this variant.

**When to hide.** If the site question above answers "nothing", hide the whole card. Do not render an empty map.

If phase 2 is not yet built, or the chip has no answer, fall back to `DashboardPreference.site`.

### 6.8 Styling

- One flat background colour behind the shapes. Use the existing page or card surface tone from `static/css/input.css`.
- One stroke weight for every boundary. Do not vary stroke by field type, size or status.
- No shadows, no gradients, no textures.
- Names in the `full` variant only, at a size that stays readable at the default zoom. Hide names below a zoom threshold rather than shrinking them.

### 6.9 Definition of done

- [ ] The same partial and component render both variants.
- [ ] A location with only a point renders as a circle, and it is tappable.
- [ ] A location with a boundary renders as a polygon. Test with a hand-written GeoJSON fixture.
- [ ] Mixed data — some points, some polygons — renders correctly in one view.
- [ ] Badge colours match the existing location cards exactly, because they are the same partial.
- [ ] Every badge is at least 44 × 44 CSS pixels.
- [ ] Leaflet is not loaded on the dashboard until a `[data-map]` element is present.
- [ ] The map tab shows an empty state, not a crash, when no location has coordinates.
- [ ] Tested with a concave polygon to confirm the anchor sits inside the shape (Python test on `representative_point`).
- [ ] The map re-initialises correctly after an `hx-boost` navigation away and back.

---

## 7. Phase 4 — Land App boundary import

**Goal:** a customer uploads their existing field map. Circles become real field shapes.

**Estimate:** 4–5 days. Budget 3 of those for the matching screen.

**Do not start this phase until a real Land App export file is available to test against.** Ask for one before writing code.

### 7.0 Split this phase in two, and run the first half early

**4a — the parser.** A management command, `import_boundaries`, that reads a Land App GeoJSON file and writes boundaries to the database. No upload screen. No matching screen. This is section 7.3 and nothing else. About one day.

**4b — the screens.** Upload, validation feedback, matching and commit. Sections 7.2 and 7.4 to 7.6.

**Run 4a before phase 3.** Import a real export into the development database first. Then build the map against real geometry — real proportions, real concave shapes, real gaps between fields — instead of hand-written fixtures. This will change design decisions about badge placement and label density that fixtures would hide.

4b still depends on phase 3, because the matching screen reuses the map partial for its preview.

### 7.1 Background

Land App is a UK land mapping platform used widely by farms, estates and rural advisers. Users export the plan features they have drawn as GeoJSON, KML, Shapefile or DXF. Many farms did not draw their fields by hand — Land App offers a one-click import of Rural Payments Agency land parcel data, so their boundaries are already the official parcels.

Land App also issues an Integration API key on its Professional tier, which allows a direct data pull. **Do not build against the API in this phase.** File upload works for every customer on any tier and needs no partnership. Revisit the API only if customers ask for automatic updates.

### 7.2 Accepted input

**Phase 4 accepts `.geojson` and `.json` only.**

Reject other formats with a clear message naming GeoJSON and pointing at Land App's export dialog.

Reasons: the GeoJSON standard requires WGS84 latitude and longitude, so there is no coordinate system to detect or convert. A Shapefile from the same source may be in British National Grid, which needs a reprojection step and a new dependency. Add other formats later if asked.

Maximum file size: 10 MB. Reuse the `validate_file_size` pattern in `core/models.py` with the new limit.

### 7.3 Parsing and validation

All of this is Python, in `core/boundary_import.py`, using `shapely`. Never trust a client-side validation of an uploaded file.

Validate in this order. Fail fast with a specific message at each step.

1. **Valid JSON.**
2. **Valid GeoJSON.** Must be a `FeatureCollection` or a `Feature`.
3. **Coordinate range.** Every coordinate must fall within −180 to 180 and −90 to 90. This single check catches a British National Grid file, whose easting and northing values are six-figure numbers. Reject the whole file, and say the file appears to use a projected coordinate system.
4. **Geometry types.** Keep `Polygon` and `MultiPolygon`. Silently discard `Point`, `LineString` and others — Land App plans often contain markers and tracks alongside field boundaries. Report how many were discarded.
5. **Winding order.** Normalise with `shapely.geometry.polygon.orient`. Many exporters ignore the right-hand rule in the GeoJSON specification, and some renderers draw a reversed ring as a hole.
6. **Self-intersection.** `is_valid` and `explain_validity`. Report it. Offer to skip the affected features rather than importing broken geometry.
7. **Vertex count.** If a single polygon has more than 1000 vertices, `simplify` at a tolerance of about `0.00001` degrees, roughly one metre. Report that it was simplified.
8. **Empty result.** If nothing survives, say so plainly and name the likely cause.

**Area.** shapely works in the coordinates it is given, so `.area` on degrees is meaningless. For the matching screen, project to British National Grid first. Use `pyproj` (one more dependency) or, to avoid it, the equal-area approximation: scale longitude by `cos(mean latitude)` and multiply by metres per degree. The approximation is within 1 % at UK latitudes on a single field. Choose one and write it down in the module.

### 7.4 The matching screen

This is the real work in phase 4. Parsing is half a day. This is three.

The uploaded file contains shapes with Land App's own names. Yardway has locations with names like `Jones's mid west - one up from grain store`. They will not match.

Build a two-column screen:

- **Left:** each imported shape. Show its name from the properties if present, its area in acres and hectares, and a small preview.
- **Right:** a dropdown of existing active locations in this site, plus `Create new location` and `Skip`.

**Suggest matches automatically, in this priority order:**

1. **Spatial.** If a location's existing `latitude` and `longitude` fall inside the polygon, that is a near-certain match. `polygon.contains(Point(lng, lat))`. Mark it as a strong suggestion and pre-select it.
2. **Name.** Compare normalised names — lowercase, punctuation stripped, common words like `field` removed. Use token overlap or `difflib.SequenceMatcher`. Pre-select above a confidence threshold, but mark it as weaker.
3. **No suggestion.** Leave it unselected.

Spatial matching is far more reliable than name matching. Order the logic accordingly.

**Preview.** Show all shapes on a map before committing, using the phase 3 partial. The operator should see the site take shape before anything is saved.

### 7.5 Committing the import

For each confirmed match:

- Write the geometry to `boundary`.
- Set `boundary_source` to `landapp`.
- Set `boundary_updated_at`.
- **If the location has no `latitude` and `longitude`, set them from `representative_point()`.** An import should never leave a location worse off.
- If the location already has a boundary, warn before overwriting and record the previous value or an audit entry.

Wrap the whole commit in `transaction.atomic()`. A half-imported farm is confusing to diagnose and annoying to undo.

### 7.6 Attribute handling

Land App uses different plan templates for different schemes — Basic Payment Scheme, Countryside Stewardship, UK Habitat Classification and others. Each template writes different keys into the feature properties.

**Do not depend on any specific property name.** Read the geometry. Look for a name in a small ordered list of likely keys (`name`, `Name`, `title`, `label`, `field_name`) and fall back to `Shape <n>`. Ignore everything else.

### 7.7 Definition of done

- [ ] A real Land App GeoJSON export imports end to end.
- [ ] A file with British National Grid coordinates is rejected with a clear message.
- [ ] A file containing points and lines imports the polygons and reports the discards.
- [ ] A self-intersecting polygon is reported, not silently imported.
- [ ] Spatial matching pre-selects correctly when a location's pin sits inside a polygon.
- [ ] A location with no coordinates gains them from the imported shape.
- [ ] The import is atomic. A failure mid-way leaves no partial data.
- [ ] Fixture files exist in the test suite for each of the above cases.

---

## 8. Out of scope

Do not build any of the following. If one seems necessary, stop and ask.

- PostGIS or any spatial database extension.
- A Site model or a foreign key from Location to a site table.
- A boundary drawing or editing tool.
- Offline map tiles or offline data sync.
- Georeferencing of scanned plans, PDFs or photographs. The coordinates are not in those files. Tracing over imagery is faster and more accurate.
- Snapping between adjacent field boundaries, or shared-edge topology.
- The Land App Integration API.
- Shapefile, KML or DXF import.
- Automatic geofence entry and exit detection, or background location tracking.
- Any horse icon rendered per animal on the map. Badges only.
- Routing, directions or measurement tools.
- Turf.js, or any geometry maths in the browser beyond haversine.

---

## 9. Testing

### 9.1 Pure functions to unit test

Python (`core/tests/`, Django test runner):

- The coordinate parser in `core/geo.py` (all four Google Maps URL forms, plus malformed input)
- Coordinate range validation, `0,0`, half-set pairs
- Badge state selection, through the ring partial
- `map_locations()` shaping: circle vs polygon vs skipped
- `representative_point` anchor on a concave polygon
- GeoJSON validation, each rule separately
- Name normalisation for match suggestions

JavaScript (`static/js/tests/`, `node --test`):

- `haversineMetres`
- The distance ladder resolver
- The accuracy gate
- `formatDistance`

### 9.2 Fixtures to create

Place these in `core/tests/fixtures/geo/` before writing phase 4:

- A valid Land App GeoJSON export with several polygons
- The same data with British National Grid coordinates — must be rejected
- A file mixing polygons, points and lines
- A self-intersecting polygon
- A concave, L-shaped polygon — for the badge placement test
- A `MultiPolygon` with a hole
- An empty `FeatureCollection`
- A 5000-vertex polygon — for the simplification test

### 9.3 Manual checks per phase

Phase 2 and 3 need a device check. A desktop browser cannot prove either one works.

The phone must load the app over **HTTPS**. `http://<lan-ip>:8000` from the dev server is not a secure context and the geolocation API will refuse. Use the Railway deployment, or a tunnel such as `cloudflared` or `ngrok` in front of the dev server.

- Phase 2: on a phone, outdoors, with location on. Then with location off. Then on a desktop browser, where the chip must never appear.
- Phase 3: on a phone in daylight. Confirm badges are readable and tappable with one thumb. Open the edit form from the map and confirm the pin picker renders inside the sheet.

`mobile_qa.py` shows how the project drives Playwright for phone-sized screenshots. Use it for layout checks; it cannot test geolocation.

---

## 10. Ship order and gates

| Phase | Ships what | Gate before continuing |
|---|---|---|
| 0 | A recon report | Done. Section 3. |
| 1 | Coordinates on every location | Backfill report reviewed. Unresolved list is manageable. |
| 2 | Nearest location chip | Used by real operators for one week. If nobody taps it, stop here. |
| 4a | A parser command. No user-facing change. | A real export file is in hand. Real boundaries are in the dev database. |
| 3 | Map tab and dashboard card | Circles look right with no boundaries present, and polygons look right with them |
| 4b | Customer-facing Land App import | Phase 3 map partial is stable |

Phase 2 is the honest gate. If operators do not use the chip, phases 3 and 4 will not save them time either. Two weeks spent is a good outcome compared to two months.

---

## 11. What changed in revision 2

Corrections after reading the codebase. Each one points at the section that now carries it.

1. **No Site table exists.** Section 4.1 replaces the `sites` columns with a `SiteSettings` model keyed on the site name string. Section 8 forbids a Site model.
2. **The maps link is in `description`.** Sections 1 and 4.3 name the field and keep it intact.
3. **The server is Python.** Turf is removed. Sections 6.1 and 7.3 use `shapely`. Badge anchors are computed on the server and sent with the data. The browser needs Leaflet only.
4. **The front end is templates + HTMX + Alpine.** Sections 4.4, 5.0 and 6.2 replace the `<LocationMap>` component with a partial, a `json_script` payload and an Alpine component. Section 0 lists the HTMX swap rules.
5. **The badge exists.** Sections 2.4 and 6.5 point at `_capacity_ring.html` and its three colour states, and move the Locations page onto the partial.
6. **The dashboard site switch already stores a site.** Section 6.7 uses `DashboardPreference.site` and drops the separate site pin. The card becomes a widget in the registry.
7. **The map data comes from `sites_overview()`.** Section 6.2 forbids a second query.
8. **No JS test runner.** Sections 5.0 and 9.1 add `node --test` and a CI step.
9. **Secure context is a browser fact.** Section 5.6 checks `window.isSecureContext` in the browser. Section 9.3 requires HTTPS for the phone check.
10. **Warnings.** Section 4.2 uses `messages.warning` after save, which the pop-up sheet already shows as a toast.
11. **The edit form is a pop-up sheet.** Section 4.4 calls out `invalidateSize` and phone layout.
12. **Archived locations and blank sites.** Section 0 and 5.4 exclude them.
13. **Feature flag.** Section 3.1 defines `LOCATION_MAPS_ENABLED`.
14. **Backfill conventions.** Section 4.3 follows `backfill_location_usage.py`: dry run by default, `--write` to save.
15. **Site centre entry.** Section 4.4 adds an `Edit site` form, without which `SiteSettings` could never be filled.
