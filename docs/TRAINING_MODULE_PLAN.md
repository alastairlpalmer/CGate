# Yardway — training module plan

**Goal:** Anyone on the yard can see, for each horse, what work it did, when, and what is planned next. A trainer puts horses into lots, gives each lot its work for the day, and confirms it. Staff can also record work for one horse or for many horses in one step. Every piece of work is one record in the database, with its date.
**Scope:** A new `training` app. A Training section in the sidebar (the "trainer panel"). A Training card on the horse page. No owner login, no offline mode, no drag tool in the first phases (section 12).
**Status:** Plan. Nothing is built. The owner decided D1 to D4 on 2026-11-19 (section 4.1). D5 to D10 are open (section 4.2).
**Build estimate:** About 18 to 25 working days for phases 1 to 5. Phases 1 and 2 (about 8 to 11 days) give the lot board and the per-horse log.

Written for the owner and for an AI coding agent. Read all of it before you write any code.

**Revision 1.** 2026-11-19. Research first (section 2), then the owner's list (section 3), then the design. The codebase facts in section 6 were checked on the same date.

**Revision 2.** 2026-11-19. The owner decided D1 to D4 (section 4.1). A critical review of the logic found errors and gaps. All are corrected in place. Section 16 lists each finding and its fix. A separate review against the codebase is in progress; its findings go in section 16.3.

---

## 0. How to use this document

**Work one pull request at a time.** Each phase in section 9 is one pull request.

**Stop at the end of each pull request.** Report what you built. Wait for approval. Do not start the next phase.

**Never build a dependency on a later phase.**

If this document conflicts with the codebase, follow the codebase and report the conflict.

**Codebase rules that apply to every phase** (from `BARNS_AND_STABLES_PLAN.md` section 0, still true):

- All code lives under `horse_management/`. Run every command from that directory.
- Before you push, run the same checks CI runs:

  ```bash
  ruff check .
  python manage.py makemigrations --check --dry-run
  python manage.py check
  DJANGO_SETTINGS_MODULE=horse_management.test_settings python manage.py test --parallel auto
  node --test 'static/js/tests/*.test.js'
  ```

- Every page is an HTMX swap of `#main-content` (`hx-boost` on `<body>`). A script in swapped content runs again. Every script you add must be safe to run two times.
- Every `hx-get` or `hx-post` that swaps a partial must set `hx-select="unset"` and `hx-push-url="false"`. `core/tests/test_htmx_lint.py` fails the build if it does not.
- Every new form opens in the shared pop-up sheet (`PopupFormMixin`, `is_popup_request`, `popup_saved_response` in `core/views/_popup.py`). Do not add a new modal.
- Use the brand tokens in `tailwind.config.js`. The default Tailwind colours are off. A class built at runtime must be listed in `static/css/input.css`.
- The template vocabulary lint (`core/tests/test_vocabulary_and_nav.py`) must pass.
- Money shows only to roles that can see charges.
- Do not edit an older migration.

---

## 1. Words used in this plan

Use these words in the code, the screens and the help guide. Use one word for one thing.

| Word | Meaning |
|---|---|
| **Work type** | A kind of work that the yard defines. For example "Canter", "Horse walker", "Rest day". It has a **kind** (ridden, not ridden, rest) and an **effort** (easy, steady, fast). |
| **Activity** | One piece of work by one horse on one date. The only training record. The board and the direct add both make activities. |
| **Lot** | A standing group of horses that usually work together. For example "1st lot", "Young horses". It has a name, an order and a usual time. Any number of horses. |
| **Lot day** | One lot on one date: its work type, where, the time, notes, and whether it is done. |
| **Board** | The page that shows all lot days for one date. The "lot board". |
| **Planned / Done / Did not work** | The three states of an activity. Only **Done** counts in the horse's history and workload. |
| **Training status** | Where a horse is in its training: In work, Light work, Pre-training, Resting, Box rest, Not in training. It decides who goes on the board. |
| **Venue** | Where the work happens, with its surface. For example "Woodchip gallop", "Arena", "Roads". Phase 3. |
| **Rider** | The person who rides or handles the horse. A name on a list, with an optional link to a login. Phase 3. |

---

## 2. Research: what other products and yards do

Done on 2026-11-19, **before** reading the owner's list (section 3). Web search only. The session network blocked some vendor help pages, so some findings come from search summaries of those pages. Section 15 lists the sources.

### 2.1 What the products do

| Product | Market | What it does that is relevant |
|---|---|---|
| Racehorse Trainer Manager | UK racing yards | A **work board**: assign riders to horses and give each a task. Shows on any internet TV in the yard. Replaces the whiteboard. A daily or periodic work log for each horse. |
| Yardman (Stable IT) | UK racing, studs, livery | **Lots and riders on the riding-out board.** Work history and training planning. Phone, tablet and PC. A named competitor in `docs/market/uk-market-sizing.md`. |
| Prism | Racing, international | **Work schedule.** Drag horses to change the order and to move them into and out of groups. Pick many horses and dates: the same work goes to each. **A default rider and default gear on each horse**; a change for one day does not change the default. **Copy from previous days.** Work shows in the horse's work history, the rider's task list, a calendar and a lifetime view. Filter by location or barn, print, and add a note for the day. |
| Ardex | Racing, Australia | **Trackwork allocation grid**: choose a venue and a date; the horses are down the side. Two kinds of work: **ridden work** (has a rider) and **exercise** (no rider: water walker, treadmill, swimming). Also a **training status** on each horse with a **daily charge rate**, which becomes owner invoices each month. |
| The Rundown | US racing | Assign and track barn tasks, schedule training, messages between owner, trainer and staff. |
| TLore, miStable | Racing, owner side | Owners see **workouts, notes, photos and videos** from the trainer. Owners choose which alerts they get. |
| Equus Note, EveryHorse, FarmKeep, Happy Horse | One rider or a small yard | A training diary: session type, duration, footing, notes. |
| HorseCare, My Cheval, StableForm, Equilab | One rider, GPS | GPS distance and speed, a walk/trot/canter split. Users report that automatic gait detection is not accurate. |
| Horsepower, Shlott, Equihub, LiveryLive | Livery and riding schools | Shared calendar, drag-and-drop turnout, yard jobs, staff task lists. |

### 2.2 How a training yard works

- A racing trainer runs **3 or 4 lots a day**, sometimes more. A lot is a string of horses that go out together. One large yard runs 3 lots of about 40 horses; the first lot goes at 8 am. Each horse works for about 1 to 1½ hours.
- Most trainers **gallop twice a week on set days**. Many rest on Sunday. The trainer changes the programme for each horse, mostly near a race.
- Horses come back from a break in stages: **roads (walk and trot) → slow canters → faster work**.
- Too much work makes a horse more tired, not fitter. A large part of limb injuries comes from the wrong **amount and intensity** of work.
- The Royal Veterinary College runs a 3-year study (Training Injury Prevention Study) to make **workload measures** for horses in training. A model study links distance, and **how often a horse rests and for how long**, to bone injury risk.

### 2.3 Types of work

Two separate things describe a piece of work: **what** the horse did and **where**. Keep them separate.

| What the horse did | Where |
|---|---|
| Walk, trot, roads, hack | Roads, tracks |
| Canter (steady), half speed, three-quarter speed, gallop ("work", "a piece of work") | Gallop: all-weather (synthetic), woodchip, grass, sand; uphill or flat |
| Schooling on the flat, jumping, cross-country, interval training | Arena, field |
| Lunge, long reining, loose school | Arena, round pen |
| Horse walker, treadmill, water walker, swimming | The machine or the pool |
| Turnout, rest day, box rest | Field, stable |
| Young horse steps: groundwork, long reining, backing, first ride | Round pen, arena |

### 2.4 What a good log holds

From training diary guides (Pony Club record book, EquiSearch, Horse Illustrated, Tamarran):

- The date. The type of session. **The duration.**
- **The intensity**: a simple scale, or the heart rate after work. One source: the heart rate must come back to 80 bpm before the next canter.
- **The surface** and the weather.
- **Tack and gear.** No diary guide says this; the racing products do.
- **The rider.**
- **Observations**: attitude, soundness, how the horse took new work. Record good things too, not only problems.
- **Plan against actual.** The Pony Club guide says each entry follows the plan or says why not. Horse & Hound: keep an "ideal work diary" and chart the real workload against it.

### 2.5 Soundness before work

- A pre-ride check: hands down all four legs, compare heat, swelling and pain. If it is not routine, change the plan and call the vet.
- The BHA pre-race check: heart, legs by hand, and a trot-up over about 25 m.
- We found no rule that makes a yard record a trot-up before exercise. It is good practice, and the BHA can ask for yard records at an inspection.

### 2.6 Plans and stages

- Event horses: an 8 to 12 week fitness plan after a break in the field. Intervals in the last 2 weeks, about once a week. Hard work every other day, or 2 to 3 times a week.
- Young horses (pre-training): groundwork → long reining → lunging → backing → first ride → all paces. 30 to 90 days, or longer. There is no fixed timeline, so record the stage each horse has reached.

### 2.7 Money

- Ardex: each horse has a **training status with a daily rate** (for example "in training", "pre-training", "spelling"). At the month end the days become owner invoices.
- In the UK, full ridden livery costs about £1,010 a month and full stabled livery about £695 (LiveryList 2026, in `docs/market/uk-market-sizing.md`). The difference is exercise by yard staff. Owners pay for it, so they want to see that it happened.

### 2.8 Owners

- Owner apps (TLore, miStable) show workouts, notes, photos and videos. Owners choose their alerts.
- Harness Racing New Zealand tells trainers to send a **weekly progress report** on fitness, with photos and videos.

### 2.9 The yard itself

- Yards often have a weak phone signal. Farm apps (Farmbrite, Gros.farm) download the day's tasks and sync later. Few stable apps say that they do this for many staff on many phones.
- A work board must be readable on a **TV or a printed sheet** in the tack room, not only on a phone.

### 2.10 What the research says the module needs

| # | Need | Seen in |
|---|---|---|
| R1 | A daily board of lots: horses in groups, in order, each with a rider and a task | All racing products |
| R2 | Move horses between groups quickly | Prism (by drag) |
| R3 | The same work for many horses in one step | Prism, Ardex |
| R4 | Copy yesterday's board, or use a plan for each day of the week | Prism, "gallop on set days" |
| R5 | A default rider and default gear on each horse | Prism |
| R6 | Ridden work and work that is not ridden, as two kinds | Ardex |
| R7 | What (work type) and where (venue, surface) as two fields | Racing practice |
| R8 | Duration, effort, notes, rider and gear on each record | Diaries |
| R9 | Planned against done: the board is a plan until someone confirms it | Pony Club, Horse & Hound, Prism |
| R10 | A history on each horse, with a calendar view | All |
| R11 | Rest days and days since fast work, as workload signals | RVC, Great British Racing, The Conversation |
| R12 | A "did not work" reason that links to the health records | Pre-ride checks, BHA |
| R13 | Young horse stages | Pre-training sources |
| R14 | A print view and a TV view of the board | Racehorse Trainer Manager, Prism |
| R15 | An owner view of recent work | TLore, miStable, HRNZ |
| R16 | Charges for sessions, and a rate for each training status | Ardex, ridden livery prices |
| R17 | Works with a weak signal | Farm apps |

---

## 3. The owner's list, checked against the research

The owner's list, then what the research adds.

| Owner's point | Fits the research? | What to add or change |
|---|---|---|
| A lot board in the trainer panel | Yes. R1. | A Training section in the sidebar with four tabs: **Board**, **Lots**, **Log**, **Set up**. The board shows one date. Arrows go to the day before and the day after. |
| Splits horses into groups of custom sizes | Yes. | No size limit. Each lot shows its count. A lot has an **order** in the day and a **usual time**. Horses have an order in the lot (the order they go out). |
| The horses in a lot do similar work | Yes, mostly. | Real lots are rarely all the same. One horse only walks; one goes on the walker. So each horse in a lot can have **its own change for the day**. A later change to the lot's work does not overwrite it (principle 5.4). |
| The work of the day is logged on each horse's page | Yes. R10. | **Add a confirm step** (R9). The board is a plan. A trainer taps **Mark lot done**. A horse that did not go out gets **Did not work** and a reason. Without this step, a lame horse that stayed in its stable gets a record that says it cantered. This is the largest gap in the list. |
| Categories of work that each lot can be given | Yes. | A work type has a **kind** (ridden, not ridden, rest) and an **effort** (easy, steady, fast). The effort makes the workload signals possible (R11). **Where** is a separate field (R7, phase 3). |
| Add an activity to one horse | Yes. | A **Training** tile in the horse page's **Record** card. The same pop-up form as the other records. |
| Add an activity to many horses (bulk) | Yes. R3. | Use the bulk bar that already exists on the horse list, the location page and the owner page. Add **Training** to its list of actions. |
| Notes and details | Yes. R8. | Fields: date, work type, duration in minutes, effort (if not the work type's usual effort), notes. Phase 3 adds venue, rider and gear. |
| Both features record the date and use the database | Yes. | **One table** (`Activity`) for both. The board and the direct add make the same record. One history, one set of totals. |

**What the list does not have, and the research says is needed:**

1. **Training status** for each horse (In work, Resting, Box rest and so on). Without it, the board does not know which horses to include, and a horse on box rest goes on the board.
2. **"Did not work" with a reason**, and a link to record a condition or a vet visit (R12).
3. **Copy forward and a week plan** (R4). A trainer does not build the board from zero each morning.
4. **Riders and gear** (R5).
5. **Workload signals**: days since the last work, days since fast work, days worked in the last 7 (R11). Information only, not advice.
6. **Print and TV views** (R14).
7. **Who did it**: each record keeps who made it and who changed it.
8. **Access**: a new `training` feature in Users & Roles.
9. **Money** (R16) and **owner reports** (R15): later phases, with decisions in section 4.

---

## 4. Decisions for the owner

### 4.1 Decided on 2026-11-19

| # | Question | Owner's answer | What it means for the build |
|---|---|---|---|
| D1 | Which yards is this for: racing and pre-training yards, livery yards with ridden exercise, or both? | **Both.** | Neutral default work types (7.1). The word "Lot" for both, with help text. Livery yards need two extra things: the rider can be **the owner** (phase 3), and a horse can have a number of **exercise days a week** that its livery includes, so the yard can show it gave that service (phase 4). |
| D2 | Is a lot a **standing group** (the same horses each day until changed) or **new each day**? | **Standing group, as the default for now.** It can change later. | The board starts from the standing lots. The design must keep "new each day" possible later without a rebuild (principle 5.11). |
| D3 | Does work on the board count **when it is planned**, or only **when someone confirms it**? | **Only when confirmed.** | One tap for each lot: **Mark lot done**. Only Done counts (5.2). |
| D4 | What happens to a lot that nobody confirms? | **It stays Planned until someone confirms it.** | No automatic confirm. After its date, a Planned activity shows as **Not confirmed**. Staff can confirm it later from the board, the horse page or the Log. The dashboard lists lots that are not confirmed (phase 4). |

### 4.2 Still open

Each row has a recommended answer. The plan uses it until the owner says otherwise.

| # | Question | Recommended answer | Why |
|---|---|---|---|
| D5 | Riders: logins, or names on a list? | **Names on a list**, with an optional link to a login. | Many riders do not have a login. |
| D6 | Can a horse be in more than one standing lot? | **No. One standing lot for each horse.** Extra work on the same day (for example the walker in the afternoon) is a separate activity. | Simple rules, a simple board. The schema keeps this cheap to change (7.3). |
| D7 | Who can use the board? | A new feature key `training` (view and full). The owner sets it for each role. Option: seed a **Trainer** role (training full, horses view, health view). | Fits the Role Suite. |
| D8 | Charge owners for some sessions (for example "Schooling £25")? | **Yes, as phase 5, off by default.** A price on the work type, and a setting on each horse: "Exercise is included in livery" or "Charge for each session". | Ridden livery yards want it (section 2.7). It must never charge a horse whose livery includes exercise. |
| D9 | Is the phone signal at the yard good enough for normal pages? | **Test it at the yard before phase 2.** If it is not, add an offline phase (section 12). | The C-vet offline work shows the cost: a separate app, an API and sync rules. |
| D10 | Drag horses between lots? | **Not in phases 1 to 5.** A **Move to** button on each horse. A drag tool can come later, on wide screens only. | No drag library is in the codebase. `BARNS_AND_STABLES_PLAN.md` chose "no drag tool". Taps work on phones and with a keyboard. |

---

## 5. Principles

These rules do not change in any phase.

### 5.1 One record for all training work

`Activity` is the only training record. The board, the horse page and the bulk bar all make activities. Do not add a second table for the same thing.

### 5.2 Only Done counts

The horse's history, totals and workload signals count only activities with the status **Done**. Planned activities show as planned (grey, a dashed border). **Did not work** shows with its reason.

A Planned activity whose date is before today shows as **Not confirmed** (D4). It is still Planned in the database. Nothing changes it automatically. Staff confirm it, or mark it Did not work, from the board, the horse page or the Log.

### 5.3 A page that shows data never writes data

Opening the board for a date with no lot days shows a **Start the board** button. A POST makes the lot days and the planned activities. A GET never makes them. Two people who tap **Start** at the same time make one board (the unique key on lot and date stops the second).

### 5.4 A lot's change never overwrites a horse's own change

Each activity on the board has `follows_lot = True` until someone changes that horse's work for the day. A change to the lot day's work type changes only the activities that still follow the lot.

### 5.5 History is never lost

- A work type, a lot, a venue or a rider is **archived**, not deleted, once an activity uses it (`on_delete=PROTECT`, `is_active=False`).
- Changes to standing lots do not change past activities.
- A horse that leaves the yard leaves its standing lot. Its **planned** activities after the departure date are deleted. Its **done** activities stay.

### 5.6 The date is the day of the work

`Activity.date` is the date of the work, not the date someone typed it. Use `timezone.localdate()` for "today". A record for yesterday is normal.

### 5.7 Access follows the feature

- `training` view: see the Training pages and the horse page's Training card.
- `training` full: add, change and delete activities; run the board; edit lots, work types, venues and riders.
- Prices and charges (phase 5) also need `charges` view or full, as everywhere else.

### 5.8 Phone first, readable from across the room

The board works with one hand: tap targets of 44 px or more, 8 or more horses on the first phone screen, two lines for each horse at most. The TV view (phase 4) uses large text.

### 5.9 Signals are information, not advice

The workload signals state facts ("No work for 4 days", "Fast work 2 days running"). They do not tell anyone what to do, and they do not claim a risk.

### 5.10 Behind a flag until it is in use

`TRAINING_ENABLED` (default off), as `BARNS_ENABLED` and `CVET_ENABLED` are. With the flag off, no Training link shows and every Training URL returns 404.

### 5.11 Standing lots are the default, not a lock (D2)

The owner may change D2 later to "new each day". So:

- `LotMember` is the only place that says "standing". No other model, rule or screen needs a horse to be a standing member.
- A lot day can hold horses that are not standing members of its lot (**Add horse**, **Move to**).
- `start_board` makes a lot day for **every** active lot, also a lot with no standing members. Its source is a parameter: `lots`, `copy` or `empty`. Phase 2 builds all three. "Empty" is the "new each day" way of working.
- A later switch to "new each day" is then a yard setting that changes the default source and hides the standing member editor. It needs no schema change.

### 5.12 How the status of an activity changes

| From | To | How | Allowed when |
|---|---|---|---|
| (new) | Planned | Board start, Add horse, direct add with a date after today, or by choice | Any date |
| (new) | Done | Direct add or bulk add with a date of today or earlier | Date is today or earlier |
| Planned | Done | **Mark lot done**, **Mark done** on one horse, or **Mark done** on many rows in the Log | Date is today or earlier |
| Planned | Did not work | **Did not work** on one horse, or **Cancel lot** | Any date |
| Done | Planned | **Reopen lot**, or **Change** on one activity | Any date. No time limit: a record must stay correctable. |
| Did not work | Planned | **Undo** on one horse, or **Reopen lot** | Any date |

- **Done is never allowed for a date after today.** The form and the service both refuse it.
- **Mark lot done** and **Reopen lot** change only the activities in that lot day. **Reopen lot** sets Done back to Planned and leaves Did not work as it is.
- Every change sets `updated_by`. `simple_history` keeps the old values.
- Every bulk change is one `UPDATE ... WHERE status = <from>`. A double tap or two people at once give the same result as one tap.

### 5.13 Colour is never the only signal

Effort, status and work type have a colour **and** text or an icon with a text alternative. "Planned", "Not confirmed", "Did not work" and "Done" are words on the screen, not only a border or a colour.

---

## 6. What exists, and what to reuse

Checked against the codebase on 2026-11-19. Paths are under `horse_management/`.

| Need | What exists | Use it like this |
|---|---|---|
| Horses | `core.models.Horse` (`core/models.py:716`). `is_active` is the only status. Active horses: `Horse.objects.filter(is_active=True)`. | Training status goes in a new one-to-one model (7.4), not on `Horse`. |
| Departures | `PlacementService.depart_horse` and `bulk_depart` (`core/services.py:327`, `:463`). | Call `training.services.horse_left(horse, day)` from both, after the placement closes (5.5). |
| Horse page history | `build_horse_timeline` (`core/views/horses.py:1149`), used by the horse page and the list preview. Template: `templates/horses/horse_detail.html`, timeline card at lines 554 to 786, filter pills at 582 to 599. | Add an `activities` argument and the type `training`. Add a **Training** pill. Each type is capped at 10 rows in the view, so training cannot fill the timeline. |
| Record tiles on the horse page | `templates/horses/_quick_actions.html`, gated by `feature_access`. | Add a **Training** tile (training full). |
| Pop-up forms | `PopupFormMixin` (`core/views/_popup.py`), `?horse=<id>` hides the horse field. "Save and add another". | Use it for every training form. |
| Bulk actions | The `bulkActions` Alpine component (`templates/base.html:866-951`). The bar `templates/health/partials/bulk_action_bar.html`. Server: `health/views.py` `BULK_FORM_MAP` (282), `_bulk_action_allowed` (499), `bulk_health_form` (526), `bulk_health_apply` (579). | Add the action type `training`. Gate it on `training` full in `_bulk_action_allowed`. The two health views send `training` to `training/bulk.py`. Keep the training form and save code in the training app. |
| A list for one day, with day arrows | The farrier's yard list: `health/farrier_yard_views.py:144`, `health/farrier_yard.py`. | Copy the day navigation pattern for the board. |
| Roles | `core/features.py` (`FEATURES`, `DEFAULT_LEVELS` all hidden), `core/permissions.py` (`FeatureAccessMixin`, `feature_required`). Test helper `make_user_with_access` in `core/roles_testutils.py`. | Add the key `training` (supports view). A new key is hidden for every role except Administrator, with no data migration. |
| Feature flags | `settings.py` `env.bool(...)` (for example `BARNS_ENABLED`). Context processors in `core/context_processors.py`. Documented in `.env.example`. | Add `TRAINING_ENABLED` and a `training_enabled` context value. |
| Sidebar | `templates/includes/_sidebar.html`. The Manage heading condition is at line 76. Phone: the More sheet in `templates/base.html` (about lines 296 to 324). Icons: `templates/includes/_icons.html`. Tests: `core/tests/test_sidebar.py`. | Add **Training** under Manage, after Health. Add `feature_access.training.view` to the heading condition. Add a new icon. |
| Add menu | `templates/includes/_add_menu_items.html` | Add **Training activity**. |
| Dashboard timeline | `core/dashboard/activity.py` (`Event`, `GROUPABLE`, `KIND_GROUP`, `recent`). | Phase 4: one row for each lot day that is done ("1st lot · Canter · 12 horses"). |
| Needs-action items | `core/dashboard/attention.py` `COLLECTORS` (914). On/off switches in `core/reminders.py` (`REMINDERS`). | Phase 4: "Lots not confirmed", "No work for N days". |
| Charges | `billing.models.ExtraCharge` (`ChargeType` at `billing/models.py:73`). `sync_record_charge` (`health/views.py:345`) is the one rule for record to charge. `ServiceItem` is the price list. | Phase 5: a `training` charge type, and a training version of the same rule. |
| Help centre | `core/help.py` (`GUIDES`), `templates/help/articles/<slug>.html`. `core/tests/test_help.py` checks the shape. | Phase 1: a `training` guide in the group "The yard". |
| Record history | `simple_history` on some models (see `core/audit.py`). | Add `HistoricalRecords()` to `Activity` and `HorseTraining`. |
| Drag and drop | None. No library. | Not used (D10). |
| Multi-tenancy | None. The database is one yard. | No yard key. Same as every other model. |
| Training, lots, riders | **Nothing exists.** No model, page or term. | All new. |

---

## 7. Data model

A new Django app: `training`. All models are in `training/models.py`. The phase that adds each model is in brackets.

### 7.1 `WorkType` [phase 1]

```python
class WorkType(models.Model):
    class Kind(models.TextChoices):
        RIDDEN = 'ridden', 'Ridden'
        NOT_RIDDEN = 'not_ridden', 'Not ridden'   # walker, treadmill, swim, lunge
        REST = 'rest', 'Rest'                     # rest day, turnout, box rest

    class Effort(models.IntegerChoices):
        NONE = 0, 'None'      # rest only
        EASY = 1, 'Easy'
        STEADY = 2, 'Steady'
        FAST = 3, 'Fast'

    name = models.CharField(max_length=60, unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    effort = models.PositiveSmallIntegerField(choices=Effort.choices)
    default_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    colour = models.CharField(max_length=20)     # a brand token name from a fixed list
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    # phase 5: price = DecimalField(null=True, blank=True)

    # check: kind == rest  <=>  effort == 0
```

Seed these in a data migration, only when the table is empty. The yard can rename, reorder and archive them.

| Kind | Work types (effort) |
|---|---|
| Ridden | Walk (easy), Walk and trot (easy), Hack (easy), Canter (steady), Schooling (steady), Jumping (steady), Fast work (fast), Interval training (fast) |
| Not ridden | Horse walker (easy), Walk in hand (easy), Lunge (steady), Long reining (steady), Loose school (steady), Treadmill (steady), Water walker (steady), Swim (steady) |
| Rest | Rest day, Turnout, Box rest |

### 7.2 `Activity` [phase 1]

```python
class Activity(models.Model):
    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        DONE = 'done', 'Done'
        NOT_DONE = 'not_done', 'Did not work'

    class Reason(models.TextChoices):           # only with NOT_DONE
        UNSOUND = 'unsound', 'Lame or sore'
        VET = 'vet', 'Vet or treatment'
        FARRIER = 'farrier', 'Shoe off or farrier'
        WEATHER = 'weather', 'Weather or ground'
        NO_RIDER = 'no_rider', 'No rider'
        OTHER = 'other', 'Other'

    class Source(models.TextChoices):
        BOARD = 'board', 'Lot board'
        HORSE = 'horse', 'Horse page'
        BULK = 'bulk', 'Bulk add'

    horse = models.ForeignKey('core.Horse', on_delete=models.CASCADE, related_name='activities')
    date = models.DateField(db_index=True)
    work_type = models.ForeignKey(WorkType, on_delete=models.PROTECT)
    status = models.CharField(max_length=10, choices=Status.choices)
    reason = models.CharField(max_length=10, choices=Reason.choices, blank=True)
    minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    effort = models.PositiveSmallIntegerField(null=True, blank=True)   # null = the work type's effort
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=6, choices=Source.choices)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    # phase 2:
    lot_day = models.ForeignKey('LotDay', null=True, blank=True, on_delete=models.PROTECT,
                                related_name='activities')
    follows_lot = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)              # order in the lot day
    # phase 3: venue (FK, null), rider (FK, null), rider_is_owner (bool), gear (CharField, blank)
    # phase 5: extra_charge (OneToOne to billing.ExtraCharge, null)

    # check: reason != ''  <=>  status == not_done
    # unique (lot_day, horse) where lot_day is not null   -> one row for each horse in a lot day
    # index (horse, -date), (date, status)
```

Rules:

- A new activity dated today or earlier starts as **Done**. A new activity dated after today starts as **Planned**. The form can change it, inside the rules of 5.12.
- Done with a date after today is refused by the form and by the service. A database check cannot hold it, because "today" changes.
- `effective_effort` = `effort` if set, else `work_type.effort`. Every total uses it.
- A horse can have more than one activity on one date (for example a canter in the morning and the walker in the afternoon).

### 7.3 `Lot`, `LotMember`, `LotDay` [phase 2]

```python
class Lot(models.Model):
    name = models.CharField(max_length=60, unique=True)
    position = models.PositiveSmallIntegerField(default=0)     # order in the day
    usual_time = models.TimeField(null=True, blank=True)
    usual_work_type = models.ForeignKey(WorkType, null=True, blank=True, on_delete=models.PROTECT)
    colour = models.CharField(max_length=20)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)


class LotMember(models.Model):
    """The standing lot of a horse. No history: activities hold the history (5.5)."""
    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name='members')
    horse = models.ForeignKey('core.Horse', on_delete=models.CASCADE, related_name='lot_members')
    position = models.PositiveSmallIntegerField(default=0)     # order in the lot

    # UniqueConstraint(fields=['horse'], name='lotmember_one_lot_per_horse')   -> D6


class LotDay(models.Model):
    class Status(models.TextChoices):
        PLANNED = 'planned', 'Planned'
        DONE = 'done', 'Done'
        CANCELLED = 'cancelled', 'Cancelled'

    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name='days')
    date = models.DateField()
    work_type = models.ForeignKey(WorkType, null=True, blank=True, on_delete=models.PROTECT)
    start_time = models.TimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default='planned')
    confirmed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_by, created_at, updated_at
    # phase 3: venue (FK, null)

    # unique (lot, date)
```

The unique constraint on `LotMember.horse` keeps a horse in one standing lot at most (D6). It is a foreign key with a constraint, not a one-to-one field, so a later change to D6 only drops the constraint. Code reads `horse.lot_members.first()`, never a one-to-one accessor.

### 7.4 `HorseTraining` [phase 2]

```python
class HorseTraining(models.Model):
    """A horse's training settings. Made the first time the horse is used in Training."""
    class Status(models.TextChoices):
        IN_WORK = 'in_work', 'In work'
        LIGHT = 'light', 'Light work'
        PRE_TRAINING = 'pre_training', 'Pre-training'
        RESTING = 'resting', 'Resting'
        BOX_REST = 'box_rest', 'Box rest'
        NOT_IN_TRAINING = 'none', 'Not in training'

    ON_BOARD = (IN_WORK, LIGHT, PRE_TRAINING)

    horse = models.OneToOneField('core.Horse', on_delete=models.CASCADE, related_name='training')
    status = models.CharField(max_length=14, choices=Status.choices, default='none')
    status_since = models.DateField()
    notes = models.TextField(blank=True)
    history = HistoricalRecords()
    # phase 3: default_rider (FK, null), default_gear (CharField, blank)
    # phase 4: days_per_week (PositiveSmallIntegerField, null) -> exercise days its livery includes (D1)
    # phase 5: billing = included | per_session
```

- A horse with no row is **Not in training**. Livery horses that are not exercised never see the module.
- A horse added to a lot moves to **In work** if it was Not in training.
- Only horses with a status in `ON_BOARD` get planned activities when the board starts. Others show in a footer: "Not on the board: 2 resting, 1 box rest".
- A status change to Resting, Box rest or Not in training asks one question in the same pop-up: "Take Ballybrook off the boards from Thu 20 Nov?" The default is yes. Yes deletes the horse's **Planned** activities from that date on. They are plans, not history (5.5). Done and Did not work stay.

### 7.5 `Venue`, `Rider`, `LotWeekPlan` [phase 3]

```python
class Venue(models.Model):
    class Surface(models.TextChoices):
        ALL_WEATHER = 'all_weather', 'All-weather'
        WOODCHIP = 'woodchip', 'Woodchip'
        GRASS = 'grass', 'Grass'
        SAND = 'sand', 'Sand'
        ARENA = 'arena', 'Arena'
        ROAD = 'road', 'Road or track'
        INDOOR = 'indoor', 'Indoor'
        WATER = 'water', 'Pool or water'
        OTHER = 'other', 'Other'
    name, surface, position, is_active


class Rider(models.Model):
    name = models.CharField(max_length=80)
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL)
    is_active, position


class LotWeekPlan(models.Model):
    """The usual work of a lot on one day of the week. For example 1st lot, Tuesday: Fast work."""
    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name='week_plan')
    weekday = models.PositiveSmallIntegerField()          # 0 = Monday; check 0 <= weekday <= 6
    work_type = models.ForeignKey(WorkType, null=True, blank=True, on_delete=models.PROTECT)
    venue = models.ForeignKey(Venue, null=True, blank=True, on_delete=models.PROTECT)
    no_lot = models.BooleanField(default=False)           # the lot does not go out that day
    # unique (lot, weekday); check no_lot or work_type is not null
```

- **The owner as rider (D1).** On a livery yard the owner often rides. `Activity.rider_is_owner` (boolean) records it, so the yard does not have to put owners on the rider list. The rider picker shows "The owner" first. `rider` and `rider_is_owner` cannot both be set.
- **A day with no lot.** `no_lot=True` for a lot and weekday: `start_board` makes no lot day for that lot on that weekday (for example, every lot on Sunday).

### 7.6 How the parts connect

```
 Set up                  Each day                         Results
 ------                  --------                         -------
 Work types ─┐
 Venues ─────┤          ┌──────────────┐  Mark lot done   ┌──────────────────────────┐
 Riders ─────┤          │ Board (date) │ ───────────────▶ │ Activities (status Done) │
 Lots ───────┼─ Start ─▶│  Lot days    │                  └────────────┬─────────────┘
 Week plan ──┘          │  Planned     │                               │
                        │  activities  │                               ├─▶ Horse page: Training card and timeline
                        └──────────────┘                               ├─▶ Training → Log, CSV
 Horse page "Record → Training" ─────────────────────────────┐         ├─▶ Workload signals (phase 4)
 Bulk bar "Training" (many horses) ──────────────────────────┴─▶ Activities  ├─▶ Dashboard timeline (phase 4)
                                                                       └─▶ Charges (phase 5)
```

---

## 8. Screens

### 8.1 The Training section (the trainer panel)

Sidebar: **Manage → Training**. Four tabs at the top of the section.

| Tab | What it shows | Access |
|---|---|---|
| **Board** (phase 2) | One date. All lot days for that date, in lot order. Day arrows and a **Today** button. | view; full to change |
| **Lots** (phase 2) | The standing lots: name, usual time, usual work, horses in order, count. Add a lot, add horses, remove horses, change the order. | view; full to change |
| **Log** (phase 1) | Every activity. Filters: dates, horse, lot, work type, status, and **Not confirmed**. Newest first. Select rows, then **Mark done** or **Did not work** for all of them (full). CSV download in phase 4. | view; full to change |
| **Set up** (phase 1) | Work types. Phase 3 adds venues, riders and week plans. | full |

Before phase 2 is built, the section has only **Log** and **Set up**.

### 8.2 The board on a phone (phase 2)

```
┌────────────────────────────────────┐
│ Training     Board  Lots  Log  Set up │
│ ◀   Thu 20 Nov   ▶         Today   │
│ 3 lots · 26 horses · 1 lot done    │
│ 1 lot not confirmed on Wed 19 Nov ▸│
├────────────────────────────────────┤
│ 1st lot · 7:30 · 10 horses         │
│ Canter                    Change ▸ │
│  1 Ballybrook                      │
│  2 Dancing Moon   Walk only ✎      │
│  3 Red Kite       Did not work:    │
│                   Lame or sore     │
│  …                                 │
│ [ Mark lot done ]   Add horse ▸    │
├────────────────────────────────────┤
│ 2nd lot · 9:00 · 8 horses   ✓ Done │
│ Horse walker                       │
│  …                                 │
├────────────────────────────────────┤
│ Not on the board: 2 resting,       │
│ 1 box rest                         │
└────────────────────────────────────┘
```

- **Start the board** shows when the date has no lot days. Phase 2 choices: **From the lots** (the default: standing members, each lot's usual work), **Copy the last board** (the last date before this one that has a board, for example Sat 15 Nov after a Sunday with no board), or **Empty** (every lot with no horses; the trainer adds horses for the day). Phase 3 adds **From the week plan** and makes it the default when a week plan exists.
- A date after today can have a board. A trainer can plan tomorrow's lots in the evening. **Mark lot done** shows only on dates of today or earlier.
- The line under the date names the last date with lot days that are not confirmed, with a link to it (D4).
- **Change** on a lot opens the pop-up: work type, time, notes. Phase 3: venue.
- **A tap on a horse** opens the pop-up for that horse today. It has: change work, effort, minutes, notes; **Move to** another lot (this date only); **Did not work** with a reason; remove from this date.
- A horse with its own change shows the change and a pencil mark, so the trainer sees the exceptions.
- **Mark lot done** sets every Planned activity in the lot day to Done. It does not change Did not work. The lot day shows "Done by Sam at 8:52". **Reopen lot** puts Done back to Planned, with no time limit (5.12).
- **Did not work: Lame or sore** offers a link to **Record a condition** or **Record a vet visit** for that horse (health full only).
- **Add horse** adds a horse to this lot day only. The standing lot does not change.
- **A standing lot that changed after the board started.** The board does not change by itself. A lot day whose standing lot now has a horse that is not on the board shows "1 horse in 1st lot is not on this board: Ballybrook. **Add**". A horse removed from the standing lot stays on boards that already started; the trainer removes it by hand. This applies to today and later dates only. Past boards never change.
- **Cancel lot** (in the lot's **Change** pop-up) asks for one reason, for example Weather or ground. Every Planned horse in the lot gets Did not work with that reason. A horse already Done stays Done. The lot day shows "Cancelled". **Reopen lot** undoes it.

On a wide screen, the lots show side by side as columns.

### 8.3 The Lots tab (phase 2)

A list of lots in order. Each lot shows its horses in order, its count, its usual time and work. Actions: **Add lot**, **Add horses** (a multi-select picker of active horses that are not in a lot), **Remove**, **Up** and **Down** to change the order, **Move to** another lot, **Archive lot**. A horse already in a lot shows its lot name in the picker.

Help text at the top: "A lot is a group of horses that usually work together. The board for each day starts from these lots. A change here does not change a board that already started." 

### 8.4 The horse page (phase 1, more in phases 2 to 4)

- A **Training** card, below the health cards:
  - Phase 1: **Coming up** (Planned activities dated after today, at most 5), then the last 14 days, newest first. Each row: date, work type, minutes, status in words, note. A Planned row dated today or earlier has **Mark done** and **Did not work** (full). **Record training** and **See all** (the Log tab filtered to this horse).
  - Phase 2: the horse's training status and lot, with **Change**.
  - Phase 4: a 28-day strip (one square for each day, coloured by effort, empty for no record) and the workload signals.
- The unified timeline: the type `training` and a **Training** pill. Only activities dated today or earlier go on the timeline. Plans for later dates show only in **Coming up**.
- The **Record** card: a **Training** tile.

### 8.5 Bulk add (phase 1)

On the horse list, the location page and the owner page: select horses, choose **Training** in the bulk bar, then fill in one form. It makes one activity for each selected active horse, in one transaction. The message says "Recorded Canter for 12 horses". Horses that are not active are skipped and named in the message.

### 8.6 Print and TV (phase 4)

- **Print**: the board for one date on one A4 page if it fits. Lot name, time, work, horses in order, rider, a blank column for ticks.
- **TV**: the board in large text, no buttons, refreshes each 60 seconds. It needs a sign-in, like every page. Use a separate login for the TV, with a role that has `training` view and nothing else, so a TV left on in the tack room cannot open other records. Check that the session stays signed in for a working day (`SESSION_COOKIE_AGE`) before the first use.

---

## 9. Phases

Each phase is one pull request. Estimates are working days for one developer with an AI coding agent, from the sizes of the earlier plans. They are estimates.

| Phase | Result | Estimate |
|---|---|---|
| 1 | Record training for one horse or many. Work types. The horse page shows training. | 3 to 4 days |
| 2 | Lots and the lot board. Training status. Confirm and Did not work. | 5 to 7 days |
| 3 | Week plan, venues, riders, default rider and gear. | 3 to 4 days |
| 4 | Workload signals, dashboard, attention items, print, TV, CSV. | 4 to 5 days |
| 5 | Charges for sessions. | 3 to 4 days |

**Why phase 1 is the direct add and not the board:** both features write the same `Activity` record. Phase 1 builds that record, its forms, its access and its display on the horse page. Phase 2 then adds the board on a base that is already tested. The board is the larger part, so this order keeps each pull request smaller.

### 9.1 Phase 1 — Record training

**Result:** Staff can record training for one horse or many. The horse page shows it.

Build:

1. The `training` app, registered in `INSTALLED_APPS`. `TRAINING_ENABLED` in `settings.py` (default off) and `.env.example`. A context value `training_enabled`.
2. The feature key `training` in `core/features.py`: group "Manage", `supports_view: True`, description "Lots, the lot board and the training log for each horse."
3. `WorkType` and `Activity` (7.1, 7.2), without the phase 2 fields. The seed data migration for work types.
4. Forms: `ActivityForm` (horse, date, work type, minutes, effort, status, reason, notes). The reason shows only with Did not work. The minutes fill from `default_minutes` when the work type changes. `WorkTypeForm`.
5. Views, all through `PopupFormMixin`: add, change, delete an activity; add and change a work type; archive a work type. URLs under `/training/`.
6. The **Log** tab with its filters and paging, the **Not confirmed** filter, and **Mark done** and **Did not work** for selected rows. The **Set up** tab with the work type list.
6a. `training/services.py` starts here with `mark_done(activities, *, user)` and `mark_not_done(activities, reason, *, user)`. They follow 5.12. Phase 2 builds on them.
7. Bulk: the action type `training` in the bulk bar, `_bulk_action_allowed`, and a dispatch in `bulk_health_form` and `bulk_health_apply` to `training/bulk.py`.
8. The horse page: the Training card (**Coming up** and 14 days, 8.4), the **Training** tile, `build_horse_timeline(activities=...)` and its pill, with activities dated today or earlier only. The list preview pane gets the same timeline rows.
9. The sidebar item, the phone More sheet item, the Add menu item, and a new icon.
10. The help guide `training` (group "The yard", features `('training',)`), with sections: Record training for one horse, Record training for many horses, Work types.

Definition of done:

- [ ] With the flag off, `/training/` returns 404 and no Training link shows.
- [ ] A role with `training` hidden sees no Training card, tile or bulk action. A role with view sees the card and the Log, and no add buttons. POSTs from a view-only role get 403.
- [ ] An activity dated today is Done by default. One dated tomorrow is Planned by default.
- [ ] Done with tomorrow's date is refused by the form and by `mark_done`.
- [ ] A Planned activity dated yesterday shows as "Not confirmed" on the horse card and in the Log. **Mark done** on it makes it Done. Two **Mark done** requests at once make one change and no error.
- [ ] A Planned activity dated tomorrow is in **Coming up** and not on the timeline.
- [ ] Did not work without a reason does not save. A reason with Done does not save.
- [ ] A bulk add for 5 horses, one of them inactive, makes 4 activities in one transaction and names the skipped horse.
- [ ] An archived work type does not show in the form. Its old activities still show its name.
- [ ] A work type that an activity uses cannot be deleted.
- [ ] The horse timeline shows training under All and under the Training pill, at most 10 rows.
- [ ] `core/tests/test_help.py`, `test_htmx_lint.py`, `test_sidebar.py` and `test_vocabulary_and_nav.py` pass.

### 9.2 Phase 2 — Lots and the board

**Result:** A trainer puts horses into lots, starts the board each day, changes the work, and confirms each lot. The horse page shows what each horse did.

Build:

1. `Lot`, `LotMember`, `LotDay`, `HorseTraining` (7.3, 7.4). The phase 2 fields on `Activity`.
2. `training/services.py`, with all board rules in pure functions:
   - `start_board(day, *, user, source)`: source is `lots`, `copy` or `empty` (5.11). Makes one `LotDay` for each active lot, also a lot with no standing members. Refuses a date that already has lot days. `lots`: one Planned activity for each standing member who is active and whose status is in `ON_BOARD`, in member order, with the lot's usual work. `copy`: copies the last board before `day`: lot days, work types, times, notes, the per-horse changes and the lot moves. It does not copy Did not work, horses that are no longer active, or horses whose status is not in `ON_BOARD`. `empty`: lot days with no activities.
   - `set_lot_work(lot_day, work_type, *, user)`: changes the lot day and only the activities with `follows_lot=True` (5.4).
   - `change_horse_work(activity, ..., *, user)`: sets `follows_lot=False`.
   - `move_horse(activity, to_lot_day, *, user)`: that date only. The activity takes the new lot day's work and `follows_lot=True`.
   - `cancel_lot(lot_day, reason, *, user)`: every Planned activity gets Did not work with the reason. Done stays Done.
   - `mark_lot_done(lot_day, *, user)`: refuses a date after today. Uses `mark_done` from phase 1.
   - `reopen_lot(lot_day, *, user)`: Done and the Did not work that `cancel_lot` made go back to Planned. Did not work set on one horse stays.
   - `set_status(horse, status, day, *, remove_plans, user)`: the training status change and the question in 7.4.
   - `missing_members(lot_day)`: standing members that are not on the lot day (the hint in 8.2).
   - `horse_left(horse, day)`: deletes the `LotMember` row and the Planned activities after `day`.
3. Calls to `horse_left` from `PlacementService.depart_horse` and `bulk_depart`, after a departure dated today or earlier.
4. The **Board** tab (8.2) and the **Lots** tab (8.3).
5. Training status on the horse page Training card, with **Change** (a pop-up).
6. Update the help guide: The lot board, Lots, Training status.

Definition of done:

- [ ] Opening the board for a date with no lot days makes no rows. Two POSTs to start the same date make one board.
- [ ] A horse on Box rest is not on the started board and shows in the footer count.
- [ ] Change the lot work from Canter to Walk. A horse whose own work was "Walker" stays on Walker. The others change to Walk.
- [ ] Mark lot done: Planned becomes Done; Did not work stays. Reopen lot puts them back to Planned, also a week later.
- [ ] Mark lot done is not offered, and is refused, for a date after today. A board for tomorrow can be started and changed.
- [ ] Start **Empty**: every active lot has a lot day with no horses. **Add horse** works on it.
- [ ] Copy on a Monday after a Sunday with no board copies Saturday.
- [ ] A horse added to the standing lot after today's board started shows in the "not on this board" hint, and **Add** puts it on. Yesterday's board does not change.
- [ ] Set a horse to Box rest from today with "Take off the boards": its Planned activities from today are gone; yesterday's Done stays.
- [ ] Cancel lot with the reason Weather: every Planned horse gets Did not work, Weather. A horse that was already Done stays Done.
- [ ] Copy the last board: the horse moved to 2nd lot on that board is in 2nd lot. A horse that left the yard is not copied.
- [ ] A horse departs. It leaves its lot. Its Planned activities after the departure date are gone. Its Done activities stay.
- [ ] A lot with lot days cannot be deleted; it can be archived. An archived lot does not start on a new board.
- [ ] Only Done activities count in the horse page totals.
- [ ] On a 375 px wide phone, the first screen of the board shows 8 or more horse rows, and every button is 44 px or more.

### 9.3 Phase 3 — Week plan, venues and riders

**Result:** The board fills itself from the week plan. Each horse has a rider and gear.

Build:

1. `Venue`, `Rider`, `LotWeekPlan` (7.5). The phase 3 fields on `Activity`, `LotDay` and `HorseTraining`.
2. `start_board` source `week_plan`: the default when the lot has a plan for that weekday; else the lot's usual work.
3. New activities take the horse's default rider and default gear. A change for one day does not change the default (R5).
4. Set up tab: venues, riders, a week grid for each lot (rows: lots, columns: Monday to Sunday).
5. The board shows the rider on each horse row. A **Rider** filter on the board shows "my horses today".
6. "The owner" as a rider (`rider_is_owner`, 7.5). "No lot" on a weekday in the week grid.

Definition of done:

- [ ] A Tuesday board starts with each lot's Tuesday work and venue.
- [ ] A rider changed for one day does not change the horse's default rider.
- [ ] An archived rider does not show in pickers. Old activities still show the name.
- [ ] A rider with a linked login sees their own horses first when the board opens.
- [ ] An activity with "The owner" as rider shows the owner's name on the horse page. It cannot also have a rider from the list.
- [ ] A week plan with "No lot" for every lot on Sunday: starting a Sunday board from the week plan makes no lot days and says so.

### 9.4 Phase 4 — Signals, dashboard and print

**Result:** The trainer sees workload facts at a glance. The dashboard shows training. The board can go on paper or a TV.

Build:

1. `training/load.py`, pure functions: for a horse and a date, `last_work_date`, `days_since_work`, `days_since_fast`, `days_worked_7`, `fast_sessions_7`, `minutes_7`, `minutes_28`, `fast_days_running`. Only Done activities with kind ridden or not ridden count as work.
2. Chips on the board and the horse Training card: "No work for 4 days" (threshold setting, default 3, only for horses on the board), "Fast work 2 days running", "First work in 16 days" (after 14 or more days with no work). Information only (5.9).
3. The 28-day strip on the horse Training card.
4. Dashboard timeline: one row for each lot day that is done, in a new chip group "Training". `KIND_GROUP` and `GROUP_LABELS` in `core/dashboard/activity.py`.
5. Attention items in `COLLECTORS`, with switches in `core/reminders.py`: "Not confirmed" (Planned activities dated before today, counted by lot day and by horse), and "No work for N days". Nothing changes a Planned activity automatically (D4).
6. Exercise days a week (D1): `HorseTraining.days_per_week`. A chip on the horse card and the board: "3 of 5 days this week" (Monday to Sunday, Done ridden or not ridden work only). It shows only when `days_per_week` is set.
7. Print view, TV view (8.6), and a CSV download on the Log tab.

Definition of done:

- [ ] `load.py` has a unit test for each function, including a gap, a rest day, a Did not work and a horse with no activities.
- [ ] A role with `training` hidden sees no training rows on the dashboard.
- [ ] A lot not confirmed yesterday shows as an attention item. It stays Planned the next day and the day after. It leaves the list when someone confirms it or marks it Did not work.
- [ ] A horse with 5 days a week and 3 Done days by Friday shows "3 of 5 days this week". A Did not work day does not count.
- [ ] The print view of a 3-lot, 30-horse board fits on one A4 page.

### 9.5 Phase 5 — Charges

**Result:** The yard can charge owners for some sessions, and never for a horse whose livery includes exercise.

Build:

1. `WorkType.price`. `HorseTraining.billing` (Included in livery / Charge for each session), default Included.
2. `ExtraCharge.ChargeType.TRAINING`. A `Activity.extra_charge` link.
3. `training/charges.py` `sync_activity_charge(activity)`, with the same rules as `sync_record_charge` (`health/views.py:345`): create when Done and priced and the horse is "Charge for each session"; update an unbilled charge; delete an unbilled charge when the activity changes to not Done or is deleted; never change a charge on a live invoice.
4. Prices show only to roles with `charges` view.

Definition of done:

- [ ] A Done Schooling session (£25) on a "Charge for each session" horse makes one £25 charge for the current owner.
- [ ] The same session on an "Included in livery" horse makes no charge.
- [ ] Mark lot done for 10 horses makes charges only for the priced, charged horses, in one transaction.
- [ ] Undo lot done deletes the charges that are not on a live invoice.
- [ ] A charge on a live invoice does not change when its activity changes, and the message says so.
- [ ] A co-owned horse's charge splits on the invoice as other charges do.

---

## 10. Testing

### 10.1 Pure functions to unit test

- `training/services.py`: `start_board`, `set_lot_work`, `change_horse_work`, `move_horse`, `mark_lot_done`, `undo_lot_done`, `cancel_lot`, `undo_cancel_lot`, `horse_left`.
- `training/load.py`: every function (phase 4).
- `training/charges.py`: `sync_activity_charge` (phase 5).

### 10.2 New test files

`training/` keeps flat `test_*.py` files, as `health/` and `billing/` do:

- `test_activity.py`: forms, defaults, status and reason rules, delete.
- `test_access.py`: hidden, view and full for every view, with `make_user_with_access`.
- `test_bulk.py`: the bulk bar dispatch and the apply.
- `test_board.py`: the service rules and the board views.
- `test_departure.py`: `horse_left` from both departure paths.
- `test_load.py`, `test_charges.py`.

Tests do not run migrations (`test_settings.py`). Make work types in `setUp`, or import the seed migration, as `health/test_farrier_today.py` does.

### 10.3 Checks by hand

- On a real phone at the yard: start the board, change a lot, mark it done, record Did not work. Note the signal (D9).
- The print view on paper.
- The TV view on a TV in the tack room for one morning.

---

## 11. Risks

| Risk | What we do |
|---|---|
| Staff do not confirm lots, so the history is empty. | One tap for each lot. The "not confirmed" line on the board. The attention item. Measure it in the phase 2 gate (section 13). If the count is high, the owner can revisit D4. |
| A weak signal at the yard makes the board slow. | Test before phase 2 (D9). The board is a small page. If needed, add an offline phase. |
| The timeline on the horse page fills with daily training. | Capped at 10 rows for each type. The Training card holds the detail. |
| The word "Lot" is racing language. | Help text on the Lots tab: "A lot is a group of horses that work together." Revisit if livery yards find it unclear. |
| Daily records make many rows. | 30 horses × 365 days ≈ 11,000 rows a year, plus history rows. Small for PostgreSQL. Index (horse, date) and (date, status). |
| Rider names are personal data. | Add riders to `DATA_RETENTION.md`. An archived rider keeps the name on old records. |
| Charges for a horse whose livery includes exercise. | Default "Included in livery". A test in phase 5. |

---

## 12. Not in this plan

These came from the research. Each needs its own decision and its own plan.

| Item | Why not now |
|---|---|
| Owner reports or an owner view of training (R15) | No owner login exists (`LAUNCH_READINESS_PLAN.md`). A first step could be a "Recent work" section on the monthly statement email. |
| Offline mode (R17) | Large: an API, a stored app and sync rules, as in `C_VET_PLAN.md` section 5. Only if D9 says the signal is poor. |
| Drag and drop (R2) | D10. A later add-on for wide screens. |
| A daily rate for each training status (R16, Ardex) | The yard bills with placements and rate types today. A training rate would be a second way to bill a day. Needs a decision on how it sits next to livery rates. |
| Young horse stages (R13) | A checklist of steps for each young horse. Useful for pre-training and studs. A separate small plan. |
| GPS, heart rate monitors, wearables | Users report that the data is not accurate. Import can come later. |
| Race entries and declarations | Racing is out of the market plan. |
| Photos and videos on an activity | The horse page already has photos. Link them later. |
| Automatic confirm at midnight | D4: work stays Planned until someone confirms it. |
| A "new each day" lot mode as a yard setting | D2: standing lots for now. 5.11 keeps the switch cheap. |

---

## 13. Ship order and gates

1. The owner answers D1 to D4 (section 4). D9 is tested at the yard.
2. Phase 1. Gate: the owner records training for real horses for one week.
3. Phase 2. Gate: one week of the board in daily use. Count the lots that were confirmed.
4. Phase 3. Gate: the week plan fills the board correctly for one week.
5. Phase 4.
6. Phase 5, only if D8 is yes.
7. Turn on `TRAINING_ENABLED` in production after phase 2 passes its gate.

---

## 14. Files each phase adds or changes

| Phase | New | Changed |
|---|---|---|
| 1 | `training/` (models, forms, views, urls, services, bulk, admin, migrations, tests), `templates/training/`, `templates/help/articles/training.html` | `settings.py`, `.env.example`, `core/features.py`, `core/context_processors.py`, `core/help.py`, `horse_management/urls.py`, `core/views/horses.py`, `templates/horses/horse_detail.html`, `templates/horses/_quick_actions.html`, `templates/horses/partials/preview.html`, `templates/health/partials/bulk_action_bar.html`, `health/views.py`, `templates/includes/_sidebar.html`, `templates/base.html`, `templates/includes/_add_menu_items.html`, `templates/includes/_icons.html`, `docs/README.md` |
| 2 | Board and lots templates | `training/models.py`, `training/services.py`, `core/services.py` (departure calls), horse page Training card |
| 3 | Set up templates for venues, riders, week plan | `training/models.py`, `training/services.py` |
| 4 | `training/load.py`, print and TV templates | `core/dashboard/activity.py`, `core/dashboard/attention.py`, `core/reminders.py` |
| 5 | `training/charges.py` | `billing/models.py` (charge type), `training/models.py` |

---

## 15. Sources

Product pages and help centres (vendor material; not checked by use):

- [Prism: How to create Trackwork](https://help.prism.horse/portal/en/kb/articles/how-to-create-trackwork)
- [Prism: filter, print and record notes on the Trackwork schedule](https://help.prism.horse/portal/en/kb/articles/how-to-filter-the-trackwork-schedule)
- [Prism: default Work Rider](https://help.prism.horse/portal/en/kb/articles/how-to-assign-default-work-rider-to-a-horse)
- [Prism: default Work Gear](https://help.prism.horse/portal/en/kb/articles/assign-default-work-gear-to-a-horse)
- [Ardex: Trackwork allocation](https://intercom.help/essentials-ardex/en/articles/9560895-trackwork-allocation)
- [Ardex Premier: Daily charges to horses](https://intercom.help/ardexpremier/en/articles/3565382-daily-charges-to-horses)
- [Ardex Premier: Bulk agistment and training charges](https://intercom.help/ardexpremier/en/articles/3556969-bulk-agistment-training-charges)
- [Racehorse Trainer Manager](http://racehorsetrainermanager.co.uk/)
- [Yardman Online](https://www.yardman-software.com/yardman-software-online.htm)
- [The Rundown app](https://rev1ventures.com/blog/the-rundown-app-is-winning-at-the-track)
- [TLore Owner app](https://pastthewire.com/tjc-innovations-releases-tlore-owner-app/)
- [miStable](https://mistable.com/products)
- [FarmKeep horse management](https://www.farmkeep.com/farm-type/horse-management-software)
- [Equus Note review](https://horserookie.com/equus-note-app-review/)

How yards train:

- [Retraining of Racehorses: Life of a racehorse](https://www.ror.org.uk/care-and-training/life-of-a-racehorse)
- [Great British Racing: The equine fitness plan](https://www.greatbritishracing.com/features/the-equine-fitness-plan-how-racehorses-get-fit/)
- [The Conversation: How trainers keep horses in winning form and injury free](https://theconversation.com/how-trainers-keep-horses-in-winning-form-and-injury-free-33253)
- [University of Melbourne: Track surfaces and alternatives to ridden exercise](https://findanexpert.unimelb.edu.au/scholarlywork/1359752-track-surfaces-used-for-ridden-workouts-and-alternatives-to-ridden-exercise-for-thoroughbred-horses-in-race-training)
- [Highclere Racing: The pre-training process](https://www.highclereracing.co.uk/november-newsletter/pretraining)
- [British Eventing: Getting your horse fit to event](https://beprepared.britisheventing.com/blog/getting-your-horse-fit-to-event)
- [Horse & Hound: Formulating a fitness plan](https://www.horseandhound.co.uk/features/formulating-a-fitness-plan/)

Logs, soundness and welfare:

- [EquiSearch: Keeping logs](https://equisearch.com/articles/tmarticle3/)
- [Pony Club record book](https://raccoonvalley.ponyclub.org/ContentDocs/30788/Documents/Instrctional/record%20book.pdf)
- [Horse Illustrated: How keeping a journal helps](https://www.horseillustrated.com/horse-community-how-keeping-a-journal-helps-you-and-your-horse/)
- [Royal Veterinary College: Training Injury Prevention Study](https://www.rvc.ac.uk/research/projects/training-injury-prevention-study)
- [ROA: Trot-ups and pre-race vet checks](https://www.roa.co.uk/resources/trot-ups-and-pre-race-vet-checks.html)
- [HRNZ: Communication with owners](https://www.hrnz.co.nz/next-gen/trainers-toolkit/engage-owners/communication/)

Signal and offline:

- [Farmbrite: Mobile farm management](https://www.farmbrite.com/post/mobile-farm-management-a-practical-guide-and-the-tools-that-actually-work-in-the-field)
- [Gros.farm: Farm worker app](https://gros.farm/en-us/farm-worker-app)

---

## 16. Revision 2: review findings and fixes

Each finding is fixed in place. The section that holds the fix is in the last column. Section 16.3, from the separate review against the codebase, follows when that review is complete.

### 16.1 From the owner's decisions

| # | Change | Where |
|---|---|---|
| O1 | D1 "both": livery yards get "The owner" as a rider and exercise days a week. | 4.1, 7.5, 9.3, 9.4 |
| O2 | D2 "standing lots for now": the design must not lock it in. New principle 5.11. `start_board` gets an **Empty** source. A lot day is made for every active lot. `LotMember.horse` is a foreign key with a unique constraint, not a one-to-one field. | 5.11, 7.3, 8.2, 9.2 |
| O3 | D4 "Planned until confirmed": the auto-confirm setting and its nightly task are removed. A past Planned activity shows as **Not confirmed**. Staff can confirm it later from the board, the horse page and the Log. | 4.1, 5.2, 8.1, 8.4, 9.1, 9.4, 12 |

### 16.2 From the critical review of the logic

| # | Finding | Fix | Where |
|---|---|---|---|
| L1 | No rule stopped **Done** on a date in the future. | Done is refused for a date after today, in the form and in the service. | 5.12, 7.2, 9.1 |
| L2 | The status changes of an activity were spread over several sections, and some were missing (confirm a single activity, confirm later, undo Did not work). | One table of every change and when it is allowed. | 5.12 |
| L3 | "Undo until the end of the day" made a wrong record impossible to correct the next day, and "the end of the day" was not defined. | **Reopen lot**, with no time limit. | 5.12, 8.2 |
| L4 | With D4, past Planned activities could only be confirmed on the board. Activities added on the horse page with a later date had no way to be confirmed. | **Mark done** on the horse card and on selected rows in the Log, from phase 1. | 8.1, 8.4, 9.1 |
| L5 | "Copy yesterday" failed after a day with no board (for example a Sunday rest). | Copy the **last board before** the date. | 8.2, 9.2 |
| L6 | A change to a standing lot after the board started had no rule. | The board does not change by itself. A hint names the missing horses, with **Add**. Past boards never change. | 8.2, 9.2 |
| L7 | A horse put on Box rest after the board started stayed on the board. | The status change asks "Take off the boards from this date?" (default yes). It deletes Planned activities only. | 7.4, 9.2 |
| L8 | Planned activities for later dates would sort to the top of the horse timeline. | The timeline shows only activities dated today or earlier. Later plans show in **Coming up** on the Training card. | 8.4, 9.1 |
| L9 | The week plan had no way to say "this lot does not go out on Sunday". | `LotWeekPlan.no_lot`. | 7.5, 9.3 |
| L10 | `Cancel lot` and its undo, and the meaning of "Cancelled", were not defined. | `cancel_lot` sets Planned to Did not work with one reason. **Reopen lot** undoes it. Done stays Done. | 8.2, 9.2 |
| L11 | Two people tapping **Mark lot done** at once, or a double tap, had no rule. | Every bulk change is one `UPDATE ... WHERE status = <from>`. The second request changes nothing and is not an error. | 5.12, 9.1 |
| L12 | Status and effort were shown only by colour and border. | Principle 5.13: words or icons with text, as well as colour. | 5.13 |
| L13 | A TV left signed in in the tack room could open every record its login can see. | A separate TV login with `training` view only. Check the session length first. | 8.6 |
| L14 | A board for a later date (planning tomorrow in the evening) was not described. | Allowed. **Mark lot done** shows only on dates of today or earlier. | 8.2, 9.2 |
| L15 | The Lots tab did not explain what a lot is, or that it does not change a board that already started. | Help text at the top of the Lots tab. | 8.3 |
| L16 | The risk "staff do not confirm" relied on auto-confirm, which D4 removed. | The board's "not confirmed" line, the attention item, and a count at the phase 2 gate. | 11, 13 |
