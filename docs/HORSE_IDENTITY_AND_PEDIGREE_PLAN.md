# Horse identity and pedigree

**Goal:** Make the horse page the record a yard trusts for "which horse is
this, and where does it come from". One card shows the identifiers
(microchip, UELN, passport) and the immediate family tree. Staff fill it
in by hand first. Other sources (documents, licensed data) come later and
use the same review step.

**Target users:** racing yards first (thoroughbreds: studs, pre-training,
training). Polo yards and other horses use the same screens, filled in by
hand.

**Scope:** the `Horse` model, a shared pedigree table, the horse page, the
pop-up edit forms, a spreadsheet import, the foaling flow, and later a
"read it from the document" step.

**Status:** planned. Nothing in this file is built. §4 is a stress test of
the first draft; the rest of the file includes its fixes. Decisions 1, 4
and 5 are agreed (19 Nov 2026, §10).

---

## 0. Summary

- **Phase 1 is all manual**: type it in, scan the chip, go through the
  horses one by one in **Fill in records** (Save and next), import a
  spreadsheet, link out to Weatherbys and Racing Post. No sync.
- **The pedigree shows as a diagram**: a sideways tree with SVG lines,
  drawn by the server from a fixed grid (§8.3). Prototype:
  [Horse Pedigree Card](https://claude.ai/artifact/7UJyTrTVJVvVYaEND1Xs1h).
- **One age rule for all horses**: a year older on 1 January (1 August in
  the southern hemisphere). Stored ages become a foaling year, so no age
  goes out of date.
- **Pedigree records are shared.** Type a stallion's parents once; every
  foal by him shows them. A foal born on the yard gets its tree with no
  typing.
- **Sire, dam and dam's sire come first** on every form. This is the
  racing standard. The other grandparents are optional.
- **One review step for every route except typing.** A spreadsheet, a
  document or a data feed makes *proposed changes*. A person accepts them.
  Nothing overwrites a value that a person typed or checked.
- **Weatherbys and Racing Post have no free public API.** Their data is
  licensed (B2B). Do not scrape their pages. Link to them in phase 1.
  Read their PDFs in phase 2. Licence a feed in phase 3 only if customers
  pay for it.
- **Polo:** names are not unique and pedigrees are often part-known, so
  the app never matches a polo pony by name alone. It accepts "Unknown"
  parents, and the dam can differ from the mare that foaled (embryo
  transfer).

### What the stress test changed

| Change | Why (see §4) |
|--------|--------------|
| Shared `PedigreeHorse` records replace per-horse ancestor copies | Stud yards retyped the same stallion for each foal; spellings drifted; foals inherited nothing |
| Sire / Dam / Dam's sire first | The three names on every racecard, sales page and passport |
| Records editor and spreadsheet import in phase 1 | 50 horses one at a time takes hours. The import's review screen also serves phases 2 and 3 |
| A "proposed changes" step for all non-typed routes | Imports and later syncs must not overwrite typed or checked values |
| Year-only foaling dates; one age rule for all horses | "born 1 Jan 2011" in the screenshot is almost certainly a year only, and a stored age never goes up |
| Chip scanner input and scan-to-check | 15 digits have no check digit; a typo is invisible |
| Phases: 1 manual, 2 documents, 3 licensed feeds | The order you asked for |

---

## 1. What the roadmap asks for

Source: Notion, CGate → Dev board. All cards have the status "ideas".

| Card | What it asks for | Where this plan covers it |
|------|------------------|---------------------------|
| Standard horse identifiers (microchip, UELN, passport) | Fixed internal ID (UUID). A table of identifiers: type, value, issuer, checked yes or no. Microchip: 15 digits, remove spaces. UELN: 15 characters. Warn (do not block) on a duplicate chip. | §6.1, §6.2, §6.6. Phase 1a. |
| Returning horses: find the old record on arrival | New Arrival asks for the chip first and reuses the old record. | §6.4 matching rules. Phase 3 (it uses the phase 1 data). |
| Link C-vet horses to yard horses by microchip | Match `ClientHorse` to `Horse` by chip. | Phase 3. Same matcher. |
| Horse life timeline with a tamper-evident log | Hash-chained events per horse. | §6.7 adds record history. The hash chain stays separate. |
| Advanced record keeping | Weight and height over time. | §6.1 adds the current height only. |
| Sales prep feature | "Is there anything to be done with this?" | A complete identity and pedigree card is the base of a sales sheet. Phase 3. |
| Improve Breeding Section (mare side) / Stallion nominations | Easier breeding entry, stallion details. | §7.4: the covering form suggests stallions from the shared pedigree table. |

---

## 2. What the code has today

`Horse` (`horse_management/core/models.py`, line 716):

| Field | Today | Problem |
|-------|-------|---------|
| `microchip` | Text, 50 chars | No format check, no duplicate check. **Not shown on the horse page, the preview or the quick view.** |
| `passport_number`, `has_passport` | Text and a tick box | No issuer. |
| `sire_name`, `dam_name` | Text | Shown in the header only when filled. |
| `dam` | Link to another `Horse` | Set only by the foaling flow. Not on any form. |
| `breeding` | Free text | Holds "By X out of Y" and whatever the original spreadsheet had. |
| `date_of_birth`, `age` | A date, or a number of years | No way to say "year only". `age` is a fixed number: "15yo" typed in 2026 still says 15 in 2030. |
| UELN, breed, height, markings, country, breeder, registered name | **Not there** | — |
| Record history | **Not there** | `Owner` uses `django-simple-history`; `Horse` does not. |

Other findings:

- **Foaling** (`health/services.py`, `record_foaling`) makes the foal with
  `dam_name = mare.name` (the yard name, not the registered name) and
  `sire_name = stallion_name` (free text typed at the covering).
- **No import screen.** `data/import_csv.py` and
  `core/management/commands/load_csv_data.py` are one-off scripts. The
  first one also reads notes out of the name ("first winter", "no
  passport").
- **Names hold other data.** "Ballerina – 4 white socks" (markings) and
  "Avicii (Karma Avicii)" (yard name and registered name).
- `Document` already has the types Passport and Registration Papers.
- Celery, `requests` and `pillow-heif` are installed. No AI service is.

---

## 3. Racehorses and polo ponies are different

| | Racehorses (thoroughbreds) | Polo ponies |
|---|---|---|
| Stud book | Strong: Weatherbys GSB (GB and IRE), France Galop, The Jockey Club (USA), Stud Book Argentino, … | Weak. Many are thoroughbred or part-thoroughbred. Argentine polo ponies have their own register (sources disagree on who keeps it now). |
| Name | Registered name plus country suffix, for example "Rosie (IRE)". Unique in its stud book. Foals have no name until registered. | Often a stable name only. Not unique. Many ponies share a name. |
| Best identity key | UELN or microchip, then registered name + suffix + year | Microchip, then passport number. Never the name. |
| Pedigree | Known to many generations. Sire, dam and dam's sire are the minimum on every racecard and sales page. | Often part-known: "Sire unknown", "TB mare". |
| Breeding | Natural cover only (thoroughbred stud book rules). The mare that foals is the dam. | Embryo transfer is common. The mare that foals can be a recipient, not the dam. |
| Age | A year older on 1 January (northern hemisphere) or 1 August (southern hemisphere) | No fixed rule. The app uses the racing rule for all horses (decision 4). |
| Passport issuer | Weatherbys (GB and IRE thoroughbreds) | The HPA, other UK issuers, or Argentine papers |
| Public pages to link to | Weatherbys, Racing Post horse profile, sales catalogue page | Few |

---

## 4. Stress test: routes to get data in

Each row is a real case. "Draft" is what the first version of this plan
did. Severity: **H** breaks the feature, **M** costs time or gives wrong
data, **L** small.

### 4.1 Typing it in (phase 1)

| # | Case | Draft | Result | Sev | Fix |
|---|------|-------|--------|-----|-----|
| 1 | A stud has 3 stallions and 40 mares. Staff enter 40 foals. | Each foal stores its own 4 grandparent names | Each stallion's parents typed 13 times or more. "Moonlit Bay", "MOONLIT BAY (FR)", "Moonlit Bay FR" all appear. | **H** | Shared `PedigreeHorse` records (§6.3). Type each stallion once. |
| 2 | A foal is born on the yard. | Foaling sets sire and dam names. Grandparents empty. | Staff must type 4 names that the app already knows. | **H** | Foaling links the foal to the mare's and the stallion's pedigree records. The tree fills itself (§7.4). |
| 3 | Office types a racehorse's pedigree from the racecard. | Six equal boxes | The racecard shows sire, dam and dam's sire. Staff do not know SS, SD, DD. Half the boxes stay empty and the card looks incomplete. | M | Sire, Dam, Dam's sire first. The other three under "More" (§7.2). |
| 4 | Foal name: "Rosie 2026 colt". Registered later as "Copper Rose (GB)". | `registered_name` field | OK. The yard name stays; the registered name is added later. | L | Keep. History records the change. |
| 5 | Foaled 2011, exact date unknown. | Date field only | Staff type 1 Jan 2011. The page says "born 1 Jan 2011" as a fact. | M | `dob_precision`; show "foaled 2011" (§6.1). |
| 6 | The age on the page. | True age from the date, or the stored `age` number | The stored number never goes up. A thoroughbred foaled 10 April shows 2 on 1 March, but it runs as a 3-year-old. | M | One age rule for all horses. Stored ages become an estimated foaling year (§6.1). Decision 4. |
| 7 | Staff type a 15-digit chip on a phone in the yard. | Format check only | A swapped digit passes the check. ISO 11784 has no check digit. | M | Accept input from a chip scanner. "Scan to check" compares a scan with the stored number (§7.1). |
| 8 | A Bluetooth chip scanner types "FDX-B 985 141000123456" and Enter. | Field strips spaces only | Saved with text in it, or the form submits early. | M | Keep digits only. Ignore the Enter key in this field (§6.6). |
| 9 | An imported horse has an old 10-digit chip and a new 15-digit chip. | One microchip field | The old chip is lost. | L | Old chip as a `HorseIdentifier` row. |
| 10 | Someone edits a stallion's parents by mistake. | — (new risk from fix 1) | Every foal by him changes. | M | Show "Used in 14 pedigrees" on edit. Ask again when the record is marked checked. History keeps the old value. |
| 11 | Two different mares called "Rosie" (polo yard). | — (new risk from fix 1) | Name matching joins two different horses. | **H** | Never join by name alone. Suggestions show suffix, year and parents. A person chooses. For non-thoroughbreds, the default is "new record" (§6.4). |
| 12 | Polo pony with "Sire unknown". | Empty box | Shows as "missing" for ever. | L | "Unknown" tick box for each parent. |
| 13 | Embryo transfer foal: the donor mare is the dam, a recipient mare foaled it. | Foaling sets dam = the mare that foaled | Wrong dam in the pedigree. | M | Foaling sets the dam as a default only. Staff can change it. A donor-mare field on the breeding record comes later (decision 6). |
| 14 | The microchip is corrected after an ID check. | Stamp stays | The stamp says "checked" for a number nobody checked. | M | A change to chip, UELN or passport number clears the stamp. |

### 4.2 Bulk and existing data (phase 1)

| # | Case | Draft | Result | Sev | Fix |
|---|------|-------|--------|-----|-----|
| 15 | A new customer brings 60 horses on a spreadsheet. | Type each one | 2 to 3 hours of typing. The customer gives up. | **H** | Records editor (§7.5) and spreadsheet import with a review screen (§7.6). |
| 16 | The import has a horse that is already on the system. | — | A second record for the same horse. | **H** | Match by chip, then UELN, then registered name + suffix + year. Name alone only suggests (§6.4). |
| 17 | The import has a value that differs from a typed value. | — | The import overwrites the typed value. | **H** | Proposed changes (§6.5). Empty fields are pre-ticked; differences are not. |
| 18 | Existing `breeding` text such as "By X out of Y" or "X x Y". | Ignored | Data the yard already has stays unused. | M | Back-fill proposals from the text (§7.7). |
| 19 | Existing names like "Avicii (Karma Avicii)". | Clean-up command | OK. | L | Keep, as proposals in the same review screen. |

### 4.3 Documents and feeds (phases 2 and 3)

| # | Case | Draft | Result | Sev | Fix |
|---|------|-------|--------|-----|-----|
| 20 | A horse arrives from a sale with its catalogue page. | Not listed | A good source is not used. | M | Phase 2 reads sales catalogue pages too. They have the pedigree but no chip. |
| 21 | The app reads a Weatherbys PDF with 3 generations and the dam line (many names). | Fields by name | A name from the dam line goes into the wrong box. | M | The schema asks for each box by position. The review screen shows the tree beside the document. |
| 22 | Older passport with hand-written markings; Argentine papers in Spanish. | Not tested | Lower accuracy. | M | The test set must include hand-written and Spanish papers. |
| 23 | A licensed feed sends a different spelling for a typed name. | — | Duplicate pedigree records. | M | Feeds match on UELN or name + suffix + year, and go through proposals. |
| 24 | Someone uses a third-party "Racing Post API" wrapper. | — | It is probably against Racing Post's terms. | **H** | Use only the official B2B licence. Link to public pages; do not copy from them. |

### 4.4 Records editor (added after decision 5)

The editor is a new route, so it gets the same test. A spreadsheet-style
grid was tested first. Most of its risks came from pasting many cells at
once (values replaced with no warning, partial saves of many rows). The
plan now uses a simpler editor: one horse at a time, with **Save and
next** (§7.5). These are the cases that remain.

| # | Case | Result without a guard | Sev | Fix (§7.5) |
|---|------|------------------------|-----|------------|
| 25 | In a long session, staff save a value on the wrong horse. | Wrong data on a horse, found late. | L | The form header shows the horse's photo and both names. After each save: "Saved Copper Rose. Undo" for 30 seconds. |
| 26 | Two people edit the same horse at the same time. | The last save wins; the first change is lost with no message. | M | The form carries the record's version. A save on a changed record stops and shows who changed it. |
| 27 | A typed sire name matches two pedigree records. | The editor joins the wrong one. | **H** | The field shows both, with suffix, year and parents. The form does not save until a person chooses one or "New". |
| 28 | Typos in shared pedigree records ("Moonlit Bay" and "Moonlight Bay"). | Two records for one stallion; family links split. | M | The Pedigree records list has a "Possible duplicates" filter and a **Merge** action. |
| 29 | Someone merges the wrong two records. | Two different horses become one. | M | Merge shows both records and their trees side by side before confirming. History stores each moved link, so the merge can be reversed. |

### 4.5 Result

The first draft worked for a livery yard with a few horses. It failed for
a stud or a racing yard (cases 1, 2, 15, 16, 17) and for polo names (case
11). The editor's main risk is a wrong pedigree match (case 27), which
needs its guard from day one. §6 and §7 below include the fixes.

---

## 5. What outside sources can give us

Facts come from web search results. This container cannot open
weatherbys.co.uk, so confirm the Weatherbys details with Weatherbys before
phase 3.

| Source | What it has | Access | Use |
|--------|-------------|--------|-----|
| Passport (any PIO) | Chip, UELN, markings, foaling date, breeder; sire, dam and dam's sire for registered breeds | The yard holds it | Phase 1: store the photo as a `Document`. Phase 2: read it. |
| Weatherbys (GSB) | Stud book, passports, pedigrees | GSB Online and the Pedigree Portal are account services. Data feeds are licensed. No public developer API. | Phase 1: link. Phase 2: read its PDFs. Phase 3: licence, only if customers pay. |
| Racing Post | Horse profile with form, sales and pedigree | Public pages. The API is B2B only, by arrangement. | Phase 1: link to the horse profile. Phase 3: licence, if needed. |
| Sales catalogue pages (Tattersalls, Goffs, …) | Pedigree and dam line for a lot | Public PDFs | Phase 2: read. |
| UK National ChipChecker (Central Equine Database) | Is the chip on record, with which PIO | Public check by hand | Phase 1: "Check on ChipChecker ↗" copies the chip and opens the site. |
| Irish equine chip checker | Is a chip or UELN valid | Public check by hand | Phase 1: same, for Irish horses. |
| Stud Book Argentino | Public profiles for Argentine thoroughbreds | Public pages | Phase 1: link, for polo and racing imports. |

Never scrape any of these. Their terms forbid it or the pages need a
sign-in, and a scraper breaks each time a site changes.

---

## 6. Data model

### 6.1 New fields on `Horse`

| Field | Type | Rule |
|-------|------|------|
| `uid` | `UUIDField(unique=True, editable=False)` | Fixed internal ID (roadmap). Three-step migration: add as null, fill, make unique. |
| `registered_name` | Text, 200, blank | The name in the passport or stud book. `name` stays the yard name. |
| `country_of_birth` | Text, 3, blank | Stud-book suffix: GB, IRE, FR, USA, GER, ARG, … Shown as "(IRE)". |
| `dob_precision` | Choice: Exact, Year only, Estimated | Exact: "born 3 May 2011". Year only: "foaled 2011". Estimated: "foaled c. 2011". |
| `ueln` | Text, 15, blank, indexed | Capitals, no spaces. Refuse characters that are not letters or digits. Warn when it is not 15 long. |
| `breed` | Text, 100, blank | Suggestions: Thoroughbred, Argentine polo pony, Thoroughbred cross, Irish Draught, Warmblood, … |
| `passport_issuer` | Text, 100, blank | Suggestions: Weatherbys, Hurlingham Polo Association, Horse Passport Agency, British Horse Society, … |
| `height_inches` | Small integer, null | Type "15.2" or "15.2hh"; save 62; show "15.2 hh". The digit after the point is 0 to 3. |
| `markings` | Text, blank | Passport words, for example "Star, snip, 4 white socks". |
| `breeder` | Text, 200, blank | |
| `identity_checked_at`, `identity_checked_by` | Date-time and user, null | A person scanned the chip and it matched the passport. A change to chip, UELN or passport number clears both. |

`microchip`, `passport_number` and `dam` stay on `Horse`. 35 files read
`microchip`. The foaling and Foals code reads `dam`.

**Age (decision 4, agreed).** One rule for all horses:

- A horse is a year older on 1 January. When `country_of_birth` is in the
  southern hemisphere (ARG, AUS, NZ, RSA, …), on 1 August.
- Age = the current season year minus the foaling season year.
- A foal under a year old keeps the "6 weeks old" label from its true
  age. After 1 January it also shows "yearling".
- `Horse.calculated_age`, `age_label` and the database sort
  (`_age_sort_expression` in `core/views/horses.py`) use this rule.

**Stored ages become foaling years.** A data migration changes each horse
that has `age` but no `date_of_birth`:

- `date_of_birth` = 1 January of (the year the record was made − `age`).
- `dob_precision` = Estimated.
- The records editor has a filter "Estimated foaling year", so staff can
  correct them.

The forms then ask for "Foaled" (a date or a year), not "Age". A later
migration removes the `age` column, after a release that proves no code
reads it.

### 6.2 New table `HorseIdentifier`

For the numbers that some horses have and others do not.

| Field | Type |
|-------|------|
| `horse` | Link to `Horse`, `CASCADE`, `related_name='identifiers'` |
| `kind` | Stud book, Racing (for example a Racing Post profile), Breed society, Competition, Old microchip, Old passport, Other |
| `value` | Text, 100, blank when only a link |
| `issuer` | Text, 100, blank (for example "Weatherbys GSB", "Racing Post") |
| `url` | URL, blank. Shown as "Open on … ↗" with `rel="noopener noreferrer"` |
| `checked`, `checked_by`, `checked_at`, `notes`, `created_at` | |

### 6.3 New table `PedigreeHorse` (shared)

One row for each horse that appears in any pedigree: horses on the yard,
and stallions, mares and grandparents that never come to the yard. Each
row points to its own sire and dam, so the tree is a chain of links.

| Field | Type | Rule |
|-------|------|------|
| `name` | Text, 200 | Registered name, no suffix |
| `name_key` | Text, 200, indexed | Lower case, no spaces or punctuation. For matching only. |
| `country` | Text, 3, blank | Suffix |
| `year_of_birth` | Small integer, null | |
| `sex` | Male, Female, or blank | A sire must not be Female; a dam must not be Male. |
| `sire`, `dam` | Link to `PedigreeHorse`, null, `SET_NULL` | |
| `sire_unknown`, `dam_unknown` | Boolean | Shows "Unknown"; not counted as missing. |
| `horse` | One-to-one link to `Horse`, null, `related_name='pedigree'` | Set when this pedigree horse is on the system. |
| `source` | Typed, Spreadsheet, Document, Feed | |
| `checked`, `checked_by`, `checked_at` | | Compared with a passport or stud book |

Rules:

- No loops: a horse must not be its own ancestor. Check 4 generations up
  on save.
- `Horse.sire_name`, `Horse.dam_name` and `Horse.dam` become copies. One
  service function (`pedigree.services.set_parents`) writes the pedigree
  rows and the copies together, so the old screens, C-vet and the Foals
  table keep working.
- The tree on the page is 2 generations: parents and grandparents. The
  links allow a third generation later with no change to the table.
- Back-fill migration: one `PedigreeHorse` for each `Horse`, and one for
  each distinct `sire_name` and `dam_name` (one row per name, so a sire
  name used for 5 horses gives 1 row). The migration does not join names
  that differ in any way.

### 6.4 Matching rules

One matcher (`pedigree.matching`) serves the forms, the import, the
document reader and later the feeds, "Returning horses" and C-vet.

**Find the horse on the yard** (import, New Arrival, C-vet), in this
order:

1. Same microchip → same horse (confirm on screen).
2. Same UELN → same horse (confirm).
3. Same registered name + suffix + year → probably the same (confirm).
4. Same yard name only → shown as "possible", never chosen for the user.

**Find a pedigree horse** (sire, dam, grandparents):

- Thoroughbred, or a suffix given: suggest rows with the same `name_key`
  and suffix. Show year and parents so the user can tell them apart.
- Other horses: suggest rows with the same `name_key` only when the year
  also matches. The default is "new record".
- Never join two rows without a person choosing.

### 6.5 Proposed changes (one review step)

Every route except typing writes proposals, not data.

| Table | Fields |
|-------|--------|
| `ImportBatch` | `source` (Spreadsheet, Back-fill, Document, Feed), uploaded file or `Document`, `created_by`, `created_at`, `status` (Open, Applied, Discarded) |
| `ProposedChange` | `batch`, `horse` (null = new horse), `target` (for example `horse.microchip`, `pedigree.dam`), `current_value`, `proposed_value`, `match_note`, `decision` (Pending, Accept, Reject) |

Rules:

- Pre-tick a change only when the current value is empty.
- Never pre-tick a change to a value that a person typed or checked.
- On Apply, check each current value again. If it changed since the
  proposal, skip the row and say so.
- Apply runs in one transaction and writes one `AuditEvent` for the batch.

### 6.6 Checks

| Check | Rule | Blocks the save? |
|-------|------|------------------|
| Microchip input | Keep digits only (a scanner can add "FDX-B", spaces or Enter). The field ignores Enter. | — |
| Microchip format | 15 digits | No. "This is not the usual 15 digits. Check the number." Old chips can be 9 or 10 digits. |
| Microchip hint | The first 3 digits are a country code (826 UK, 372 Ireland, 250 France, 032 Argentina) or a maker code (900–998) | No. Shows "Irish chip" as a hint. |
| Duplicate microchip or UELN | Same number on another horse | No. "Also on Rosie (departed 2024). Is this the same horse?" |
| UELN | Letters and digits only; 15 long | Bad characters: yes. Length: warning. |
| Height | Whole number, then 0 to 3 after the point | Yes |
| Pedigree | Sire not Female; dam not Male; no loops | Yes |

### 6.7 Record history

Add `HistoricalRecords` to `Horse` (exclude `photo_thumb`, `updated_at`),
`HorseIdentifier` and `PedigreeHorse`. Each save from an import or a
document sets the change reason, for example "Spreadsheet import 14 Nov
2026" or "Read from passport (document 212)".

---

## 7. Routes, by phase

### Phase 1: by hand

#### 7.1 Identity pop-up and scan-to-check

- The §6.1 fields in the existing pop-up sheet.
- The microchip field accepts a typed number or a scanner that types like
  a keyboard (many Bluetooth readers do).
- **Scan to check**: staff select "Scan to check", scan the horse, and the
  app compares the scan with the stored number.
  - Match: the app sets the ID-checked stamp.
  - No match: "The scan is 985 141 000 123 457. The record says
    985 141 000 123 456." Staff choose which is correct.
- "Check on ChipChecker ↗" copies the chip number and opens the national
  checker (UK or Irish by the hint).

#### 7.2 Pedigree pop-up

```
Sire          [ Copper Ridge      ] (GB)  2004     ← suggestions
              by Highland Star (IRE) out of Silver Brook (GB)  (on record)
Dam           [ Rosie             ] (IRE) 2005  ● on this yard
Dam's sire    [ Moonlit Bay       ] (FR)
More ▾        Sire's sire · Sire's dam · Dam's dam
☐ Sire unknown   ☐ Dam unknown
```

- When the chosen sire already has parents on record, they show in grey.
  Nothing to type.
- Dam's sire writes to the dam's own record. If the dam's record already
  has a different sire, the form shows both and asks.
- Choosing a horse on the yard links it. Typing a name not on record makes
  a new pedigree row when the form is saved.

#### 7.3 Links out

"Add link" saves a `HorseIdentifier`: Weatherbys, Racing Post horse
profile, Stud Book Argentino, sales catalogue, or other. The card shows
them as "Open on … ↗".

#### 7.4 Foaling and covering

- The covering form suggests stallions from the pedigree table. One
  stallion, one spelling.
- At foaling, the foal's pedigree row gets sire = the stallion's row and
  dam = the mare's row. The mare's row uses her registered name when set,
  not the yard name. The grandparents show at once.
- The dam is a default. Staff can change it (embryo transfer).

#### 7.5 Records editor (decision 5)

**Not a spreadsheet grid.** A grid needs paste handling, keyboard moves,
drafts and conflict checks for many rows at once. It caused most of the
editor risks in the stress test (§4.4). The editor below reuses screens
the app already has, and it works the same on a phone.

**Fill in records** (button on the horse list header). Two tabs: Horses
and Pedigree records. A third link goes to **Import a file** (§7.6).

```
Fill in records   [Horses]  Pedigree records  ·  Import a file
Missing: [Any] Chip  UELN  Passport  Pedigree  Foaled          Location ▾  Owner ▾
┌ 23 horses with gaps ──────┬ Copper Rose (GB) · yard name Rosie 23 ───── 4 of 23 ┐
│   Ballerina        2 of 9 │ IDENTITY                                           │
│   Avicii           6 of 9 │ Registered name [Copper Rose   ]  Country [GB ▾]   │
│ ▸ Copper Rose      7 of 9 │ Foaled [12/04/2023] ☐ Year only   Microchip [    ] │
│   Beech            5 of 9 │ UELN [               ]  Passport [826002] Weatherbys│
│   …                       │ PEDIGREE                                           │
│                           │ Sire [Copper Ridge (GB) ▾]  Dam [Starlit Rose ● ▾] │
│                           │ Dam's sire [Moonlit Bay (FR)]   More ▾             │
│                           │                        [Save]  [Save and next →]   │
└───────────────────────────┴────────────────────────────────────────────────────┘
```

- **The list** is the horse list split view (HORSE_LIST_SPLIT_VIEW_PLAN.md)
  with a completeness count: how many of 9 key fields are filled
  (registered name, country, foaled, microchip, UELN, passport, sire,
  dam, dam's sire). "Unknown" counts as filled. Filters: missing field,
  location, owner, breed, "Estimated foaling year".
- **The pane** shows one form with the identity fields (§7.1) and the
  pedigree fields (§7.2). It uses the same form classes as the pop-ups,
  so the rules are in one place.
- **Save and next** saves and opens the next horse in the filtered list.
  The pane shows "Saved Copper Rose. Undo" for 30 seconds, with the
  existing helper (`core/views/_undo.py`) (case 25).
- **Versions:** the form carries the record's version. A save on a record
  that changed since it opened stops and shows who changed it (case 26).
- **Pedigree fields** use the §6.4 matcher. A name with more than one
  match must be chosen before the form saves (case 27).
- **Phone:** the list, then the form on its own screen, with Save and
  next. No separate phone design.
- **History:** each save gets the change reason "Records editor".
- **Access:** full Horses access edits. View-only roles see the list with
  its counts, as a "what is missing" report.

**Pedigree records tab** (cases 10, 28, 29):

- A list of the shared `PedigreeHorse` records: name, country, year, sex,
  sire, dam, "used in N horses on this yard". Search by name.
- Filter **Possible duplicates**: the same `name_key`, or names that
  differ by one letter.
- Select a record to edit it in the pane. A record used by more than one
  horse shows "Used in 14 pedigrees" and asks again before a save.
- **Merge:** choose two records. The pane shows both, with their trees,
  side by side. Choose the one to keep, then confirm. Every link moves to
  the kept record. History stores each moved link, so the merge can be
  reversed.

**Bulk changes without a grid:** **Download as CSV** gives the filtered
horses in the import template's columns. Edit the file in Excel, then
import it (§7.6). The import's review screen shows each change before
it saves.

**Later, only if yards ask:** a spreadsheet-style grid.

#### 7.6 Spreadsheet import

- **Fill in records → Import a file**. Download a CSV template with these
  columns: Yard name (required), Registered name, Country, Foaled (date
  or year), Sex, Colour, Breed, Sire, Dam, Dam's sire, Microchip, UELN,
  Passport number, Passport issuer, Markings, Height.
- Upload the file. The app checks each row and matches it with §6.4.
- A review screen shows each row: "New horse", "Matches Rosie (by chip)",
  or "Possible match", with the proposed changes (§6.5).
- Staff select **Apply**. Nothing saves before that.
- A row with an error does not stop the other rows.

#### 7.7 Back-fill from what the yard already has

A job makes one `ImportBatch` (source Back-fill) for staff to review:

- `breeding` text: "By X out of Y", "X x Y", "X ex Y", "X / Y",
  "X (IRE) x Y (GB)" → proposed sire and dam.
- Names like "Avicii (Karma Avicii)" → proposed registered name.
- Names with " - " and a markings word (sock, star, blaze, snip, stripe)
  → proposed markings.

### Phase 2: read documents

1. Staff select **Read details** on a Passport or Registration document,
   or upload a Weatherbys pedigree PDF or a sales catalogue page.
2. A Celery job sends the file to the Claude API. A PDF goes as a
   document block; a photo as an image block (HEIC changed to JPEG
   first). A JSON schema fixes the answer: identity fields, and each
   pedigree box by position (S, D, DS, SS, SD, DD), each one a value or
   empty. The prompt says: leave a box empty when you cannot read it;
   never guess.
3. The answer becomes an `ImportBatch` (source Document). The review
   screen shows the document beside the tree.

Guard rails:

- Off by default: needs an API key and the yard setting "Read documents
  with AI". Full Horses access only. A daily limit for each yard
  (`core/ratelimit.py`). The model name is a setting.
- Cost: about 2p to 5p for each document. Log the tokens for each job.
- **Personal data:** passport ownership pages show the owner's name and
  address. Ask staff to upload the identification and pedigree pages
  only. Add the API provider as a processor in `DATA_RETENTION.md` and the
  privacy notice. Delete the raw answer when the batch closes.
- **Test before release:** 20 to 30 real documents with known values:
  Weatherbys passports, Weatherbys PDFs, catalogue pages, hand-written
  older passports, and Argentine papers in Spanish if polo customers use
  it. Measure correct values for each field.

### Phase 3: licensed data and matching

- Talk to Weatherbys (data supply) and Racing Post (B2B) only when
  customers ask and pay. Thoroughbreds only.
- Feed data matches on UELN, or registered name + suffix + year, and
  becomes an `ImportBatch` (source Feed). Same review screen.
- "Returning horses" and the C-vet link use the §6.4 matcher.
- A one-page identity and pedigree PDF for sales.

---

## 8. The horse page

### 8.1 Header

- After the name, when it differs: "Registered as **Karma Avicii (GB)**".
- The pedigree line the trade uses:

  > by **Copper Ridge (GB)** out of **Rosie (IRE)**, by Moonlit Bay (FR)

  Horses on the yard are links.
- **ID checked** badge (green) when the stamp is set. Keep "No passport"
  (amber).
- Age: one rule for all horses (§6.1). "foaled 2011" when the date is
  year only; "foaled c. 2011" when it is estimated.

### 8.2 Card: Identity & pedigree

Second card in the left column, after Current Placement. It replaces the
Details card. Notes and conditions move to a smaller **Notes &
conditions** card (UX_REVIEW_PLAN.md A2 item 5 already asks to remove
Details).

**Prototype with example data:**
[Horse Pedigree Card](https://claude.ai/artifact/7UJyTrTVJVvVYaEND1Xs1h)
(private to the account owner until shared from its Share menu).

Layout: the identity pane on the left (about 17.5rem), the pedigree pane
on the right. When the card is narrower than about 760px, the identity
pane goes above the pedigree.

```
┌ Identity & pedigree ─────────────────────────────────────── Edit · Scan to check ┐
│ IDENTITY                     │ PEDIGREE                                           │
│ Yard name  Rosie 23          │ by Copper Ridge (GB) out of Starlit Rose (IRE),    │
│ Microchip  826 098 000 123 456│ by Moonlit Bay (FR)                                │
│ UELN       826002 000123456  │                          ╭ Highland Star (IRE)     │
│ Passport   Weatherbys        │            ╭ Copper Ridge╯                         │
│ Breed      Thoroughbred      │ Copper Rose┤ (GB) 2014   ╰ Silver Brook (GB)       │
│ Foaled     12 Apr 2023 · GB  │            ╰ Starlit Rose╮ Moonlit Bay (FR)        │
│ ✓ Chip scanned · 3 Oct 2026  │              (IRE) ●     ╰ Fair Lady (GB)          │
│                              │ FAMILY  Dam Starlit Rose · Full sibling …          │
│                              │ SOURCE  Typed 12 Nov 2026 · Weatherbys ↗           │
└──────────────────────────────┴────────────────────────────────────────────────────┘
```

Identity rules:

- Numbers in mono, in groups of three, with a copy button that copies the
  number with no spaces.
- An empty field is not a row. One line lists what is missing: "Missing:
  UELN, height. **Add**". "Unknown" parents are not missing.
- Existing tokens and `.card`, `.section-header`, `.dt-label` classes. No
  new colour.

### 8.3 The pedigree diagram

This is the most visible part of the feature. The prototype above shows
each rule below.

**Shape.** A sideways tree: the horse on the left, its parents, then its
grandparents. In each pair the sire is above the dam. This is the layout
of sales catalogue pages, so racing staff read it at once. A round "fan"
chart was considered and rejected: long racehorse names do not fit on
curved wedges, and the trade does not use that layout.

**Generations.** 2 by default (6 boxes). **Show 3 generations** (14 boxes)
appears only when a third-generation record exists (decision 3).

**The grid.** The diagram is a CSS grid. Nothing is measured in the
browser.

- Columns: generation 0, gap, generation 1, gap, generation 2 (and gap,
  generation 3).
- Rows: 2^G equal rows, where G is the number of generations shown
  (`repeat(R, minmax(row-height, 1fr))`).
- Each position has a code: S = sire, D = dam, read from the horse
  outwards (SD = sire's dam). Read S as 0 and D as 1, and the code is the
  box's index in its generation (SS 0, SD 1, DS 2, DD 3).
- The box at generation k with index i sits in column 2k+1, starts at row
  i × R/2^k + 1 and spans R/2^k rows. It is centred in its rows, so its
  middle is at (i + 0.5) / 2^k of the height.

**The lines.**

- One inline SVG for each gap: `viewBox="0 0 100 100"`,
  `preserveAspectRatio="none"`, spanning all rows.
- For each box at generation k + 1, one path from the middle of its child
  (x 0) to its own middle (x 100), using the fractions above × 100.
- Curved: `M0 y1 C55 y1 45 y2 100 y2`. Bracket: `M0 y1 H50 V y2 H100`.
  The prototype has both. Recommendation: curved.
- `vector-effect: non-scaling-stroke` keeps the line width the same when
  the SVG stretches.
- Lines to "Unknown" or "not entered" boxes are dashed.
- The line colour is a theme token, so it also works in dark mode.

**Drawn by the server.** A template tag `{% pedigree_tree horse
generations=2 %}` (`pedigree/templatetags/pedigree.py`) does this work:

- It reads the records, one query for each generation.
- It works out each box's grid place and each SVG path.
- It renders `templates/pedigree/_tree.html`.

No script is needed to draw the tree. One small script
(`static/js/pedigree_tree.js`) only adds the highlight. The same tag
serves the card, the split-view preview and the Merge screen.

**Boxes.**

| State | Look |
|-------|------|
| The horse | Filled forest box: name, year, colour, sex |
| On record | Light box: relation label (Sire, Dam's sire, …; hidden at generation 3), name and suffix, year and colour |
| On this yard | As "On record", with a green dot before the name |
| Unknown | Dashed box: "Unknown" |
| Not entered | Dashed box: "+ Add" for full access, empty for view-only |
| Appears twice | Saddle border and fill, with a badge such as "2×3" |

When a parent is Unknown or not entered, its own parents are not drawn.

**Interaction.**

- Point at a box, or move the keyboard focus to it: the line from that
  box back to the horse is highlighted. Every copy of the same record
  highlights with it.
- Select a box: the card shows that record's own tree, with "← Back to
  Copper Rose". A horse on the yard also offers "Open horse page".
- Select "+ Add": the pedigree pop-up opens at that position.

**Inbreeding.** A record that appears more than once in the generations
shown is marked. A line under the tree says, for example, "Inbreeding:
Highland Star (IRE) 2×3": the sire is generation 1, so 2×3 means
"sire's sire, and also a great-grandparent on the dam's side". Shared
records make this exact: the same record means the same horse, with no
name matching.

**Text around the tree.**

- Above: the byline the trade uses, for example "by Copper Ridge (GB)
  out of Starlit Rose (IRE), by Moonlit Bay (FR)".
- Below:
  - **Family** on this yard: dam, full and half siblings, progeny, the
    recipient mare after an embryo transfer.
  - **Inbreeding**, when there is any.
  - **Not entered**, with an Add link.
  - **Source:** "Typed 12 Nov 2026", "Filled at foaling from the Copper
    Ridge and Starlit Rose records", or "Read from passport". Then the
    links out (Weatherbys ↗, Racing Post ↗).
- Family uses the pedigree links. At most 3 extra queries.

**Narrow screens.**

- The diagram reacts to its own width (a CSS container query), not the
  screen width. So the same partial works in the card, in the split-view
  pane and on a phone.
- Below about 30rem, the horse's own box and the first gap are hidden,
  because the page title already names the horse. Parents and
  grandparents fill the width.
- With 3 generations, the tree keeps a minimum width and scrolls sideways
  inside the card. The page itself never scrolls sideways.

**Accessibility.**

- The boxes are in family order in the page: Sire, Sire's sire, Sire's
  dam, Dam, Dam's sire, Dam's dam. A screen reader reads them as a list.
- Each box has a label, for example "Sire's dam: Silver Brook (GB),
  foaled 2008".
- The SVGs are hidden from screen readers. The byline gives the summary.

**No graph library.** A library would lay out the tree in the browser,
add size, and need changes to the Content Security Policy. A pedigree is
a fixed binary tree, so the server can place every box.

### 8.4 Empty state

```
┌ Identity & pedigree ────────────────────────────────────────────┐
│ No microchip, UELN or pedigree yet.                             │
│ [ Type it in ]   Scan chip   Add Weatherbys or Racing Post link │
└─────────────────────────────────────────────────────────────────┘
```

Phase 2 adds "Read from passport or PDF".

### 8.5 Other places

| Place | Change |
|-------|--------|
| Horse list header | **Fill in records** button: opens the records editor (§7.5) with the list's filters |
| Split-view preview | Microchip, UELN, the "by … out of …" line, and the diagram (§8.3) in its narrow form |
| Quick view | Microchip |
| Search | Also by UELN and registered name |
| Foals table | Sire from the pedigree row; a link when on the system |
| Help article (`help/articles/horses.html`) | "Record identity and pedigree", "Import horses from a spreadsheet" |

---

## 9. Phases

| Phase | What | Effort | Needs |
|-------|------|--------|-------|
| **1a. Identity and age** | §6.1 fields, the age rule and the age-to-foaling-year migration, §6.2 links, checks (§6.6), history (§6.7), scan-to-check and ChipChecker link (§7.1), identity half of the card, header, preview and quick view | M | — |
| **1b. Pedigree** | `PedigreeHorse` and back-fill migration, matcher (§6.4), pedigree pop-up (§7.2), foaling and covering (§7.4), the diagram and its template tag (§8.3), family on this yard | M/L | 1a |
| **1c. Records editor** | Fill in records (list, form, Save and next), Pedigree records with duplicates filter and Merge, Download as CSV (§7.5) | M | 1b |
| **1d. Import and clean-up** | Proposed changes (§6.5), spreadsheet import (§7.6), back-fill from text and names (§7.7) | M | 1c |
| **2. Documents** | Read passports, Weatherbys PDFs and catalogue pages into proposals; the test set; the processor notice | M | 1d; an API key |
| **3. Licensed data and matching** | Feeds into proposals; Returning horses; C-vet link; sales PDF; third generation | L, plus a contract | 1d |

---

## 10. Decisions for you

| # | Question | Status |
|---|----------|--------|
| 1 | Shared pedigree records (§6.3), or a copy on each horse? | **Agreed: shared.** |
| 2 | Keep `microchip` and `passport_number` on `Horse`? | Open. Recommendation: yes; use `HorseIdentifier` for extra numbers. |
| 3 | Generations on the page? | Open. Recommendation: 2 by default, with "Show 3 generations" when the records exist (§8.3; the prototype shows both). Sire, dam, dam's sire are the minimum to enter. |
| 4 | Racing age? | **Agreed: one rule for all horses** (1 January, or 1 August in the southern hemisphere). Stored ages become estimated foaling years (§6.1). |
| 5 | Spreadsheet import in phase 1? | **Agreed: yes, and a records editor in the app.** The editor is a list and a form with Save and next, not a spreadsheet grid (§7.5). |
| 8 | Pedigree lines: curved or bracket? | Open. Recommendation: curved (§8.3). The prototype shows both. |
| 6 | Embryo transfer for polo? | Open. Recommendation: phase 1 lets the dam differ from the mare that foaled; a donor-mare field on the breeding record later. |
| 7 | AI document reading (phase 2)? | Open. Recommendation: yes, through the review screen, after the test set passes. |

---

## 11. Tests

- `core/tests/test_identity.py`: chip input from a scanner string; format
  and duplicate warnings that do not block; UELN rules; height in and out;
  `uid` on new and old rows; a chip change clears the stamp; scan-to-check
  match and mismatch; exact, year-only and estimated date display.
- `core/tests/test_age.py`: the age goes up on 1 January, and on 1 August
  for a southern-hemisphere country; a foal shows weeks, then "yearling"
  after 1 January; the database sort gives the same order as
  `calculated_age`; the migration turns `age` 15 on a 2026 record into
  "foaled c. 2011".
- `pedigree/tests/test_models.py`: sire not Female, dam not Male; no
  loops; "Unknown" is not missing; `set_parents` keeps `sire_name`,
  `dam_name` and `dam` in step.
- `pedigree/tests/test_matching.py`: chip, UELN, name + suffix + year
  order; a yard name alone never auto-matches; two polo "Rosie" rows stay
  apart.
- `pedigree/tests/test_foaling.py`: a foal gets its grandparents with no
  typing; the dam can be changed after foaling.
- `pedigree/tests/test_import.py`: template columns; a bad row does not
  stop others; nothing saves before Apply; a typed value is never
  pre-ticked for change; a value changed after the proposal is skipped.
- `pedigree/tests/test_backfill.py`: each `breeding` text pattern.
- `pedigree/tests/test_records_editor.py`: the completeness count
  ("Unknown" counts as filled); Save and next opens the next horse in the
  filtered list; undo; a record changed since it opened does not save; a
  pedigree name with two matches does not save until chosen; a view-only
  role cannot save; merge moves every link, keeps history and can be
  reversed.
- `pedigree/tests/test_tree.py`: grid place and SVG path for each position
  with 2 and 3 generations; the parents of an Unknown or not-entered box
  are not drawn; dashed lines to those boxes; inbreeding labels such as
  2×3; one query for each generation.
- `static/js/tests/pedigree_tree.test.js`: the highlight follows the
  position code back to the horse, and twins highlight together.
- `core/tests/test_query_counts.py`: a cap for the horse page with a full
  tree and family.
- Phase 2: the review screen saves only accepted rows; the job never
  writes to `Horse` itself; the feature is hidden when off.
- The htmx lint and the vocabulary lint stay green.

---

## Sources

- Notion Dev board cards: "Standard horse identifiers (microchip, UELN,
  passport)", "Returning horses: find the old record on arrival", "Link
  C-vet horses to yard horses by microchip", "Horse life timeline with a
  tamper-evident log", "Advanced record keeping", "Sales prep feature".
- [Weatherbys – Pedigree Services](https://www.weatherbys.co.uk/bloodstock/pedigree-services)
- [Weatherbys – Data Services](https://www.weatherbys.co.uk/bloodstock/data-services)
- [Weatherbys – Data Supply](https://weatherbys.co.uk/commercial/data-supply)
- [Weatherbys – Thoroughbred registrations](https://www.weatherbys.co.uk/passports/passports-gb-registrations/thoroughbreds)
- [Weatherbys – Stud book](https://www.weatherbys.co.uk/stud-book)
- [Racing Post API announcement (Spotlight Sports Group)](https://www.spotlightsportsgroup.com/sports-betting-news/announcing-the-racing-post-api-available-now/)
- [World Horse Welfare – ID regulations and the Chip Checker](https://www.worldhorsewelfare.org/advice/identification-regulations-is-your-horse-passported-and-microchipped)
- [BHS – Is your horse microchipped?](https://www.bhs.org.uk/horse-care-and-welfare/ownership-loaning/is-your-horse-microchipped/)
- [Irish government – equine microchip checker](https://www.gov.ie/en/department-of-agriculture-food-and-the-marine/press-releases/minister-mcconalogue-launches-equine-microchip-checker)
- [HPA passport application form 2026](https://hpa-polo.co.uk/download/HPA-Passport-Application-Form-2026-updated.pdf)
- [Stud Book Argentino – example public profile](https://www.studbook.org.ar/ejemplares/perfil-pdf/172083/pololeo)
- [Argentine Polo Pony (Wikipedia)](https://wikipedia.com/wiki/Argentine_Polo_Pony)
- [Argentine Ministry of Agriculture – horse breeds](https://www.magyp.gob.ar/sitio/areas/equinos/razas/)
- [Regulation 2015/262, Article 2 (definitions)](https://www.legislation.gov.uk/eur/2015/262/article/2)
- [Regulation 2015/262, Annex I, Section I](https://legislation.gov.uk/eur/2015/262/annex/I/part/1/section/I)
- [BHS – overstamping a foreign passport (UELN length)](https://www.bhs.org.uk/horse-care-and-welfare/horse-passports/overstamping-a-foreign-passport/)
- [General Stud Book (Wikipedia)](https://en.wikipedia.org/wiki/General_Stud_Book)
