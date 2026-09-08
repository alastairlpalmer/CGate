# Yardway UI audit: brand and uniqueness

Date: 2026-09-04
Scope: the Django + Tailwind 3 + Alpine + HTMX front end in `horse_management/`.
Method: read `tailwind.config.js`, `static/css/input.css`, `base.html` and the
main page templates, then rendered 14 pages at desktop (1440px) and phone
(390px) with seeded data.

## 1. The current visual style

Yardway is a dark teal sidebar shell on a cream page, with white cards, 6 to
8px corner radii, `shadow-sm`, and Heroicons outline icons on every control.
The palette (Brim teal, Crown cream, Saddle rust) is well chosen, but on screen
the app is almost one colour: rust appears only in the title underline and one
warning line, so teal does every job (nav, buttons, links, tabs, focus).
The type stack (DM Sans, Source Sans 3, JetBrains Mono) is good, but the scale
is flat: one 24px title, then 14px body and 12px uppercase tracked labels
everywhere, and the fonts fall back to Arial when the Google CDN does not
answer. The result is a tidy Tailwind UI "application shell" that any SaaS
could wear. Nothing in the interface says horses, yards or fields.

What is already strong and must stay: the mobile tab bar and bottom sheet, the
44px touch targets, the pop-up sheet for edits, skeleton loaders, zebra tables,
the sticky form footer, and the toast pipeline. This audit is about identity
and hierarchy, not about rebuilding.

## 2. Where the defaults show

| Default in use | Where | Tell |
|---|---|---|
| Tailwind UI "dark sidebar + cards" shell | `base.html` | The most common admin layout on the web |
| Heroicons outline, stroke 1.5 | every template | The signature of generated Tailwind UIs |
| Pill badge with `ring-1 ring-inset` | `.badge-*` | The exact Tailwind UI / shadcn badge recipe |
| Initial-letter circle avatar | `_horse_avatar.html` | Reads as a contacts app, not a yard |
| Split-screen login with check-circle bullets | `login.html` | Template login page |
| "Good morning, {user}" greeting as the page title | `dashboard.html` | Generic dashboard opener |
| Icon in grey circle + text + button empty state | `empty_state.html` and 6 hand-rolled copies | Default empty state |
| Native `<select>` and file input styling | all forms | Grey gradient selects beside white inputs |
| Raw `red-*` error boxes | login and password reset pages | Bypasses the `error-red` token |
| Raw `amber-*` warning boxes | location, role and bulk health forms | Bypasses the `saddle` and `sand` tokens |
| `gray-*` classes | `invoicing/partials/preview.html`, `settings.html` | Bypasses the palette |

## 3. Twelve opportunities

They are ordered so that each one makes the next one look better. Do them in
this order if you can.

### 3.1 Self-host the fonts and give the fallback a shape

**Problem.** The fonts load from Google Fonts with `media="print" onload`.
When the CDN is slow or blocked, the page renders in Arial with no letter
spacing and no weight contrast. In my render every page fell back. The JS is
already vendored locally for this reason; the fonts are not.

**Why it matters.** Typography is 80% of "does this feel designed". A fallback
to Arial is the single biggest reason the app can look plain.

**Fix.**
1. Put `DMSans-Variable.woff2`, `SourceSans3-Variable.woff2` and
   `JetBrainsMono-Variable.woff2` (latin subsets, about 90 KB total) in
   `static/fonts/`.
2. Declare them in `input.css` with `font-display: swap`.
3. Give the fallback stack metric overrides so the layout does not jump.
4. Remove the two Google Fonts `<link>` tags from `base.html` and
   `registration/_auth_head.html`.

```css
@font-face {
  font-family: "DM Sans";
  src: url("../fonts/DMSans-Variable.woff2") format("woff2");
  font-weight: 400 700; font-display: swap;
}
@font-face {
  font-family: "DM Sans Fallback";
  src: local("Arial");
  size-adjust: 104%; ascent-override: 92%; descent-override: 24%;
}
```

```js
// tailwind.config.js
fontFamily: {
  heading: ['"DM Sans"', '"DM Sans Fallback"', 'system-ui', 'sans-serif'],
  body:    ['"Source Sans 3"', 'system-ui', 'sans-serif'],
  mono:    ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
}
```

### 3.2 Give Saddle rust one job: the primary action

**Problem.** `btn-primary`, the sidebar, active tabs, links, focus rings and
the toggle are all Brim teal. The rust accent exists in the config but only
decorates the title underline.

**Why it matters.** One accent colour with one job is what makes a UI feel
authored. Rust on cream is also the natural equestrian palette (leather, tack),
so it carries the domain without a single icon.

**Fix.**
- `btn-primary`: background Saddle, hover a step darker, focus ring Saddle at
  40%.
- Keep teal for navigation, selected tabs and links.
- Use rust for "needs attention" counts and the active tab bar indicator on
  mobile.
- Add a `saddle-dark: #86431F` token for hover.

```css
.btn-primary { @apply btn bg-saddle text-white hover:bg-saddle-dark
               focus-visible:ring-saddle/40 shadow-sm; }
.btn-success { @apply btn bg-forest text-white hover:bg-forest-light; }
```

Check contrast: `#A0522D` on white is 5.6:1, which passes AA for text.

### 3.3 Build a real type scale

**Problem.** Titles are 24px bold. Everything else is 14px or a 12px uppercase
tracked label. KPI labels, section labels, table headers, filter labels and
detail labels all use the same uppercase treatment, so nothing stands out.

**Why it matters.** Hierarchy is what lets the eye rest. Uppercase tracking
loses its power when it is on every third line.

**Fix.**
- Page title: 30px on desktop, weight 600 not 700, tracking -0.02em.
- KPI value: 36px, weight 500, mono.
- Section title: 15px, weight 600, sentence case.
- Keep uppercase tracking on table headers only. Detail labels (`dt`) and
  filter labels become 13px, weight 500, `text-charcoal-light`, normal case.
- Body: 15px at `lg` and above. 14px is a mobile size.

```css
.page-title   { @apply text-2xl lg:text-3xl font-semibold tracking-[-0.02em] text-charcoal font-heading; }
.kpi-value    { @apply font-mono tabular-nums text-4xl font-medium text-forest mt-2 leading-none; }
.kpi-label    { @apply text-[13px] font-medium text-charcoal-light; }
.section-title{ @apply text-[15px] font-semibold text-forest font-heading; }
.dt-label     { @apply text-[13px] font-medium text-charcoal-light; }
```

Then search the templates for `text-xs font-semibold text-sage uppercase
tracking-wider` (about 40 uses) and replace with `dt-label`.

### 3.4 Three surface levels instead of one card

**Problem.** Every block is the same white card: 1px sage border, `shadow-sm`,
20px padding, cream section header. Tables, KPIs, the horse hero, forms and
empty states all look identical, so the page has no depth.

**Why it matters.** Surface contrast tells the user what is content, what is
a control, and what is a summary, before they read a word.

**Fix.** Define three surfaces and a brand-tinted shadow.
- `card`: white, border, no shadow. For lists, tables and forms.
- `card-raised`: white, tinted shadow, 120ms lift on hover. For KPIs and
  anything the whole card links to.
- `panel`: Crown cream fill (`bg-sand-50`), no border. For the horse hero, the
  dashboard greeting and summary strips.
- Card radius 12px, button radius 6px. The step between them reads as
  deliberate.
- Desktop card padding 24px.

```js
// tailwind.config.js
boxShadow: {
  card:  '0 1px 2px rgba(44,44,44,.04), 0 4px 12px -4px rgba(61,90,99,.12)',
  float: '0 8px 24px -8px rgba(61,90,99,.25)',
},
borderRadius: { btn: '6px', card: '12px', sm: '4px' },
```

```css
.card        { @apply bg-white rounded-card border border-light-sage; }
.card-raised { @apply card shadow-card transition-[transform,box-shadow]
               duration-150 ease-out hover:-translate-y-0.5 hover:shadow-float; }
.panel       { @apply bg-sand-50 rounded-card p-5 lg:p-6; }
```

### 3.5 Fix the form controls that still look like the browser

**Problem.** Native `<select>` elements render with a grey gradient next to
white inputs. The file input shows the browser's "Choose File". In
`horse_form.html`, a `<legend>` inside each `<fieldset>` causes a 40px empty
gap under every heading and draws the section rule through the word
"Identification".

**Why it matters.** Forms are where users spend most of their time. One
browser-default control makes the whole form feel unfinished.

**Fix.**
1. Selects: `appearance-none`, white background, a chevron as a background
   image, `pr-9`.
2. Legend: reset it so it flows like a heading, or replace with `<h3>`.
3. File input: hide the native control and use `btn-secondary` as the label
   with the file name shown beside it (the photo form already does this).
4. Remove the double spacing: labels have `mb-1.5` and inputs have `mt-1`.

```css
select {
  @apply appearance-none bg-white pr-9 bg-no-repeat;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='none' stroke='%234F727D' stroke-width='1.8'><path d='M6 8l4 4 4-4'/></svg>");
  background-position: right .6rem center; background-size: 1rem;
}
fieldset > legend { float: left; width: 100%; padding: 0; margin-bottom: 1rem; }
fieldset > legend + * { clear: both; }
```

### 3.6 Coat-colour avatars and a real hero

**Problem.** A horse with no photo shows a letter in a grey circle. The hero on
the horse page is a white card with a letter tile, the same surface as the
placement card under it.

**Why it matters.** This is the one place the app can be about horses. A
coat-coloured avatar is a memorable, domain-specific detail that costs one
template filter.

**Fix.**
- Add a `coat_colour` filter in `core/templatetags/ui_extras.py` that maps
  `horse.color` to a hex pair (fill, text).
- Avatar fallback: a horse-head glyph (see 3.7) on that fill, not a letter.
- Hero: `panel` surface, 112px avatar with `rounded-2xl`, name at 30px, and a
  "spec strip" under it in mono: age, colour, sex, owner, location, days on
  site.

```python
COAT = {
    'bay': ('#7A4A2B', '#FFFFFF'), 'chestnut': ('#A0522D', '#FFFFFF'),
    'grey': ('#B7B7B7', '#2C2C2C'), 'black': ('#2C2C2C', '#FFFFFF'),
    'palomino': ('#D9B36A', '#2C2C2C'), 'dun': ('#C2A46B', '#2C2C2C'),
    'skewbald': ('#8B5A3C', '#FFFFFF'), 'piebald': ('#4A4A4A', '#FFFFFF'),
}
@register.filter
def coat_colour(code):
    return COAT.get(code, ('#9CB2B8', '#2C2C2C'))
```

```html
{% with c=horse.color|coat_colour %}
<div class="w-10 h-10 rounded-full flex items-center justify-center"
     style="background:{{ c.0 }};color:{{ c.1 }}">
  <svg class="w-5 h-5"><use href="#i-horse"/></svg>
</div>
{% endwith %}
```

### 3.7 Ten domain icons in a sprite

**Problem.** All icons are Heroicons outline. The Horses nav item uses the
"layers" icon. The logo mark is an ellipse with three lines and reads as an
abstract "e".

**Why it matters.** Icons are the most repeated brand element on every screen.
Ten custom glyphs for the domain nouns change the feel more than any colour
change.

**Fix.**
- Draw or license ten glyphs on the same 24px grid and 1.75 stroke: horse
  head, horseshoe, paddock fence, stable door, hay bale, hoof, syringe,
  halter, feed scoop, gate.
- Put them in one `<svg style="display:none">` sprite included once in
  `base.html`, and reference with `<use href="#i-horse">`.
- Use them for nav, KPI cards, quick actions, empty states and the avatar
  fallback. Keep Heroicons for utility actions (edit, close, search).
- Consider a gate for the logo. The repo is called CGate and a gate is what
  every yard has.

```html
<svg class="w-5 h-5 shrink-0" aria-hidden="true"><use href="#i-horse"/></svg>
```

### 3.8 One empty state, with a voice

**Problem.** There are seven different empty states. Most are an icon in a grey
circle, a sentence, and a button. The invoice list shows "Create your first
invoice" even when a filter caused the empty result.

**Why it matters.** Empty states are the first screen a new yard sees. They
set the tone and tell the user what to do next.

**Fix.**
- Use `includes/empty_state.html` everywhere and delete the copies in
  `horse_list`, `owner_list`, `location_list`, `invoice_list`,
  `provider_list` and the table `colspan` rows.
- Add `variant="first"` and `variant="filtered"`. "First" gets a small line
  illustration (paddock fence from the sprite) and the primary action.
  "Filtered" gets the search glyph and a "Clear filters" link.
- Write the copy in the yard's voice: "No horses on the yard yet. Log an
  arrival to start." not "No horses found."

```html
{% include "includes/empty_state.html" with variant="first"
   title="No horses on the yard yet" body="Log an arrival to start."
   add_url=arrive_url add_label="Log arrival" %}
```

### 3.9 Make the dashboard title say what matters

**Problem.** The page title is "Good morning, admin". The useful fact, "2 items
need attention", is a 14px subtitle. The "Resting Locations" widget shows
"0 RESTED / 214 HORSES", which reads as 214 horses. The "Horses on site" KPI
has an empty meta line.

**Why it matters.** The dashboard is opened more than any other page. Its
title should be the yard's state, not a greeting.

**Fix.**
- Title: "2 things need attention" or "All clear on the yard". Subtitle: date
  and greeting.
- Label the resting number as "horse-days".
- Fill the KPI meta: "across 5 locations".
- Show the business name from `BusinessSettings` in the sidebar header and
  the mobile top bar instead of a second "Yardway" wordmark. The product name
  can live in the footer.

### 3.10 Motion that reports change

**Problem.** Motion is a 200ms page fade, a card lift and a button press. When
a form in the pop-up saves, the sheet closes and the page reloads, but the
changed row does not show what changed. There is no global reduced-motion
rule.

**Why it matters.** Micro-feedback is what makes an app feel responsive rather
than "reloaded". It also answers "did that save?" without a toast.

**Fix.**
- Easing tokens: `--ease-out: cubic-bezier(.25,1,.5,1)` for lifts and sheets.
- A `flash` keyframe on the saved row: Crown cream to transparent over 1.2s.
  `popup.js` already knows the saved object; pass its DOM id in the
  `HX-Trigger` payload and add the class after the swap.
- Staggered entrance for dashboard cards: 20ms per card, capped at 10.
- One reduced-motion rule for everything.

```css
@keyframes flash { from { background-color: theme(colors.sand.100); } to { background-color: transparent; } }
.flash { animation: flash 1.2s var(--ease-out) both; }
.stagger > * { animation: page-in .25s var(--ease-out) both; animation-delay: calc(var(--i, 0) * 20ms); }
@media (prefers-reduced-motion: reduce) {
  *, ::before, ::after { animation-duration: .01ms !important; transition-duration: .01ms !important; }
}
```

### 3.11 Tokenise the error and info panels

**Problem.** About 30 forms hand-write `bg-red-50 border border-red-200`
error boxes. `login.html` uses `text-red-700`. The invoice preview uses
`gray-*`. These bypass the `error-red` and `info-blue` tokens and shift hue
from page to page.

**Why it matters.** Small hue drift is what makes a UI feel assembled rather
than designed. It is also the cheapest fix in this list.

**Fix.** Add four alert components and replace the inline classes.

```css
.alert         { @apply rounded-card border p-3 text-sm flex gap-2.5; }
.alert-error   { @apply alert bg-error-red-50 border-error-red/25 text-error-red; }
.alert-info    { @apply alert bg-info-blue-50 border-info-blue/25 text-info-blue; }
.alert-warning { @apply alert bg-sand-50 border-sand-200 text-saddle; }
.alert-success { @apply alert bg-sage-50 border-sage/25 text-forest; }
```

Then run: `grep -rln "bg-red-50" templates` and swap each panel for
`alert-error`. Remove `red`, `gray`, `blue` and `slate` from the Tailwind
`theme.colors` so the build fails on the next leak.

### 3.12 Quieter tables

**Problem.** Row action icons are always visible at 60% grey, so every row has
three grey marks on the right. The list reads as a directory.

**Why it matters.** Hiding actions until hover lowers noise on desktop and
lets the coat-colour avatars and names carry the row.

**Fix.**
- Desktop: `opacity-0 group-hover:opacity-100 group-focus-within:opacity-100`
  on the action cell. Touch: always visible (`max-lg:opacity-100`).
- Right-align money and dates, mono, tabular.
- Add a 6px coat-colour dot before the name in compact lists where an avatar
  is too big.

```html
<td class="text-right">
  <div class="flex justify-end gap-0.5 lg:opacity-0 lg:group-hover:opacity-100
              lg:group-focus-within:opacity-100 transition-opacity duration-100">
```

## 4. Suggested order and effort

| Step | Item | Effort | Files |
|---|---|---|---|
| 1 | 3.1 Self-host fonts | 1 h | `input.css`, `tailwind.config.js`, `base.html`, `_auth_head.html` |
| 2 | 3.2 Rust primary | 30 min | `input.css` |
| 3 | 3.3 Type scale | 2 h | `input.css`, about 40 label replacements |
| 4 | 3.4 Surfaces | 2 h | `input.css`, `tailwind.config.js`, KPI and hero templates |
| 5 | 3.5 Form controls | 1 h | `input.css`, `horse_form.html` |
| 6 | 3.11 Alert tokens | 1 h | `input.css`, 30 templates |
| 7 | 3.6 Coat avatars and hero | 2 h | `ui_extras.py`, `_horse_avatar.html`, `horse_detail.html` |
| 8 | 3.7 Icon sprite | 3 h plus drawing time | `base.html`, nav and KPI templates |
| 9 | 3.8 Empty states | 1.5 h | `empty_state.html`, 6 templates |
| 10 | 3.9 Dashboard title | 1 h | `dashboard.html`, `base.html`, dashboard view |
| 11 | 3.10 Motion | 1.5 h | `input.css`, `popup.js`, `dashboard.html` |
| 12 | 3.12 Tables | 1 h | list templates |

Steps 1 to 6 are token and CSS changes with no template redesign. Together
they shift the feel of every page in about one working day.
