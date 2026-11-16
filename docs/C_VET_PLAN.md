# C-vet plan: the vet's app, built on C-gate

**Goal:** Tim finishes a yard visit on his phone, with or without a signal. Every note, every bill and every record is correct before he leaves the yard. Nobody types the same data twice.
**The client:** the yard. Tim works for a yard (for example Colgate Farm, where he deals with Charlie), bills the yard, and the yard invoices its horse owners. C-vet works in that order: the yard first, then its horses, then one bill to the yard.
**Scope now:** Tim, our own yard. C-vet is a companion to Tim's practice system. It does not replace it.
**Scope later (1 to 3 years):** sell C-vet to other vets, with C-gate or without it.
**Status:** Phases 1 to 6 are built, and the screens were reviewed and corrected (section 7f). C-vet is off by default, and in test mode. Next: the test on Tim's phone (section 8), then phase 7 (the trial).
**Date:** 16 November 2026.

Names. **C-gate** is the yard product. Older documents call it Yardway. **C-vet** is the vet product. This document sets the structure and the order of the work. `VET_TRIAL_PLAN.md` stays the description of Tim's screens.

---

## 1. Decisions made

| # | Decision | Why |
|---|---|---|
| 1 | Two products: C-gate for the yard, C-vet for the vet. | The yard manager and the vet do different jobs and are different customers. |
| 2 | One backend and one database for the two products. | One horse record. No copy to keep in step. One codebase for a small team. |
| 3 | C-vet is a companion to the practice system for the trial. | A replacement is 5 to 10 times the work, and Tim has 16 years of data in the old system. |
| 4 | C-vet has its own address (`/c-vet/`), its own screens and its own installed app. | The vet does not need the yard screens. The yard must not see the vet screens. |
| 5 | C-vet is an installable web app first. | No app store, one codebase, same sign-in. Phase 1 tests if an iPhone permits this. |
| 6 | C-vet works with no signal after one download. It sends by itself when the app is open and has a signal. There is also a **Sync** button. | The yard has no signal. |
| 7 | The phone test comes before all other build work. | If the iPhone fails the test, the plan for the app changes (section 9, Risk 1). |
| 8 | Compact screens and fast pages are a rule, with limits that a script checks. | The vet uses the app with one hand between horses. A slow or long screen will not be used. |
| 9 | A C-vet clinical note is a new record that belongs to the practice. The yard's "vet visit" log in C-gate stays as it is, as the yard's own record. | The yard's log is what yard staff wrote down for the yard's bills and reminders. It is not the vet's clinical record. To hide it from the yard would break a C-gate feature and move no clinical record to the practice. |
| 10 | C-vet has its own `Visit` record, which belongs to the practice. It does not use `ProviderVisit`, the yard's record of a farrier's day. | The visit is the practice's record of its own work and its own bill. Phase 6 sends the yard what the yard needs from it. It also means the vet work and the farrier work do not change the same code. |
| 11 | **Test mode** is on until the trial with real clinical notes starts (`CVET_TEST_MODE`). Each note made in test mode is marked. The app shows "test notes". An administrator can delete a practice's test notes without reading them. | The phone test and the build use the real tables, so the test proves the real path. The mark keeps test data apart from clinical records. |
| 12 | A vet's phone stays signed in for 90 days (`CVET_SESSION_IDLE_AGE`, `CVET_SESSION_ABSOLUTE_AGE`). C-gate staff keep 7 days idle and 30 days in total. | A vet who is asked for a password at the yard has no signal to sign in with. To cut off a lost phone at once, untick the login in its practice. |
| 13 | Prices are typed and shown with VAT included, as the owner pays them. Each price has a VAT rate (20% unless changed). | A vet says "£65 for a booster" to an owner. The invoice (phase 6) shows the VAT part. Tim must confirm (open decision 9). |
| 14 | A note that was sent is not changed on the phone. A correction is a new note. A note that is not sent yet can be removed. | A clinical record is corrected, not replaced. This is also the simplest rule that cannot lose or duplicate data in a sync. |
| 15 | The price list belongs to the practice. The vet sets it in C-vet. It is not in the Django admin. | Section 3: the yard does not see the vet's prices. |
| 16 | Phase 4 was built with defaults in place of two answers from Tim: phone first (the same screens work on an iPad), and a note that holds text, items, a recheck date, a photo and a voice recording. | To keep the build moving. The two questions stay open (decisions 3 and 9 in section 10). |
| 17 | **The practice's client is the yard.** The vet bills the yard for a visit, and the yard invoices each owner for their horse. For each yard this can be set the other way: the vet bills each owner. | This is how Tim works: he deals with the yard's owner, who re-invoices the horse owners. C-gate already makes those invoices and already splits a charge between co-owners. |
| 18 | When the vet bills owners (not the yard) and a horse's shares do not add up to 100%, the owners on record pay all of the bill, in proportion to their shares. | The owner of C-gate decided this. A part of a bill with no payer is never collected. |
| 19 | **Spoken notes use the phone's own dictation.** The vet taps the microphone on the keyboard and speaks; the words go into the note box. C-vet does not send audio to a speech service. | It is free. On recent iPhones it works with no signal. No audio leaves the phone, so there is no service to pay and no data agreement to sign. A server speech service is added only if dictation fails the phone test. |
| 20 | The app reads a recheck date from the words of the note ("recheck in two weeks") and offers it. The vet taps to use it. Nothing is set without the tap. | Less typing, and the vet stays in control of the record. |
| 21 | What goes back to the yard is written as the yard's own records, the way its staff would type them: a vaccination record, a vet visit record with its cost. C-gate's existing code then makes the charge, splits it between co-owners and puts it on the owners' invoices. | No second billing route to keep correct. The yard sees and changes these records with the screens it already has. |
| 22 | **Nothing goes to the yard in test mode.** A test bill is made, with a number of its own (TEST-…). No record, charge, cost or email is. | The phone test must not put a charge on a real owner's invoice. |
| 23 | A yard that charges VAT gets each horse's part without VAT. A yard that does not gets the full amount. | A VAT-registered yard takes the vet's VAT back and its own invoices add VAT again. A yard that is not registered passes on what it paid. Confirmed by the owner of C-gate. |
| 24 | A bill never changes. Work that arrives after a bill goes on a further bill. | An invoice that was issued must not change. |
| 25 | **The vet or the practice office chooses when to bill a yard:** after each visit, or once a month. The choice is on the Bills page. With "once a month", the bill is made on the 1st for the month before, and **Bill it now** makes it on any day. In both cases the yard's records and the owners' charges are written when the visit is finished. | Some yards want a bill for each visit and some want one statement. The owner's charge must not wait for the vet's monthly bill, or the yard's own invoices are late. |
| 26 | **The yard can add a margin** to each vet item when it becomes an owner's charge (a percentage, set in the link to the yard). The vet's bill and the yard's cost do not include it. The yard can change one charge by hand before it invoices. | The yard normally adds a margin, and sometimes does not. |
| 27 | The limit on the size of the app's files is on what the phone downloads: under 40 KB compressed, and under 150 KB as written. Before, it was 100 KB as written. | The written size grows with the comments in the code, which cost the phone nothing. The phone downloads the compressed files once and then opens the app from its own store. Now: 27 KB compressed, 102 KB as written. |

## 2. What each product is for

| | C-gate | C-vet |
|---|---|---|
| User | Yard manager and yard staff | Vet (Tim), later the practice office |
| Client | The horse owners | The yard (decision 17) |
| Main question | Where is each horse, and who owes the yard money? | At this yard: what did I find, what did I do, and what does the yard owe the practice? |
| Shows | Locations, livery, monthly invoices, yard reminders | The yard and its horses, each horse's owner, clinical history, notes, the practice's bill for the visit |
| Device | Computer and phone, with a signal | Phone and iPad, with or without a signal |
| Does not show | Clinical notes | Locations and livery money, except the field name to find the horse |

## 3. Who owns which data

The yard database is the shared source for the horse's **identity**. It is not the owner of every record about the horse.

| Data | Owner | Yard sees | Vet sees |
|---|---|---|---|
| Name, microchip, passport, photo | Shared | Yes | Yes |
| Owner, co-owner shares, who pays | Shared | Yes | Yes |
| Location, stable, livery rate, yard invoices | Yard | Yes | Field name only |
| Clinical notes, diagnosis, photos of the horse's condition (C-vet) | Practice | No | Yes |
| The yard's vet visit log (C-gate, Health → Vet visits) | Yard | Yes | No (open decision 7) |
| Medicines given, batch, withdrawal period | Practice | Food-chain status only | Yes |
| The vet's price list | Practice | No | Yes |
| The bill for a visit: each horse's items and amounts | Practice, sent to the yard | Yes: it is the yard's bill, and the yard invoices each owner from it | Yes |
| Vaccination dates and next due dates | Practice, sent to the yard | Yes | Yes |
| Follow-up dates ("Recheck due 24 Nov") | Practice, sent to the yard | Date only, no reason | Yes |

Why this split:

- Clinical records belong to the practice. The yard is the practice's client for the bill. The bill says what was done and what it cost. It does not carry the clinical note. Open decision 11 asks if a yard, as the client, may read the notes for its horses.
- A vet visits many yards. Most of them will not use C-gate. When C-vet is sold, it must hold horses and clients that no C-gate yard holds.
- Because Tim is family, it is easy to give him a view of the yard's records. That works for the trial, but it puts the clinical records in the wrong place. It is cheap to put them in the right place now and costly later.

## 4. How it is built

```
                 ┌───────────────────────── one Django project, one database ─────────────────────────┐
 Yard staff ───▶ │  C-gate pages (server pages)          Shared: horse identity, owner, who pays       │
                 │                                       Yard:   placements, livery, yard invoices     │
 Tim's phone ──▶ │  C-vet API (JSON)  ◀── sync ──▶       Practice: visits, notes, medicines, invoices  │
   C-vet app     │  C-vet page + service worker                                                         │
   (stored on    └──────────────────────────────────────────────────────────────────────────────────────┘
    the phone)
```

Four parts:

1. **The ownership seam (built, section 7a).** A `Practice` record. A link between a practice and a yard. A `practice` field on every clinical record. For the trial there is one practice and one yard. This is the smallest part of the multi-tenant work that `market/uk-market-sizing.md` names as the blocker for sales.
2. **The C-vet API (built, section 7b).** JSON endpoints under `/c-vet/api/`. C-gate has no API today: every page is made on the server, and a page made on the server cannot open with no signal.
3. **The C-vet app (built, sections 7 and 7b).** One page that the phone stores, with its data in the phone's database (IndexedDB). It lives in the same repository as C-gate, in the `cvet` Django app.
4. **What goes back to the yard (built, section 7e).** The bill for the visit, with each horse's part as a charge in C-gate, so that the yard's monthly invoices bill each owner. Vaccination dates and follow-up dates, so that the yard's reminders stay correct. Clinical notes do not go to the yard.

What is reused from the farrier page (`FARRIER_PAGE_PLAN.md`), as a pattern and not as shared code: a visit that groups one day's work, a login linked to its organisation, a price list per provider, the QR code on the yard door, and the fence that keeps a visiting professional away from staff pages.

## 5. Offline: the rules

### Where the vet is: yard first, then horses

The horse list is the list of one yard, and its first line is the yard's name and its number of horses, for example "Palmer Farm · 24 horses". The visit and its bill belong to that yard.

Today C-gate holds one yard, so C-vet opens on that yard's horses. When a practice has more than one yard in C-vet, the app opens on the list of yards, and a tap on a yard opens its horses. That needs C-gate to hold more than one yard, or C-vet to hold a yard of its own that is not in C-gate (section 6, Later).

### What works with no signal

| Works with no signal | Needs a signal |
|---|---|
| Open the app | First sign-in |
| Horse list, owners, who pays, items due | Download or refresh the yard |
| Price list and treatment screen | A server speech service (not used: decision 19) |
| Typed and spoken notes (the phone's dictation), photos, voice recordings | Make and send the bill (the server does this after the sync) |
| End-of-visit summary and Finish visit | A change to the price list |
| The horse's notes that are on this phone | The horse's older notes from the practice's records |

### Sync rules

1. **The phone only adds.** It makes new visits, lines, notes and files. It never edits yard data. So two people cannot overwrite each other's change.
2. **Each record gets its id on the phone.** The phone sends the id with every attempt. The server stores each record once, no matter how many times it arrives. A lost reply in a weak signal cannot make a second bill.
3. **Nothing leaves the phone's storage until the server confirms it.** The phone deletes its copy of a photo or a recording only after the server says it holds it.
4. **The server decides who pays, at sync time.** If an owner or a share changed during the visit, the server uses the current one and shows the change on the summary.
5. **Tim always sees the count.** "3 notes not sent" stays on the screen until it is "Everything is sent". An iPhone cannot send while the app is closed, so Tim must see the count reach zero before he puts the phone away.
6. **A sign-in that ends does not lose data.** The phone keeps every record and asks for a sign-in. A vet's phone stays signed in for 90 days (decision 12).
7. **A note is accepted when the yard has changed.** The horse left, or the link to the yard ended, after the phone downloaded the list. The server stores the note under the name the phone had, with no link to the yard's horse. The vet did the work: the record is kept.

### Speed and layout rules

C-vet is used with one hand, outdoors, between horses. Every screen in every phase obeys these rules. The check script measures the first four.

| Rule | Limit |
|---|---|
| Open the app, from the tap on the icon to the horse list | Under 1 second, with or without a signal |
| Page files (script, styles) | Under 40 KB as the phone downloads them (compressed), and under 150 KB as written (decision 27). No web fonts, no framework. Now 27 KB and 102 KB. |
| Horses on the first screen of a phone | 8 or more |
| Tap targets | 44 px or more |
| A tap shows its result | At once. No screen waits for the network. The app reads and writes the phone's storage, and the sync runs behind it. |
| A weak signal | Never slows a screen. The stored copy shows first. |
| Lines per horse in a list | 2: the name, then owner and location. More detail shows on search or on a tap. |
| Typing | As little as possible. Tap a horse, tap a price, speak a note. |

Why this is fast: the phone holds the page and the data. To open the app or to tap a horse uses no network at all.

### Data on the phone

The phone holds owner names and, in the trial, clinical notes that are not sent yet. Before the trial with real clinical notes:

- **Clear this phone** removes the horse list and every note. The sign-in stays. It works with no signal.
- **Sign out** removes them too, removes the stored app, and ends the sign-in. It needs a signal.
- The phone deletes its copy of a photo or a recording when the server confirms it. It keeps the text of sent notes until Clear or Sign out.
- A lost phone: an administrator unticks the login in its practice (Django admin → C-vet → Practices). The next request from the phone is refused. The phone lock protects the data that is on it. This must be in Tim's instructions.

## 6. Phases

| Phase | What | Estimate | Done when |
|---|---|---|---|
| **1. Offline app** | The app on Tim's phone: download horses, save notes, photos and recordings with no signal, sync. | Built. Test: 1 to 2 weeks of Tim's use. | Section 8 pass list. |
| **2. Ownership seam** | `Practice`, the practice-to-yard link, `practice` on clinical records. Vet role. Clinical notes hidden from yard roles. | Built. | A yard login cannot read a clinical note by any route. Tests prove it (section 7a). |
| **3. API and sync** | Yard download, note upload with ids from the phone, visits, files, long sign-in for the phone, clear on sign-out, test mode. | Built. | The phase 1 checks pass on the real records, not on test records (section 7b). |
| **4. Screens** | Horse list with due items, treatment screen, price list with VAT, several horses at once, end-of-visit summary and Finish visit. | Built. | Tim records a 10-horse visit with no signal and with no help (section 8, step 12). |
| **5. Spoken notes** | The phone's own dictation types the note (decision 19). The app offers a recheck date from the words of the note. The yard as the client: yard name on the list, bill to the yard on the summary. | Built. | Fewer than 1 spoken note in 5 needs a large change (section 8, steps 6 and 7). |
| **6. The bill to the yard, and records back** | One bill for a visit, in the practice's name, with VAT, to the yard. Each horse's part into C-gate as a charge. Vaccination and recheck dates into the yard's records. The bill as a cost in the yard's books and by email. A CSV file for the practice's accounts. For a yard set to "bill each owner": one bill for each owner. | Built. | The bill for a visit exists the same day, and each horse's part is on its owner's next yard invoice with no re-typing (section 7e). |
| **7. Trial** | Tim uses C-vet at our yard for 8 to 12 weeks. | — | The measures in `VET_TRIAL_PLAN.md` 7.6. |
| Later | More than one yard for a practice, with the list of yards first. Yards that do not use C-gate. The practice's own Xero. A monthly statement. Bills by email to owners. A server speech service, if dictation is not good enough. Pay-now links, medicines record, insurance claim pack, a second practice. | — | — |

Phases 2 to 6 are about 6 to 7 weeks of build. This agrees with the 4 to 6 weeks in the trial plan, plus the ownership seam.

**The farrier page.** A separate team does the real-phone check of the farrier page. The two teams change the same visit code (`ProviderVisit` in `health/models.py`). Agree who changes it, and when, before phase 3 starts.

## 7. Phase 1: what is built

The app at `/c-vet/`, in the `cvet` Django app. Phase 1 built it as a test page with its own test table. Phase 3 moved it to the real records (section 7b), so there is one app and one path to test.

| Part | File | What it does |
|---|---|---|
| The page | `templates/cvet/app.html`, `static/cvet/app.js`, `static/cvet/app.css` | Horse list, search, note form with photo and voice, the list of notes, the unsent counter, Sync, the details of this phone, Sign out, Clear this phone. |
| Logic with tests | `static/cvet/app_core.js` | Ids, one visit for each day, the send order, what to do with each server reply. Tested by Node. |
| Service worker | `templates/cvet/sw.js` | Stores the page and its files on the phone. Stores no data. |
| Installed app | `static/cvet/manifest.json` | "C-vet" on the home screen, separate from C-gate. |
| Data on the phone | IndexedDB, in `static/cvet/app.js` | The horse list and the notes. Photos and recordings until the server confirms them. |

Safety:

- **Off by default.** Every `/c-vet/` address is a 404 until `CVET_ENABLED=True` is set.
- **Access:** the Vet role and membership of a practice (section 7a). The horse list needs the practice's open link to the yard. Yard staff, administrators and a farrier login cannot open it.
- **Test mode is on by default** (decision 11). Notes are marked as test notes.
- **Uploads:** a photo must be a real image. A recording must be an audio type on a list. Files get a name made by the server. Limits: 8 MB each, 300 notes an hour for one login.
- **Microphone:** allowed on the C-vet page only. It stays off on every other page.

Checked before the phone test:

| Check | Result |
|---|---|
| Server tests (`cvet/tests.py`, `tests_ownership.py`, `tests_visit.py`, `tests_billing.py`) | 160 pass |
| Logic tests (`static/js/tests/cvet_app_core.test.js`) | 34 pass |
| Chromium, phone size (`tools/qa/cvet_offline_qa.js`), signed in as a vet, from a cold start: a whole visit with no signal (sections 7c and 7d), a reload in the middle of a note, signal back, one lost reply, read the records back, the bill, add a price, sign out | 74 of 74 pass in test mode, and 74 of 74 with test mode off. With test mode off, C-gate held the bill, 4 vaccination records, 1 vet visit record, charges for 3 owners and 1 cost (section 7e). |
| Speed and layout, in the same script, with no signal | Opens in 53 ms. 10 horses on the first screen of an iPhone 13, above the tabs. Page files: 27 KB compressed. Rows, tabs and buttons 44 px or more. No sideways scroll on any screen, and the top bar is one line on a 320 px phone. |

Chromium on a computer is not an iPhone, and it is faster than one. The checks above prove the code. They do not prove the phone. The page measures its own open time on the phone (**This phone → Opened in**) and sends it with each note.

## 7a. Phase 2: what is built

The ownership seam. It has no new screen. It is the set of rules that every later phase builds on.

| Part | File | What it does |
|---|---|---|
| `Practice` | `cvet/models.py` | The veterinary practice: the owner of the clinical records. |
| `PracticeMember` | `cvet/models.py` | A login that works for the practice. One login, one practice. |
| `PracticeYardLink` | `cvet/models.py` | The agreement between the practice and the yard. While it is open, the practice sees the yard's horses. It also holds what the practice sends back to the yard: vaccination dates, follow-up dates. |
| `ClinicalNote` | `cvet/models.py` | What a vet found and did for one horse on one day. It has a `practice`. It keeps a copy of the horse's name and numbers. Every change keeps the text before it. |
| The gate and the scope | `cvet/access.py` | The only way to read a clinical note: `notes_for(practice)`. The practice comes from the signed-in login, never from an address or a form. |
| The Vet role | `cvet/migrations/0002_seed_vet_role.py`, feature `cvet` | Opens C-vet and nothing else. |
| The fence | `cvet/middleware.py` | Sends a Vet login back to C-vet from every yard page, the Django admin and uploaded files. |
| Set-up screen | Django admin → C-vet → Practices | The practice's details, its logins and its link to the yard. It shows who can read the notes. It does not show the notes. |

### Who can read what

| Login | C-vet | Clinical notes | Yard pages |
|---|---|---|---|
| Vet role and a member of the practice | Yes | Its own practice's notes only | No. Each one sends the login back to C-vet. |
| Vet role, not a member | A page that says the login is not set up | No | No |
| Yard staff, any role | No | No | As the role permits |
| Yard administrator or superuser | No, unless made a member of the practice | No | Yes |
| Farrier | No | No | The farrier page only |
| A vet of a second practice | Yes | Its own practice's notes only | No |

### How the rule is kept

- **The sweep.** A test signs in as a superuser and requests every GET address of the site, the Django admin included, with the ids of a real horse, owner and note, and with the note's text as a search. The note's text must come back on no page. A second test proves that the sweep finds text that the yard does show.
- **The tripwire.** A test fails if code outside the `cvet` app names the clinical models. The person who writes the first yard page that reads them sees the failure before the page ships.
- **No way back from a horse.** A `Horse` has no link to its notes, so a yard page that holds a horse cannot follow it to them.
- **Not in the Django admin, not in the audit log page, not in the owner data export.** Tests check each one.

Tests: `cvet/tests_ownership.py`, 30 pass.

### What the rule does not reach

- **The database and the backups.** A person with access to the server reads every table. The off-site backups hold the clinical notes. This is the same as for every hosted practice system: the host can read the data. It must be in the agreement between the yard and the practice.
- **A superuser can make itself a member of the practice.** The membership history records who did it and when.
- **Files.** Phase 3 stores clinical photos and recordings with the same rule: practice members only (section 7b). In the file store itself (the disk or the bucket) they are files like all others: a person with access to the server can read them.

### What phase 2 does not do

- Phase 2 wrote no clinical note. Phase 3 does (section 7b).
- The yard's vet visit log (C-gate, Health → Vet visits) does not change (decision 9).
- No data goes back to the yard yet: phase 6.

## 7b. Phase 3: what is built

The API and the sync for real clinical notes.

| Address | What it does |
|---|---|
| `GET /c-vet/api/session/` | Who is signed in, the practice, test mode, and a token for uploads. |
| `GET /c-vet/api/yard/` | The yard's horses: name, microchip, passport, field, owner. Needs the open link to the yard. |
| `POST /c-vet/api/notes/` | One clinical note, with its photo and its recording. Stored once for each id from the phone. |
| `GET /c-vet/api/notes/?horse=<id>` | The practice's own notes, newest first. |
| `GET /c-vet/api/files/<id>/` | A photo or a recording, as a download, for a member of its practice. |

| Record | What it holds |
|---|---|
| `Visit` | One day's work by one vet. The phone makes its id with the first note of the day (decision 10). |
| `ClinicalNote` | The note, its visit, the horse and a copy of its name and numbers, the vet, the time on the phone. |
| `ClinicalFile` | A photo or a recording of a note. Stored under `cvet/`. |
| `SyncReceipt` | How the note arrived: time on the phone, time on the server, number of tries, sizes, and if each file arrived complete. It holds no clinical text. |

Rules that tests hold (`cvet/tests.py`):

- **Stored once.** The same note sent twice is one note, one file, one visit. The repeat changes nothing but the count of tries.
- **Nothing is refused for a reason the vet could not see at the yard.** A horse that left, or a link that ended after the download, does not lose the note (sync rule 7).
- **Another practice learns nothing.** Its note id gives a refusal with no detail. Its visit id does not join its visit. Its file address is "not found".
- **Files have the same rule as notes.** A yard administrator and a superuser cannot download a clinical file through C-vet, and the yard's media view refuses everything under `cvet/`.
- **Receipts are safe to read.** The admin screen for receipts shows no note text and no way to the note.
- **A vet's phone stays signed in for 90 days.** C-gate staff keep their 30-day limit. A login that stops being a Vet login loses the long sign-in.
- **A login removed from its practice is refused on its next request**, with its sign-in still valid. This is how a lost phone is cut off.

### Test mode

`CVET_TEST_MODE` is `True` by default.

- Each note, visit and receipt made in test mode is marked.
- The app shows **test notes** at the top of the screen.
- Django admin → C-vet → Practices → select the practice → **Delete the test notes of the selected practices**. This deletes the notes, their files and their change history. It reads none of them. The receipts stay, so the test results are kept.
- Set `CVET_TEST_MODE=False` when the trial with real clinical notes starts (phase 7). Do this after open decisions 1, 5 and 8 are closed and a vet adviser has checked the record design.

### What phase 3 does not do

- The note has one text field in the app. The treatment, the follow-up date, the price list and the end-of-visit summary are phase 4.
- A visit is not finished yet: the Finish button is on the phase 4 summary screen.
- The vet cannot change a note that was sent. Phase 4 decides how a correction is made. The record already keeps the text before each change.
- The phone does not download old notes. To read history with no signal is phase 4, with open decision 5 (a PIN).

## 7c. Phase 4: what is built

The screens for a yard visit. All of them work with no signal, except a change to the price list and the load of older notes.

| Screen | What the vet does |
|---|---|
| **Horses** | The list, with the horses that have something due at the top: the most overdue first. Each row: name, owner, field, and a label such as "Flu/Tet overdue 6 Nov". A tick shows the horses already on today's visit. Search by name, owner, field, microchip or passport. |
| **Select** (on Horses) | Tap **Select**, tap the horses, tap **Add item**, tap an item. The item goes on every selected horse in one step, for example a booster for 10 horses. |
| **A horse** | The owner, or the owners and their shares. What is due. Tap items from the price list; tap again for 2. Change the quantity or the price for this horse. **Other item** for a line that is not on the list. A note, typed or spoken. A recheck date: None, 1 wk, 2 wk, 4 wk, 3 mo, or a date. A photo. A voice recording. **Save** goes back to the list. |
| **History** (on a horse) | The notes for this horse that are on this phone. **Load older notes** gets the practice's records for the horse, with a signal. A note that is not sent yet can be removed. |
| **Visit** | The end-of-visit summary: who the bill goes to (the yard), each horse with its items, its total and its owners, the total and the VAT in it. For a yard set to "bill each owner", it also shows what each owner pays. **Finish visit** closes the visit. A note after that starts a new visit. |
| **Prices** | The practice's price list: add, change, hide. Prices include VAT (decision 13). Needs a signal. |
| **Phone** | Sync now, Download horses, the details of this phone, Sign out, Clear this phone. |

The line under the top bar says what the app did ("Willow saved.", "3 notes sent."). The counter in the top bar is also the Sync button.

| Address | What it does |
|---|---|
| `GET /c-vet/api/yard/` | Now also: who pays for each horse and each share, what is due for each horse, and the price list. |
| `POST /c-vet/api/notes/` | Now also: the items done (each with its own description, quantity, price and VAT rate) and the recheck date. |
| `POST /c-vet/api/prices/` | Save one price. Stored once for each id from the phone. |
| `POST /c-vet/api/visits/<id>/finish/` | Close a visit. The first call sets the time. |

| Record | What it holds |
|---|---|
| `PriceItem` | One line of the practice's price list: name, price with VAT, VAT rate. |
| `NoteLine` | One item done for a horse on a visit. The description, the price and the VAT rate are copied from the price list when the vet taps the item. |
| `ClinicalNote.payers` | The horse's owners and their shares, as the yard's records said when the note arrived. A copy. Phase 6 uses it. |

Where the data comes from (`cvet/yard.py`, read only):

- **The owners:** the horse's ownership shares in C-gate. If there are none, the owner on its open placement, at 100%. This is the rule that C-gate's own invoices use.
- **Vaccination due:** the next due date of the newest dose of each vaccination type in C-gate.
- **Recheck due:** the date on the practice's own newest note for the horse. A newer note with no date clears it.

Rules that tests hold (`cvet/tests_visit.py`, `static/js/tests/cvet_app_core.test.js`):

- **Money is exact.** The phone counts in whole pence. The split between co-owners adds up to the total, to the penny.
- **Work done keeps its price.** A later change to the price list does not change an item already on a note.
- **The server decides who pays, when the note arrives** (sync rule 4). A later change of owner at the yard does not rewrite the note.
- **The price list is the practice's.** Another practice cannot read or change it. The yard cannot read or set it. It is not in the Django admin.
- **Finish is safe to send twice,** and a note that arrives after Finish still joins its visit.
- **The download takes the same number of database queries for 1 horse or for 20.**
- **Test notes leave the history and the due list when test mode is turned off.**

### What phase 4 does not do

- **Horse photos on the list.** Each photo must be stored on the phone and the vet's login cannot read the yard's files. Later.
- **Dental due.** C-gate has no dental record to read a date from.
- **Open the yard by QR code.** There is one yard. It is needed when a vet has more than one.
- **Medicines** (batch, dose, withdrawal period): later, as in the trial plan.
- **The yard's own vet visit log as history:** open decision 7.
- **Old notes on the phone with no signal:** open decision 5 (a PIN) comes first.
- **Invoices and dates back to the yard:** phase 6. Phase 4 stores what they need: the items, the prices, the VAT rates and the payers.

## 7d. Phase 5: what is built

Spoken notes, and the yard as the practice's client.

### Spoken notes

| Part | What it does |
|---|---|
| Dictation | Under the note box the app says: "To speak the note, tap the microphone on the keyboard." The phone types the words into the note. C-vet does nothing with audio for this: the phone does all of it (decision 19). |
| Recheck from the words | The app reads the note as it is typed or spoken. For "Recheck in two weeks", "review in 3 months", "see again tomorrow", "recheck in a fortnight", it shows a button: **Use 30 Nov: you said "Recheck in two weeks"**. One tap sets the recheck date. The last such phrase in the note wins. |
| What it does not read | "Lame for two weeks", "next due in 12 months", "owner to call in a week": none of these is a recheck by the vet, and the app offers nothing. Tests hold both lists (`cvet_app_core.test.js`). |
| Voice recording | It stays, as a recording that goes with the note. The app does not type it. Use it when dictation is not possible. |

The parts of a note are already apart, so nothing has to sort the text: the items done are bill lines, the recheck is a date, and the note text is what the vet found.

**Not tested here:** dictation is the phone's own function, and a test browser on a computer does not have it. Tim tests it on his phone, in aeroplane mode (section 8, steps 6 and 7). On recent iPhones, dictation in English works on the phone with no signal. On an old iPhone it can need a signal.

**If dictation fails the test:** a server speech service types the voice recording after the sync, and Tim checks the text before it goes on the note. This needs a paid service, a UK GDPR data agreement with it, and a check screen in the app. It is not built.

### The yard as the client

| Part | File | What it does |
|---|---|---|
| Who the bill goes to | `PracticeYardLink.bill_to` | **The yard** (the default) or **each horse owner**. Set in the Django admin, C-vet → Practices, in the link to the yard. |
| The yard's name | `cvet/yard.py` | Read from C-gate's business settings. |
| On each visit | `Visit.yard_name`, `Visit.bill_to` | Copies, made when the visit starts. A bill for work done does not change when the yard is renamed or the arrangement changes. |
| On the phone | `static/cvet/app.js` | The horse list is headed "Palmer Farm · 24 horses". The summary says "Bill to Palmer Farm" and names each horse's owners, for the yard's own invoices. |
| The split rule | `static/cvet/app_core.js`, `splitByShare` | For a yard set to "each horse owner": the owners on record pay all of the bill, in proportion to their shares (decision 18). The parts add up to the total, to the penny. |

### What phase 5 does not do

- **No bill is made or sent yet.** Phase 6 does that, and puts each horse's part into C-gate.
- **One yard.** The list of yards comes when there is a second yard (section 5).
- **No server speech service** (decision 19).

## 7e. Phase 6: what is built

The bill to the yard, and what goes back to the yard. This phase is server work. The app changes little.

### What happens when the vet taps Finish visit

After every note of the visit has arrived, the server does this (`cvet/billing.py`). Steps 2 to 4 are done at once. Steps 1, 5 and 6 are done at once for a yard that is billed after each visit, and on the 1st of the next month for a yard that is billed once a month (decision 25):

| Step | What | Where the yard sees it |
|---|---|---|
| 1 | **Makes the bill.** One bill for the visit, to the yard, with a number such as HV-0001. Each line: horse, item, quantity, price, amount. Totals: net, VAT and total at each VAT rate. | — |
| 2 | **Records each vaccination** in the yard's vaccination records: the horse, the type, the date. C-gate works out the next due date. | Health → Vaccinations. The yard's reminders use it. |
| 3 | **Records the other items and the recheck date** as a vet visit record for the horse. The text is the item names, for example "2 x Sedation; Visit fee". The recheck is a date with no reason. | Health → Vet visits. The recheck is in "Needs action". |
| 4 | **C-gate makes a charge** from each record, for the horse's owner, as it does for a record that staff type. The yard's margin is added, if it has one (decision 26). | Charges. Then the owner's next invoice. A co-owned horse is split by C-gate, as now. |
| 5 | **Records the bill as a cost**: supplier, bill number, net amount and VAT. | Costs. |
| 6 | **Emails the bill to the yard**, with each horse's items and the problems the yard must act on. | The yard's email address in C-gate's settings. |

Then the app shows the bill on the Visit screen: "All sent to the practice. Bill HV-0001 to Palmer Farm, £282.50."

### Rules that tests hold (`cvet/tests_billing.py`)

- **Nothing clinical goes to the yard.** The vet visit record, the charge, the cost and the email hold item names, amounts and dates. A test puts marked words in the note and looks for them in all four.
- **The charge reaches the owner's invoice.** A test runs C-gate's monthly invoicing after a visit and finds the vet's item on the owner's invoice.
- **Once.** Finish sent twice makes one bill, one set of yard records, one cost and one email.
- **A bill never changes.** A note that arrives after Finish goes on a further bill for the same visit, with the next number.
- **Numbers have no gaps.** HV-0001, HV-0002, HV-0003. Test bills count apart and do not use a real number.
- **Test mode sends nothing to the yard** (decision 22).
- **A horse with no owner** is on the bill, and its cost is recorded, but C-gate makes no charge. The email tells the yard what to do.
- **A horse that left C-gate** is on the bill. No record is made. The email tells the yard.
- **The yard can turn each thing off** in the link: charges, vaccination dates, recheck dates.
- **After the link ends,** a bill is still made for work done. Nothing more goes into the yard's records.
- **Only the practice sees its bills,** chooses when to bill, and bills. Another practice gets "not found". A yard administrator gets "no access".
- **Monthly.** With "once a month", Finish makes no bill, and the owner's charge is made at once. The monthly run makes one bill for the visits before the 1st, with one cost and one email. A second run bills nothing twice. An open visit is not billed.
- **The margin** is on the owner's charge, and not on the vet's bill or on the yard's cost. For a yard that charges VAT, it is added to the amount without VAT.

### For the practice

| Part | Where |
|---|---|
| The list of bills, and each bill as a page to print or to save as a PDF | In the app: **Phone → Bills**. It needs a signal. Address: `/c-vet/bills/`. |
| When to bill the yard: after each visit, or once a month | **Bills**, at the top. |
| What is finished and not on a bill yet, and **Bill it now** | **Bills**, at the top. |
| All bill lines as a CSV file, for the practice's accounts | **Bills → Download all as a CSV file**. One row for each line: bill, date, bill to, horse, item, quantity, price, VAT rate, net, VAT, total. |
| The practice's letters for bill numbers, its address, VAT number and how to pay | Django admin → C-vet → Practices. |
| Which price is a vaccination | In the app: **Prices**, "Records a vaccination at the yard". The list is the yard's own vaccination types. |

### A yard set to "bill each owner"

One bill for each owner, for that owner's part (decision 18). The yard still gets the vaccination and recheck dates, with no cost. No charge and no cost goes into C-gate, and the yard gets no bill email. A horse with no owner on record is billed to the yard. The practice sends these bills itself: C-vet does not email owners.

### Set-up before the first real visit

1. Django admin → C-vet → Practices: the practice's address, VAT number, letters for bill numbers, and how to pay.
2. C-gate → Settings: the yard's email address, and its VAT rate (0 if the yard is not VAT-registered).
3. Django admin → C-vet → Practices, in the link to the yard: the yard's margin on vet items (0 for none).
4. In the app, **Prices**: mark each vaccination price with its vaccination type.
5. In the app, **Phone → Bills**: choose when to bill the yard.
6. Set `CVET_TEST_MODE=False`. Delete the test notes first (section 7b).
7. After the first real visit, check in C-gate: Charges, Costs, the horses' health records, and the email.

### What phase 6 does not do

- **The practice's own Xero.** C-gate's Xero link is the yard's. The practice gets a CSV file. A direct link, or a file in the exact form of Tim's practice system, needs Tim's answer (open decision 2).
- **Bills by email to owners,** for a yard set to "bill each owner".
- **Payment of a bill.** C-vet does not record that the yard paid. The yard's cost record does not mark it as paid.
- **A change to a bill.** A wrong bill needs a credit, which is not built. The yard can change its own charge before it invoices the owner.
- **Pay-now links, medicines, insurance claim pack:** later.

## 7f. The review of the screens

Every screen and state was captured at three sizes: a small phone (320 px wide), an iPhone 13 and an iPad. A script checked each one for text that is cut, tap targets under 44 px, low contrast, controls with no label and sideways scroll. Then each screen was read by eye.

No contrast failure, no control without a label, no sideways scroll and no script error was found. These were found and corrected:

| Found | Corrected |
|---|---|
| On a 320 px phone the top bar took two lines. | One line. The label is **TEST**. The counter says "All sent". "No signal" shows only when there is none. |
| "Signal" showed when the phone had a network with no way out. | A request that gets no answer also shows "No signal". |
| The counter is also the Sync button, and it was 28 px high. | It looks the same and takes a 44 px finger area. |
| A long horse name was cut short in the list. | The name takes a second line. |
| In Select, the last horses were under the bar. | The list has room under it. |
| The item list for several horses did not say how many. | "Add item to 3 horses". A tap outside the list closes it. |
| A refused Save gave its reason in small grey text under the button, and the wrong box could be off the screen. | The reason is in red next to Save, the screen goes to the wrong box, and the cursor is in it. |
| Save was at the end of a long screen. | Save stays at the foot of the screen. |
| A photo or a recording could not be taken off a note. | **Remove** next to each one. |
| A recording showed no time. | "Recording: 12 s of 60." |
| The offered recheck date looked like a choice already made. | A dashed outline: an offer. |
| **A note being written was lost if the app closed or the phone reloaded it.** An iPhone can reload a web app after the camera is used. | The note is kept on the phone as it is typed, with its items, recheck date, photo and recording. The app opens on it again and says so. |
| On Prices, the form was under the list, and its buttons took two lines on a small phone. | **Add a price** at the top opens the form at the top. Buttons: Save, Hide, Cancel. A saved price is confirmed in the line under the top bar. |
| Buttons side by side broke their words on a small phone. | They go one under the other when there is no room. |
| A finished visit did not say what to do next. | "A new note starts a new visit." For a monthly bill: "It goes on the monthly bill." |
| On the Bills pages the links were small and a bill number broke over three lines. | Links have a 44 px area. Numbers and dates stay on one line. |

Left as they are, on purpose:

- **The owner and field line of a horse** is cut with "…" when it is long. The horse screen shows it in full.
- **A sent note cannot be changed** (decision 14).
- **Questions use the phone's own Yes/No box** ("Finish this visit?"). It is plain, and it works with no signal.

Not reviewed here: the screens on a real iPhone in sunlight, with gloves or with wet hands. That is the phone test.

## 8. The test on Tim's phone

### Set-up (once, by an administrator)

1. Set `CVET_ENABLED=True` on the server and deploy. Leave `CVET_TEST_MODE` as it is (on).
2. In C-gate, **Settings → Users**: add a login for Tim with the role **Vet**.
3. In the Django admin, **C-vet → Practices → Add**: type the practice name. In **Practice yard link**, leave today's date. In **Practice members**, choose Tim's login. Save.

Tim's login opens C-vet only. It cannot open a C-gate page.

### Set-up (once, by Tim, with a signal)

1. Open `/c-vet/` in Safari and sign in.
2. Tap Share, then **Add to Home Screen**.
3. Open **C-vet** from the home screen. Sign in again if it asks: an installed app has its own storage.
4. Tap **Download horses**.
5. Tap **Prices** and add the items of a normal yard visit, for example "Flu/Tet booster", "Sedation", "Visit fee".
6. Tap **Phone**. "Opens with no signal" must say Yes. The top of the screen must say **test notes**.

### The test (Tim, at the yard)

| # | Step | Pass |
|---|---|---|
| 1 | Turn on aeroplane mode. Open C-vet from the home screen. | The app opens. The list is headed by the yard's name. The due horses are at the top. |
| 2 | Find a horse with the search box. | The correct horse shows. |
| 3 | Tap the horse. Tap an item. Type a note. Tap a recheck. Save. | Save is at the foot of the screen. The app goes back to the list. The horse has a tick. The counter says "1 note not sent". |
| 3a | Start a note on a horse. Take a photo. Close the app fully. Open it again. | The app opens on that horse, with the note, the items and the photo as they were. |
| 4 | On another horse, add a photo. | The photo is ready in a few seconds. |
| 5 | On another horse, add a voice recording. Play it back. | The recording plays. |
| 6 | Still in aeroplane mode: on another horse, tap the note box, tap the microphone on the keyboard, and say "Mild heat left fore, no lameness at trot. Recheck in two weeks." | The words appear in the note box with no signal. The app offers "Use … : you said Recheck in two weeks". One tap sets the date. |
| 7 | Speak the notes of a real visit, with drug names and the words Tim uses. Count the notes that need a large change. | Fewer than 1 note in 5. |
| 8 | Tap **Select**, tap 3 horses, tap **Add item**, tap an item. | The item is on the 3 horses. |
| 9 | Tap **Visit**. | "Bill to" names the yard. Each horse, each item and the total are correct. |
| 10 | Close the app fully. Open it again. | The visit and all notes are there. |
| 11 | Tap **Finish visit**. Turn off aeroplane mode. Keep the app open. | The counter reaches "Everything is sent" with no tap. The Visit screen says "All sent to the practice" and names a test bill (TEST-…). Tap **Phone → Bills** to read it. Nothing is added to C-gate: test mode. |
| 12 | Do steps 3 to 5 in a place with a weak signal (1 bar). | Every note arrives once. |
| 13 | Save 2 notes with no signal. Do not open the app for 8 days. Then open it with a signal. | The 2 notes are there and are sent. |
| 14 | Record a real visit of about 10 horses with no signal, with no help. | Tim finishes the visit on the phone. He says what was slow or not clear. |
| 15 | Tap **Phone** and read "Opened in". | Under 1000 ms. |
| 16 | Use the app on real visits for 1 to 2 weeks. | No note is lost. Tim says the app is fast enough and the list is easy to read outdoors. |

Step 13 is the most important. Safari can clear a web app's storage after 7 days with no use. Reports disagree on whether an app on the home screen is safe from this.

### What we read from the results

Open the Django admin → C-vet → **Sync receipts**. They show how each note arrived. They do not show the notes.

| Column | What it tells us |
|---|---|
| Waited on phone | How long notes stay on the phone in real use. |
| Attempts | How often a reply is lost in a weak signal. |
| Photo intact, Audio intact | That each file arrived complete. All must be Yes. |
| Installed app | That Tim used the home-screen app and not Safari. |
| Opened in | How fast the app opened on the phone. |

### Decision at the end of phase 1

| Result | Next step |
|---|---|
| All steps pass | Continue with phase 6 as a web app. |
| Step 6 fails (dictation needs a signal on Tim's phone) | Tim types at the yard, or speaks where there is a signal. If that is not enough: the server speech service (section 7d). |
| Step 7 fails (too many spoken notes need a large change) | Add Tim's words to the phone's text replacements. If that is not enough: the server speech service (section 7d). |
| Step 13 fails (storage cleared) | Put the same web code in an app-store wrapper (Capacitor), which gives it storage that is not cleared. Add about 2 weeks and an Apple developer account. |
| Step 5 fails (no voice recording in the installed app) | Typed notes only in the web app, or the wrapper as above. |
| Step 15 fails (slow to open) | Find the slow part with Safari's tools before any other work. The limit stays. |
| Step 11 is a problem (Tim forgets to keep the app open) | Add a reminder on the summary screen. If that is not enough, the wrapper permits a send in the background. |

### When the phone test ends

1. Read the results in **Sync receipts**.
2. Django admin → C-vet → Practices: delete the practice's test notes (section 7b, Test mode).
3. Tim taps **Clear this phone**.
4. For the trial with real notes: do the set-up in section 7e, then set `CVET_TEST_MODE=False`. To close C-vet, set `CVET_ENABLED=False`.

## 9. Risks

| # | Risk | What we do |
|---|---|---|
| 1 | The iPhone clears a web app's storage, or does not send in the background. | Phase 1 tests this first. The fallback is an app-store wrapper around the same code. |
| 2 | A lost phone holds owner names, unsent notes and the text of sent notes. | Section 5, "Data on the phone". An administrator cuts the phone off in the practice's set-up screen. Open decision 5: is a PIN necessary? |
| 3 | Clinical notes become visible to the yard by mistake. | Phase 2 put them behind the practice (section 7a). A sweep test requests every page as a yard administrator, and a tripwire test stops yard code that reads them. Phase 3 gave files the same rule (section 7b). |
| 4 | RCVS rules on clinical records (revised April 2026) and UK GDPR. | A vet adviser checks the record design before the trial with real notes (phase 7). No speech service holds audio (decision 19). |
| 5 | Two teams change the same visit code. | Closed by decision 10: C-vet has its own `Visit` and does not change the farrier's code. |
| 6 | The scope grows into a full practice system. | Decision 3. C-vet covers the yard visit only. The practice system stays the full record. |
| 7 | Offline is not a selling point. Other equine vet products say they work offline (VetIT, Lupa). We did not verify their claims. | The selling point is the link to the yard: one horse list, the correct payer, co-owner splits, dates that go back to the yard. |
| 8 | The app's files grow with each screen. | The limit is on what the phone downloads (decision 27), and the check script measures it on each run: 27 KB of 40 KB. |
| 9 | C-vet now writes into the yard's records: vaccinations, vet visits, charges and costs. A fault here puts a wrong charge on an owner's invoice. | One module does all of it (`cvet/billing.py`). It uses C-gate's own charge code. Nothing is written in test mode. The yard can change a charge before it invoices. The first real visit is checked by hand (section 7e). |

## 10. Open decisions

| # | Question | Who | Needed before |
|---|---|---|---|
| 1 | Must livery owners consent before the yard sees vet dates for their horse? The yard is the vet's client and asked for the visit, so this is likely "no". | Owner of C-gate, with a vet adviser | Phase 7 |
| 2 | Which practice system holds Tim's records? Can it import a file, and in which form? Built: a general CSV file. | Tim | Phase 7 |
| 3 | Phone or iPad on visits? Built for a phone first (decision 16). | Tim | Phase 7 |
| 4 | How long must Tim's phone stay signed in? Built as 90 days (decision 12). Change the two settings if another length is better. | Owner of C-gate | Phase 7 |
| 5 | Is a PIN necessary to open C-vet? | Owner of C-gate, Tim | Before the phone holds old notes |
| 6 | Which speech-to-text service? Closed for now by decision 19: the phone's own dictation. Open again only if the phone test fails steps 6 or 7. | Owner of C-gate | After the phone test |
| 7 | Does the vet see the yard's own vet visit log and medical conditions for a horse, as history? | Owner of C-gate, Tim | Phase 7 |
| 8 | What does the agreement between the yard and the practice say about the server and the backups, which hold the clinical notes? | Owner of C-gate, with a vet adviser | Phase 7 (before real clinical notes) |
| 9 | Does Tim give prices with VAT included? What must a clinical note always contain? Are the recheck choices (1 wk, 2 wk, 4 wk, 3 mo) the correct ones? | Tim | Phase 5 |
| 10 | Shares that do not add up to 100%: closed by decision 18. | — | — |
| 11 | The yard is the practice's client. May the yard read the clinical notes for its horses, or only the bill and the dates? Built as: only the bill and the dates. | Owner of C-gate, Tim, with a vet adviser | Phase 7 |
| 12 | When does the yard get the bill: after each visit, or one statement each month? Closed by decision 25: the practice chooses. | — | — |
| 13 | Is there a yard where Tim bills the owners and not the yard? Built as a setting for each yard. | Tim | Phase 7 |
| 14 | Is decision 23 correct for our yard? Confirmed by the owner of C-gate. An accountant's check is still good practice before the first real visit. | — | — |
| 15 | What margin does the yard add to a vet item, and in which cases does it add none? Built: one percentage for the yard (decision 26), and the yard changes a charge by hand for an exception. | Owner of C-gate | Before the first real visit |

The other questions for Tim are in `VET_TRIAL_PLAN.md` 7.7.

## 11. Sources

- [RCVS: Clinical and client records](https://www.rcvs.org.uk/veterinary-professionals/conduct-and-guidance/supporting-guidance/13-clinical-and-client-records). Revised April 2026. The full text was not read for this plan.
- [RCVS: Standards and advice, spring 2026 update](https://www.rcvs.org.uk/about-us/news-and-views/features/standards-and-advice-spring-2026-update)
- [Provet: Best veterinary PMS in the UK (2026)](https://www.provet.com/blog/best-pms-uk-veterinary). A vendor's page. It describes the VetIT offline app.
- [Lupa: Best veterinary practice management systems in 2026](https://lupapets.com/blog/best-veterinary-practice-management-systems-in-2026/). A vendor's page.
- [MagicBell: PWA iOS limitations and Safari support](https://www.magicbell.com/blog/pwa-ios-limitations-safari-support-complete-guide)
- [WebKit bug 237350: home screen web app data deleted after 7 days](https://bugs.webkit.org/show_bug.cgi?id=237350)
- `docs/market/uk-market-sizing.md`, `docs/market/vet-app-market-model.xlsx`, `docs/payments/payments-plan.md`
