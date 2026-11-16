# C-vet plan: the vet's app, built on C-gate

**Goal:** Tim finishes a yard visit on his phone, with or without a signal. Every note, every bill and every record is correct before he leaves the yard. Nobody types the same data twice.
**Scope now:** Tim, our own yard. C-vet is a companion to Tim's practice system. It does not replace it.
**Scope later (1 to 3 years):** sell C-vet to other vets, with C-gate or without it.
**Status:** Phases 1 to 3 are built: the offline app, the ownership seam, and the API and sync for clinical notes. C-vet is off by default, and in test mode. Next: the test on Tim's phone (section 8), then phase 4 (screens).
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
| 10 | C-vet has its own `Visit` record, which belongs to the practice. It does not use `ProviderVisit`, the yard's record of a farrier's day. | A vet bills in the practice's name, so a vet's visit never enters the yard's books. It also means the vet work and the farrier work do not change the same code. |
| 11 | **Test mode** is on until the trial with real clinical notes starts (`CVET_TEST_MODE`). Each note made in test mode is marked. The app shows "test notes". An administrator can delete a practice's test notes without reading them. | The phone test and the build use the real tables, so the test proves the real path. The mark keeps test data apart from clinical records. |
| 12 | A vet's phone stays signed in for 90 days (`CVET_SESSION_IDLE_AGE`, `CVET_SESSION_ABSOLUTE_AGE`). C-gate staff keep 7 days idle and 30 days in total. | A vet who is asked for a password at the yard has no signal to sign in with. To cut off a lost phone at once, untick the login in its practice. |

## 2. What each product is for

| | C-gate | C-vet |
|---|---|---|
| User | Yard manager and yard staff | Vet (Tim), later the practice office |
| Main question | Where is each horse, and who owes the yard money? | What did I find, what did I do, and who pays the practice? |
| Shows | Locations, livery, monthly invoices, yard reminders | Owner, who pays, clinical history, notes, medicines, practice invoices |
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
| Vet price list, vet invoices, vet payments | Practice | No | Yes |
| Vaccination dates and next due dates | Practice, sent to the yard | Yes | Yes |
| Follow-up dates ("Recheck due 24 Nov") | Practice, sent to the yard | Date only, no reason | Yes |

Why this split:

- Clinical records belong to the practice. The owner is the vet's client. The yard is a third party.
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
4. **The records that go back to the yard.** After a sync, the server writes the vaccination dates and follow-up dates to the yard's records. The yard's reminders stay correct. Clinical notes do not go to the yard.

What is reused from the farrier page (`FARRIER_PAGE_PLAN.md`), as a pattern and not as shared code: a visit that groups one day's work, a login linked to its organisation, a price list per provider, the QR code on the yard door, and the fence that keeps a visiting professional away from staff pages.

## 5. Offline: the rules

### What works with no signal

| Works with no signal | Needs a signal |
|---|---|
| Open the app | First sign-in |
| Horse list, owners, who pays, items due | Download or refresh the yard |
| Price list and treatment screen | Voice to text (the phone keeps the audio; the server types it later) |
| Typed notes, photos, voice recordings | Make and send invoices (the server does this after the sync) |
| End-of-visit summary | |

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
| Page files (script, styles) | Under 100 KB in total. No web fonts, no framework. |
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
| **4. Screens** | Horse list with due items, treatment screen, price list with VAT, several horses at once, end-of-visit summary. Screens as in `VET_TRIAL_PLAN.md` 7.2. | 1.5 to 2 weeks | Tim records a 10-horse visit with no signal and with no help. |
| **5. Voice notes** | Record, type on the server, tidy, Tim's check, delete the audio. | 1 week | Fewer than 1 note in 5 needs a large change. |
| **6. Practice invoices and records back** | Invoices in the practice's name with VAT. Export to Xero or a file. Dates back to the yard. | 1 week | An invoice from a visit is in the practice's accounts the same day. |
| **7. Trial** | Tim uses C-vet at our yard for 8 to 12 weeks. | — | The measures in `VET_TRIAL_PLAN.md` 7.6. |
| Later | Pay-now links, medicines record, insurance claim pack, a second practice, yards that do not use C-gate. | — | — |

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
| Server tests (`cvet/tests.py`, `cvet/tests_ownership.py`) | 80 pass |
| Logic tests (`static/js/tests/cvet_app_core.test.js`) | 13 pass |
| Chromium, phone size (`tools/qa/cvet_offline_qa.js`), signed in as a vet, from a cold start: download, no signal, open again, 3 notes with photo and voice, signal back, one lost reply, read the notes back, sign out | 29 of 29 pass. The server stored 3 clinical notes, 3 files and 3 receipts from 4 uploads. |
| Speed and layout, in the same script, with no signal | Opens in 43 ms. 9 horses on the first screen of an iPhone 13. Page files: 48 KB. All tap targets 44 px or more. |

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
| The Vet role | `cvet/migrations/0003_seed_vet_role.py`, feature `cvet` | Opens C-vet and nothing else. |
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
5. Open **This phone**. "Opens with no signal" must say Yes. The top of the screen must say **test notes**.

### The test (Tim, at the yard)

| # | Step | Pass |
|---|---|---|
| 1 | Turn on aeroplane mode. Open C-vet from the home screen. | The app opens. The horse list is there. |
| 2 | Find a horse with the search box. | The correct horse shows. |
| 3 | Type a note. Save. | The counter says "1 note not sent". |
| 4 | Add a note with a photo. | The photo is ready in a few seconds. |
| 5 | Add a note with a voice recording. Play it back. | The recording plays. |
| 6 | Close the app fully. Open it again. | All notes are there. |
| 7 | Turn off aeroplane mode. Keep the app open. | The counter reaches "Everything is sent" with no tap. |
| 8 | Do steps 3 to 5 in a place with a weak signal (1 bar). | Every note arrives once. |
| 9 | Save 2 notes with no signal. Do not open the app for 8 days. Then open it with a signal. | The 2 notes are there and are sent. |
| 10 | Open **This phone** and read "Opened in". | Under 1000 ms. |
| 11 | Use the app on a real visit for 1 to 2 weeks. | No note is lost. Tim says the app is fast enough and the list is easy to read outdoors. |

Step 9 is the most important. Safari can clear a web app's storage after 7 days with no use. Reports disagree on whether an app on the home screen is safe from this.

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
| All steps pass | Build phases 2 to 6 as a web app. |
| Step 9 fails (storage cleared) | Put the same web code in an app-store wrapper (Capacitor), which gives it storage that is not cleared. Add about 2 weeks and an Apple developer account. |
| Step 5 fails (no voice recording in the installed app) | Typed notes only in the web app, or the wrapper as above. |
| Step 10 fails (slow to open) | Find the slow part with Safari's tools before any other work. The limit stays. |
| Step 7 is a problem (Tim forgets to keep the app open) | Add a reminder on the summary screen. If that is not enough, the wrapper permits a send in the background. |

### When the phone test ends

1. Read the results in **Sync receipts**.
2. Django admin → C-vet → Practices: delete the practice's test notes (section 7b, Test mode).
3. Tim taps **Clear this phone**.
4. Leave `CVET_ENABLED` on if phase 4 work continues on the same server. Set it to `False` to close C-vet.

## 9. Risks

| # | Risk | What we do |
|---|---|---|
| 1 | The iPhone clears a web app's storage, or does not send in the background. | Phase 1 tests this first. The fallback is an app-store wrapper around the same code. |
| 2 | A lost phone holds owner names, unsent notes and the text of sent notes. | Section 5, "Data on the phone". An administrator cuts the phone off in the practice's set-up screen. Open decision 5: is a PIN necessary? |
| 3 | Clinical notes become visible to the yard by mistake. | Phase 2 put them behind the practice (section 7a). A sweep test requests every page as a yard administrator, and a tripwire test stops yard code that reads them. Phase 3 gave files the same rule (section 7b). |
| 4 | RCVS rules on clinical records (revised April 2026) and UK GDPR. | A vet adviser checks the record design before phase 4. The voice service needs a UK GDPR data agreement and must not keep the audio. |
| 5 | Two teams change the same visit code. | Closed by decision 10: C-vet has its own `Visit` and does not change the farrier's code. |
| 6 | The scope grows into a full practice system. | Decision 3. C-vet covers the yard visit only. The practice system stays the full record. |
| 7 | Offline is not a selling point. Other equine vet products say they work offline (VetIT, Lupa). We did not verify their claims. | The selling point is the link to the yard: one horse list, the correct payer, co-owner splits, dates that go back to the yard. |

## 10. Open decisions

| # | Question | Who | Needed before |
|---|---|---|---|
| 1 | Must livery owners consent before the yard sees vet dates for their horse? | Owner of C-gate, with a vet adviser | Phase 6 |
| 2 | Which practice system holds Tim's records? Can it import a file? | Tim | Phase 6 |
| 3 | Phone or iPad on visits? | Tim | Phase 4 |
| 4 | How long must Tim's phone stay signed in? Built as 90 days (decision 12). Change the two settings if another length is better. | Owner of C-gate | Phase 7 |
| 5 | Is a PIN necessary to open C-vet? | Owner of C-gate, Tim | Phase 4 (before the phone holds old notes) |
| 6 | Which speech-to-text service? | Owner of C-gate | Phase 5 |
| 7 | Does the vet see the yard's own vet visit log and medical conditions for a horse, as history? | Owner of C-gate, Tim | Phase 4 |
| 8 | What does the agreement between the yard and the practice say about the server and the backups, which hold the clinical notes? | Owner of C-gate, with a vet adviser | Phase 7 (before real clinical notes) |

The other questions for Tim are in `VET_TRIAL_PLAN.md` 7.7.

## 11. Sources

- [RCVS: Clinical and client records](https://www.rcvs.org.uk/veterinary-professionals/conduct-and-guidance/supporting-guidance/13-clinical-and-client-records). Revised April 2026. The full text was not read for this plan.
- [RCVS: Standards and advice, spring 2026 update](https://www.rcvs.org.uk/about-us/news-and-views/features/standards-and-advice-spring-2026-update)
- [Provet: Best veterinary PMS in the UK (2026)](https://www.provet.com/blog/best-pms-uk-veterinary). A vendor's page. It describes the VetIT offline app.
- [Lupa: Best veterinary practice management systems in 2026](https://lupapets.com/blog/best-veterinary-practice-management-systems-in-2026/). A vendor's page.
- [MagicBell: PWA iOS limitations and Safari support](https://www.magicbell.com/blog/pwa-ios-limitations-safari-support-complete-guide)
- [WebKit bug 237350: home screen web app data deleted after 7 days](https://bugs.webkit.org/show_bug.cgi?id=237350)
- `docs/market/uk-market-sizing.md`, `docs/market/vet-app-market-model.xlsx`, `docs/payments/payments-plan.md`
