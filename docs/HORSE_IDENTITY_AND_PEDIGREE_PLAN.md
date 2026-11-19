# Horse identity and pedigree

**Goal:** Make the horse page the record a yard trusts for "which horse is
this, and where does it come from". One card shows the identifiers
(microchip, UELN, passport) and the immediate family tree (parents and
grandparents). Staff can fill it in less than two minutes, from a
Weatherbys pedigree or from the passport.

**Scope:** the `Horse` model, the horse page, the pop-up edit forms, the
split-view preview, and an optional "read it from the document" step.

**Status:** planned. Nothing in this file is built.

---

## 0. Summary

- The roadmap card "Standard horse identifiers" (Notion Dev board) asks
  for UELN, format checks, a duplicate-chip warning and a fixed internal
  ID. Phase 1 does this.
- The app already stores the microchip, but **the horse page does not
  show it**. Phase 1 fixes this first.
- Weatherbys has **no free public API**. Its data is sold under licence.
  Do not scrape its pages. Staff copy the data in, or the app reads a
  Weatherbys pedigree PDF or a passport photo that staff upload.
- The passport is the best single source. It has the identifiers **and**
  the sire, dam and dam's sire, for every breed, not only thoroughbreds.
- Recommended order: identity fields (1) → pedigree card (2) → read from
  a document with AI, staff confirm each value (3).

---

## 1. What the roadmap asks for

Source: Notion, CGate → Dev board. All cards have the status "ideas".

| Card | What it asks for | Where this plan covers it |
|------|------------------|---------------------------|
| Standard horse identifiers (microchip, UELN, passport) | Fixed internal ID (UUID). A table of identifiers: type, value, issuer, checked yes or no. Microchip: 15 digits, remove spaces. UELN: 15 characters. Warn (do not block) on a duplicate chip. | §5.1, §5.2, §5.5. Phase 1. |
| Returning horses: find the old record on arrival | New Arrival asks for the chip first and reuses the old record. | Phase 4. It needs the clean chip data from phase 1. |
| Link C-vet horses to yard horses by microchip | Match `ClientHorse` to `Horse` by chip. | Phase 4. Same dependency. |
| Horse life timeline with a tamper-evident log | Hash-chained events per horse. | §5.6 adds record history to `Horse`. The hash chain stays a separate piece of work. |
| Advanced record keeping | Weight and height over time. | §5.1 adds the current height only. Height over time is a later measurement table. |
| Sales prep feature | "Is there anything to be done with this?" | A complete identity and pedigree card is the base of a sales sheet. Phase 4. |
| Improve Breeding Section (mare side) / Stallion nominations | Easier breeding entry, stallion details. | §5.3 adds a `sire` link, so a stallion on the yard links to its foals. |

---

## 2. What the code has today

`Horse` (`horse_management/core/models.py`, line 716):

| Field | Today | Problem |
|-------|-------|---------|
| `microchip` | Text, 50 chars, blank allowed | No format check. No duplicate check. **Not shown on the horse page, the preview or the quick view.** Only the edit forms and search use it. |
| `passport_number`, `has_passport` | Text and a tick box | No issuer. Shown in the Details card and as a header badge. |
| `sire_name`, `dam_name` | Text | Shown in the header only when filled. No link to a sire on the yard. |
| `dam` | Link to another `Horse` | Set only by the foaling flow (`health/services.py`). Not on any form. |
| `breeding` | Free text | Repeats sire and dam ("By X out of Y"). |
| `color`, `sex`, `date_of_birth` | Fixed lists and a date | Good. |
| UELN, breed, height, markings, country of birth, breeder, registered name | **Not there** | — |
| Record history | **Not there** | `Owner` and others use `django-simple-history` (`core/audit.py`). `Horse` does not. An identifier change leaves no trace. |

Other parts that this plan uses:

- `Document` has the types Passport and Registration Papers. A passport
  photo taken from the Photos card is saved as a `Document`
  (`core/views/photos.py`).
- The foaling flow makes the foal's `Horse` row with `dam`, `sire_name`
  (from the covering) and `microchip`.
- `BreedingRecord.stallion_name` is free text.
- Celery, `requests` and `pillow-heif` are already installed. No AI
  service is installed.

**What the screenshot shows.** Staff put identity data into the name
field because there is nowhere else for it:

- "Ballerina – 4 white socks" — the markings are in the name.
- "Avicii (Karma Avicii)" — the yard name and the registered name are in
  one field.

A registered name field and a markings field remove the need for this.

---

## 3. What Weatherbys can give us

Weatherbys keeps the General Stud Book (thoroughbreds in Great Britain and
Ireland) and issues their passports. Facts below come from web search
results. This container cannot open weatherbys.co.uk, so confirm them
with Weatherbys before phase 4.

- **GSB Online** is the breeder's account for foal registrations, mare and
  stallion registrations and ownership changes.
- **Pedigree Portal**: account holders download pedigrees "in their
  preferred format". Public sample files are PDF pedigree pages
  (sire line and dam line).
- **Data services** (paid, under licence): a Stallion Data API, an
  Entries, Results and Horse List API, and bespoke data feeds. No public
  developer licence or price is published.
- A GB thoroughbred foal must be microchipped by a vet before Weatherbys
  issues its passport.

### Ways to get the data in

| Route | Effort for staff | Works for | Risk | Verdict |
|-------|------------------|-----------|------|---------|
| A. Type it in (6 boxes) | About 2 minutes | Every horse | Typing errors | **Phase 2.** Always available. |
| B. Save the Weatherbys link or GSB number | Paste one link | Thoroughbreds | None | **Phase 2.** One tap to the full record. |
| C. Upload the passport or a Weatherbys PDF; the app reads it; staff confirm | About 30 seconds | Every horse with a passport | A wrong read if staff do not check | **Phase 3.** Best effort-to-value. |
| D. Paste the text of a Weatherbys page; the app splits it into boxes | About 1 minute | Thoroughbreds | Copied text order changes; fragile | Only if route C is refused. |
| E. Licensed Weatherbys data feed | None | Thoroughbreds only | Cost, contract | Later, only if stud customers ask. |
| F. Scrape weatherbys.co.uk | — | — | Breaks their terms; pages need a sign-in; breaks on each redesign | **Do not do.** |

---

## 4. What the passport gives us

A UK passport (Section I, identification) has, for every breed:

- name, UELN, species, sex, colour, breed;
- date of birth, country of birth, breeder;
- transponder (microchip) code;
- markings, as words and as a diagram;
- for a registered breed: sire, dam and dam's sire, and the stud book;
- the issuing body (PIO), for example Weatherbys.

The UELN is 15 characters: a 6-character code for the issuing database,
then a 9-character number for the horse. Some older UELNs are shorter or
contain letters. The microchip is 15 digits (ISO 11784).

---

## 5. Data model

### 5.1 New fields on `Horse`

Each of these has one value per horse and shows in many places, so it
goes on the `Horse` row.

| Field | Type | Rule |
|-------|------|------|
| `uid` | `UUIDField(unique=True, editable=False)` | Fixed internal ID (roadmap). Three-step migration: add as null, fill, make unique. |
| `registered_name` | Text, 200, blank | The name in the passport or stud book. `name` stays the yard name. |
| `country_of_birth` | Text, 3, blank | Stud-book suffix: GB, IRE, FR, USA. Shown as "(GB)". |
| `ueln` | Text, 15, blank, indexed | Save in capitals with no spaces. Refuse characters that are not letters or digits. Warn (do not block) when it is not 15 characters. |
| `breed` | Text, 100, blank | Free text with suggestions (Thoroughbred, Irish Draught, Warmblood, Cob, Welsh, Connemara, …). |
| `passport_issuer` | Text, 100, blank | Suggestions: Weatherbys, Horse Passport Agency, British Horse Society, … |
| `height_inches` | Small integer, null | Type "16.2" or "16.2hh". Save 66. Show "16.2 hh". The digit after the point must be 0 to 3. |
| `markings` | Text, blank | Passport words, for example "Star, snip, 4 white socks". |
| `breeder` | Text, 200, blank | |
| `sire` | Link to `Horse`, null, `SET_NULL`, `related_name='offspring_as_sire'` | Same as `dam`. Set only when a person chooses a horse on the yard. |
| `identity_checked_at`, `identity_checked_by` | Date-time and user, null | A person read the chip with a scanner and it matched the passport. |

`microchip` and `passport_number` stay on `Horse`. 35 files read
`microchip` (search, C-vet API, foaling, preview). Moving them into a
table gives no gain for that cost.

### 5.2 New table `HorseIdentifier`

For the numbers that some horses have and others do not. This is the
roadmap's "table of identifiers", for everything except the three main
numbers.

| Field | Type |
|-------|------|
| `horse` | Link to `Horse`, `CASCADE`, `related_name='identifiers'` |
| `kind` | Choice: Stud book, Breed society, Competition (FEI, British Eventing, …), Old microchip, Old passport, Other |
| `value` | Text, 100 |
| `issuer` | Text, 100, blank (for example "Weatherbys GSB") |
| `url` | URL, blank. Shown as "Open on Weatherbys ↗" with `rel="noopener noreferrer"` |
| `checked`, `checked_by`, `checked_at` | As the roadmap card asks |
| `notes`, `created_at` | |

The Weatherbys link (route B) is a row here: kind Stud book, issuer
Weatherbys GSB, value the registration number, url the link.

### 5.3 Pedigree: new table `PedigreeAncestor`

Parents stay on `Horse` (`sire`, `sire_name`, `dam`, `dam_name`). The
foaling flow, the Foals table, CSV import and the header use them. The
grandparents go in a new table, one row for each box in the tree.

| Field | Type |
|-------|------|
| `horse` | Link to `Horse`, `CASCADE`, `related_name='ancestors'` |
| `position` | Choice: `SS` sire's sire, `SD` sire's dam, `DS` dam's sire, `DD` dam's dam. Codes for a third generation (`SSS` …) can come later with no change to the table. |
| `name` | Text, 200. Can include the suffix, for example "Moonlit Bay (FR)". Same as `sire_name`. |
| `year_of_birth` | Small integer, null |
| `linked_horse` | Link to `Horse`, null. Set only when a person chooses a horse on the yard. |
| `source` | Choice: Typed, Read from document |
| `source_document` | Link to `Document`, null |

Unique: (`horse`, `position`).

Why rows and not four text fields on `Horse`: rows let a third
generation come later with no migration, and they let the app answer
"which horses on the yard have this grandsire".

The edit form hides the split. Staff see six boxes in tree order.

### 5.4 Family on this yard (no new data)

Worked out from the links when the page loads:

- Sire and dam, when they are horses on the system.
- Full siblings: same `dam` and same sire.
- Half siblings: same `dam`, different sire. With a stallion on the yard,
  also same `sire`.
- Offspring: `dam` or `sire` is this horse.

Limit: 3 extra queries. Add a cap to `core/tests/test_query_counts.py`.

### 5.5 Checks

| Check | Rule | Blocks the save? |
|-------|------|------------------|
| Microchip format | Remove spaces and dashes. 15 digits. | No. Shows "This is not the usual 15 digits. Check the number." Old chips can be 9 or 10 digits. |
| Duplicate microchip | Same number on another horse, active or departed | No. Shows "Also on Rosie (departed 2024). Is this the same horse?" with a link. This is the hook for "Returning horses". |
| UELN format | Letters and digits only; 15 long | Bad characters: yes. Wrong length: no, warning only. |
| Duplicate UELN | Same as microchip | No. Warning. |
| Height | Whole number, then 0 to 3 after the point | Yes |

### 5.6 Record history

Add `history = HistoricalRecords(excluded_fields=['photo_thumb', 'updated_at'])`
to `Horse`, `HorseIdentifier` and `PedigreeAncestor`. The audit log then
shows who changed a chip number and when. This is also the first step
for the "tamper-evident log" card.

---

## 6. The horse page

### 6.1 Header

- Keep the name, badges, age, colour and sex line.
- After the name, when it is different: "Registered as **Karma Avicii (GB)**".
- Replace the "Sire: · Dam:" line with the line the trade uses:

  > by **Copper Ridge (GB)** out of **Rosie (IRE)**, by Moonlit Bay (FR)

  Names of horses on the yard are links.
- Replace the "Passport" badge with **ID checked** (green) when
  `identity_checked_at` is set. Keep "No passport" (amber) as it is.

### 6.2 New card: Identity & pedigree

Second card in the left column, after Current Placement. It replaces the
Details card. Notes and conditions move to a smaller **Notes &
conditions** card (UX_REVIEW_PLAN.md A2 item 5 already asks to remove
Details).

Desktop (`lg` and up):

```
┌ Identity & pedigree ─────────────────────── Edit · Read from document ┐
│ IDENTITY                        │ PEDIGREE                             │
│ Microchip 985 100 012 345 678 ⧉ │           ┌ Copper Ridge (GB) 2004 ┬ Highland Star (IRE)
│ UELN      826002 000123456    ⧉ │ Ballerina ┤                        └ Silver Brook (GB)
│ Passport  826002 · Weatherbys   │           └ Rosie (IRE) 2005 ●     ┬ Moonlit Bay (FR)
│ Breed     Thoroughbred          │                                    └ Fair Lady (GB)
│ Height    16.1 hh               │ ● on this yard                       │
│ Markings  Star, 4 white socks   │ From passport · 12 Nov 2026          │
│ Born      1 Jan 2011, GB        │ Open on Weatherbys ↗                 │
│ ✓ Chip scanned, matches passport · 3 Oct 2026 · Sam                    │
├────────────────────────────────────────────────────────────────────────┤
│ FAMILY ON THIS YARD   Dam Rosie · 2 half-siblings · 1 foal             │
└────────────────────────────────────────────────────────────────────────┘
```

Phone (below `lg`). The tree becomes the "by … out of …" form, which
fits a narrow screen:

```
IDENTITY & PEDIGREE                    Edit
Microchip   985 100 012 345 678    ⧉
UELN        826002 000123456       ⧉
Passport    826002 · Weatherbys
Breed       Thoroughbred · 16.1 hh
Markings    Star, 4 white socks
✓ Chip scanned · 3 Oct 2026

Sire  Copper Ridge (GB) 2004
      by Highland Star (IRE) out of Silver Brook (GB)
Dam   Rosie (IRE) 2005 ● on yard
      by Moonlit Bay (FR) out of Fair Lady (GB)

Family here: Dam Rosie · 2 half-siblings
```

Rules:

- Numbers in mono, in groups of three, with a copy button (⧉). The copy
  button copies the number with no spaces.
- An empty field is not a row. One quiet line lists what is missing:
  "Missing: UELN, height. **Add**".
- The tree uses the existing tokens and `.card`, `.section-header`,
  `.dt-label` classes. No new colour.
- The tree is a nested list (`<ul>`) with "Sire", "Dam", "Sire's sire"
  as screen-reader text, so it reads in order without the lines.

### 6.3 Empty state

For a horse with no identity data (Ballerina today):

```
┌ Identity & pedigree ────────────────────────────────────────────┐
│ No microchip, UELN or pedigree yet.                             │
│ [ Read from passport or Weatherbys PDF ]   Type it in           │
└─────────────────────────────────────────────────────────────────┘
```

Before phase 3, only "Type it in" shows.

### 6.4 Other places

| Place | Change |
|-------|--------|
| Split-view preview (`partials/preview.html`) | Microchip, UELN and the "by … out of …" line |
| Quick view (`partials/quick_view.html`) | Microchip |
| Search | Also find by UELN and registered name |
| Foals table | Sire is a link when `sire` is set |
| Help article (`help/articles/horses.html`) | New section "Record identity and pedigree" |

---

## 7. How staff fill it in

### 7.1 Type it in (phase 1 and 2)

- **Edit identity** pop-up: the §5.1 fields, plus a tick box "I scanned
  the chip and it matches the passport".
- **Edit pedigree** pop-up: six boxes laid out as the tree.
  - Each box suggests horses on the system (yard name and registered
    name, departed horses too). Choosing one sets the link. Typing a
    name that is not on the system saves the text only.
  - The app never links a horse by name match alone. A person chooses.
- Both pop-ups use the existing pop-up sheet (`includes/_popup_sheet.html`)
  and the `_popup.py` helpers, like the quick edit.

### 7.2 Weatherbys link (phase 2)

"Add Weatherbys link" asks for the link and the registration number. It
saves a `HorseIdentifier` row (§5.2). The card shows "Open on
Weatherbys ↗".

### 7.3 Read from a document (phase 3)

1. Staff select **Read details** on a Passport or Registration document,
   or upload one from the empty state.
2. A Celery job sends the file to the Claude API. A PDF goes as a
   document block; a photo goes as an image block (HEIC changed to JPEG
   first). A JSON schema (structured output) fixes the shape of the
   answer: each field is a value or empty. The prompt tells the model to
   leave a field empty when it cannot read it, and never to guess.
3. A review screen shows the document on the left and a table on the
   right:

   | Field | Now | In the document | Use |
   |-------|-----|-----------------|-----|
   | Microchip | — | 985100012345678 | ☑ |
   | Dam | Rosie | Rosie (IRE) | ☐ |

   The app ticks "Use" only where the current value is empty. A
   difference is in amber. Nothing saves until a person selects **Save**.
4. The save writes `source = Read from document` and the document link
   on each pedigree row, and an `AuditEvent`.

Guard rails:

- Off by default. It needs an API key in the settings and a yard
  setting "Read documents with AI".
- Only roles with full Horses access can use it.
- A daily limit for each yard (`core/ratelimit.py`).
- The model name is a setting, not code.
- Cost: about 2p to 5p for each document (a few thousand tokens in, a
  few hundred out). Log the tokens for each job.
- **Personal data.** Passport ownership pages show the owner's name and
  address. Tell staff to upload only the identification and pedigree
  pages, or the Weatherbys pedigree PDF. Add the API provider as a
  processor in `DATA_RETENTION.md` and the privacy notice. Delete the raw
  answer when the review closes.
- **Test before release.** Collect 15 to 20 real passports and Weatherbys
  PDFs with known values. Measure correct fields before the feature goes
  to any customer.

### 7.4 Clean-up of current names (phase 1)

A management command `suggest_identity_cleanup` lists, and does not
change:

- names like "Avicii (Karma Avicii)" → suggest registered name
  "Karma Avicii";
- names with " - " or " – " and a markings word (sock, star, blaze, snip,
  stripe) → suggest moving the words to Markings.

Staff accept each suggestion on a list page.

---

## 8. Phases

| Phase | What | Effort | Needs |
|-------|------|--------|-------|
| **1. Identity** | §5.1 fields (not `sire`), checks (§5.5), record history (§5.6), identity part of the card, header line, microchip on preview and quick view, ID-checked stamp, name clean-up (§7.4), help article | M | — |
| **2. Pedigree** | `sire` link, `PedigreeAncestor`, `HorseIdentifier`, tree on the card, "by … out of …" header line, family on this yard, edit pedigree pop-up with suggestions, Weatherbys link | M | 1 |
| **3. Read from document** | §7.3, the test set, the processor notice | M | 1, 2; an API key; the privacy update |
| **4. Later** | Returning horses by chip; C-vet link by chip; third generation; a one-page identity and pedigree PDF for sales; talk to Weatherbys about a data feed if stud customers ask | — | 1 |

---

## 9. Decisions for you

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | Keep `microchip` and `passport_number` on `Horse`, or move all numbers into the identifiers table as the roadmap card says? | Keep them on `Horse`. Use the table for the extra numbers (§5.1, §5.2). |
| 2 | Two generations or three? | Two now. The table allows three later with no migration. |
| 3 | Use AI to read passports and PDFs (phase 3)? | Yes, with a person confirming each value, and only after the test set passes. |
| 4 | Split "Avicii (Karma Avicii)" style names? | Yes, with the suggestion list. Never change a name without a person. |

---

## 10. Tests

- `core/tests/test_identity.py`: microchip and UELN clean-up and
  warnings; a duplicate chip warns and does not block; height in and out;
  `uid` is set on new and old rows.
- `core/tests/test_pedigree.py`: one row for each position; a link only
  when chosen; family on this yard (full and half siblings, offspring);
  the foaling flow still sets the foal's sire and dam.
- `core/tests/test_horse_detail.py`: the card shows the chip; an empty
  field shows no row; the empty state; the header "by … out of …" line.
- `core/tests/test_query_counts.py`: a cap for the horse page with a full
  tree.
- Phase 3: the review screen saves only ticked rows; the job never writes
  to `Horse` itself; the feature is hidden when off.
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
- [Equine identification regulation 2015/262, Article 2 (definitions)](https://www.legislation.gov.uk/eur/2015/262/article/2)
- [Regulation 2015/262, Annex I, Section I](https://legislation.gov.uk/eur/2015/262/annex/I/part/1/section/I)
- [British Horse Society – overstamping a foreign passport (UELN length)](https://www.bhs.org.uk/horse-care-and-welfare/horse-passports/overstamping-a-foreign-passport/)
- [General Stud Book (Wikipedia)](https://en.wikipedia.org/wiki/General_Stud_Book)
