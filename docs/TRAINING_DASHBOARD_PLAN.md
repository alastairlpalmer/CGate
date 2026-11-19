# Training dashboard (Overview) — plan

**Status:** Plan, ready for phase A. Nothing is built. The owner decided T1 to T7 on 2026-11-19 (section 3).

**Revision 3.** 2026-11-19. The owner decided T1 to T7. T7 is new: the yard's own **Stable Staff** role gets Full on Training (section 3.1).

**Revision 2.** 2026-11-19. The owner asked how the page works for a yard with lots of 20 to 30 horses, and asked for an area for horses that are not in work. New: section 4.3 (large yards), section 4.4 (Not in work), T5 and T6, and a second mock-up with 120 horses in 5 lots.
**Date:** 2026-11-19.
**Parent plan:** [`TRAINING_MODULE_PLAN.md`](TRAINING_MODULE_PLAN.md). Phases 1 to 4 of that plan are built, behind `TRAINING_ENABLED`.

---

## 1. The problem

Training has four tabs: Board, Lots, Log and Set up. Each tab does one job well. But:

- There is no page that shows the **whole yard's training** at one time. The sidebar link goes directly to today's board (`training_home` redirects to `training_board`).
- To answer "how is the week going?", the trainer must open the board, then the Log, then horse pages.
- The attention items (not confirmed, no work, fast work) show on the main dashboard only. Training itself does not show them.
- A new yard sees an empty board with no guide to the first steps.
- The tab pages have different headers and different main buttons. The section does not feel like one product.

The owner's words: "it feels basic". The Overview is the clean interface that wraps the section.

## 2. What the Overview must answer

These are the trainer's questions at 06:30, in order. Each answer is one block on the page.

| # | Question | Block | Data that exists now |
|---|---|---|---|
| Q1 | Is today's board started? How far through the day are we? | Figures: **Today's lots** | `LotDay` rows for today |
| Q2 | Which lots go out, when, with what work, and where? | **Today** card | `LotDay`, `Lot`, `WorkType`, `Venue` |
| Q3 | Who does not go out today, and why? | **Today** card, last line; figure **Not going out** | `HorseTraining` status, Not done rows with reason |
| Q4 | Which horses need my attention? | **Needs attention** card; figure | `training/attention.py`, `load.chips()` |
| Q5 | Is any work not confirmed? | **Needs attention** card (first rows) | `load.unconfirmed_days`, Planned rows before today |
| Q6 | How has each horse worked this week? | **This week** grid | `load.rows_for`, `load.strip()` (7 days, not 28) |
| Q7 | Is tomorrow ready? | **Tomorrow** card | `LotDay` rows for tomorrow, `LotWeekPlan` |
| Q8 | Who rides what today? | **Riders today** card | `Activity.rider`, `HorseTraining` default rider |
| Q9 | Which horses that are not in work need an action? | **Not in work** card; figure **Not in work** | `HorseTraining` status and `status_since`, `MedicalCondition`, `VetVisit.follow_up_date`, and a new review date (T5) |

The page gives facts. It gives no advice and claims no risk (principle 5.9 of the parent plan).

## 3. Owner decisions

All decided on 2026-11-19. Each one is the recommended answer.

| # | Question | Decided | Why |
|---|---|---|---|
| T1 | Where does the Overview go? (a) A first tab, **Overview**, that is the Training landing page. (b) A band of figures above the tabs on every Training page. | **(a) A first tab.** | A band pushes the board down. On a phone the board would start below the first screen. The board is the page staff use most during the morning. A tab keeps the board clean. The tab bar and header stay the same on every tab, so the section still feels like one product. |
| T2 | What does the sidebar **Training** link open? | **Overview.** | It is the summary. "Open today's board" is the first button on it, so the board is one tap away. |
| T3 | What does the phone bottom-bar Training item open? | **Overview**, the same as the sidebar. | One rule is easier to learn. The large "Open today's board" button is at the top of the phone screen. If riders find the extra tap slow, change this item to Board later. |
| T4 | Who sees the Overview? | **Everyone with `training` view.** Riders see the same page. **Stable Staff must see it** (T7). | No data on it is private. **No prices show** on the Overview or on any Training page (D11). A test checks this. |
| T5 | Add a **Review on** date to a horse's training status? | **Yes.** One optional date, set in the status pop-up. | Without a date, the app cannot know when a horse on rest needs a decision. "Review due" is the main action in the Not in work card (4.4). |
| T6 | Do horses with the status **Not in training** show in the Not in work card? | **No.** Only Resting and Box rest. | Not in training covers broodmares, youngstock and retired horses. They are not part of the training yard's work. They can be 50 % of a large yard. |
| T7 | What can the yard's **Stable Staff** role do in Training, and how does it get the access? | **Full on Training** (see and record), set by a **migration that finds the role by name**. | Stable staff ride the lots, so they mark lots done and record training. Section 3.1. |

### 3.1 The Stable Staff role (T7)

"Stable Staff" is not a seeded role. The yard made it in Settings → Users & Roles. The app gives it access in a data migration, `training/migrations/0008_stable_staff_training.py`, in phase A:

- Find roles where the name, with spaces trimmed and in lower case, is `stable staff`, and `is_system` is false.
- Set `training` to `full` only if its value is absent or `hidden`. A value that an administrator chose stays.
- Change no other feature. Stable Staff keeps its current access to money, owners and settings. Training shows no prices, so Full on Training shows no money.
- If no role has that name, the migration does nothing.
- The reverse migration does nothing, because it cannot know the value before.
- Tests import the migration module and check: a match, a match with other letter case, a role with `training` already set, and no match.

A yard that names the role differently sets Training in Settings → Users & Roles. The help guide says this.

## 4. Layout

The mock-up shows the layout with sample data. Desktop (1280 wide) and phone (375 wide) screenshots are in the PR.

```
┌ Training                      [Open today's board] [Record training] ┐
│ Thursday 20 November 2026                                            │
│ Overview · Board · Lots · Log · Set up                               │
├──────────────┬──────────────┬──────────────┬─────────────────────────┤
│ Today's lots │ Horses in    │ Not going    │ Needs attention         │
│ 1 of 3 done  │ work 24      │ out 3        │ 5                       │
├──────────────┴──────────────┼──────────────┴─────────────────────────┤
│ Today                        │ Needs attention                        │
│ 1st lot 07:30 Canter … Done  │ Not confirmed: 2nd lot, Wed · Open     │
│ 2nd lot 09:00 Walker Planned │ No work 4 days: Kestrel                │
│ 3rd lot 10:30 Hack  Planned  │ Fast 2 days: Saltire                   │
│ Not going out: Juniper …     │ First work in 16 days: Umber           │
├──────────────────────────────┴────────────────────────────────────────┤
│ This week   (horses by lot × Mon–Sun, letter squares, Days, Fast)     │
├──────────────────────────────┬────────────────────────────────────────┤
│ Tomorrow  (not started · Start from week plan)  │ Riders today         │
└──────────────────────────────┴────────────────────────────────────────┘
```

On a phone the blocks go in one column, in the order Q1 to Q8. The figures go in a 2 × 2 grid.

### 4.1 Rules for each block

1. **Header.** Title, today's date, and two buttons. The primary button is **Open today's board** (or **Start today's board** when there is no board yet). The second button is **Record training**. The same header shows on every Training tab (section 6).
2. **Figures.** Four tiles. Each tile is a link: Today's lots → board; Horses in work → Lots tab with the status filter; Not going out → board, filtered; Needs attention → the card below (an anchor).
3. **Today.** One row for each lot: name, time, work, venue, a progress bar (done rows ÷ all rows) and a status badge. The last line lists the horses that do not go out, with the reason. If there is no board, the card shows one line and a **Start today's board** button that uses the yard's board source.
4. **Needs attention.** Not confirmed rows first, then the chips from `load.chips()` in this order: no work, fast work running, first work, days this week. Maximum 8 rows, then "and N more" with a link to the Log. Each row opens the horse page, or the board for a lot row.
5. **This week.** Horses grouped by today's lots, then "Not in a lot". Columns Mon to Sun: the same letter squares as the horse page strip (E, S, F, R, ×, ?). Then **Days** (days worked this week) and **Fast** (fast sessions in 7 days). Today's column has a light background.
6. **Tomorrow.** If tomorrow has a board: its lots and the count of horses. If not: "Not started" and a **Start** button with the yard's board source (week plan, lots or copy).
7. **Riders today.** Each rider and the horses they ride today, from the board rows. Riders with no horse are not shown.

### 4.2 Faults seen in the mock-up, to fix in the build

| # | Fault | Fix |
|---|---|---|
| M1 | Desktop: the This week name column is too wide. | Fixed width `12rem`, names truncate with a title. |
| M2 | Phone: the "Not going out" line in the Today card breaks into a narrow column of words. | Put it as a list under a small heading: one horse a line, reason in grey. |
| M3 | Phone: the This week grid is wider than the screen. | Scroll the grid sideways inside its card, with the name column sticky. The page itself must not scroll sideways. |
| M4 | Phone: the Horses in work tile text is cut ("2…"). | Show only "18 in work" on the tile; the full split shows on the Lots tab. |
| M5 | Phone: the Needs attention "Open" link is far from its row text. | Make the whole row the link, with a chevron. |
| M6 | Large-yard mock-up: today's column shows "0/26" in orange, but those lots are only planned. | Today and later days are grey (4.3). |

### 4.3 Large yards (lots of 20 to 30 horses)

There are **no size tiers to set**. The page changes by itself when a block gets long. One layout and one code path is easier to test, and a yard that grows does not need to change a setting. The second mock-up shows a yard with 120 horses in 5 lots.

| Block | Small yard (up to 25 horses on the board) | Large yard (more than 25) |
|---|---|---|
| Lot filter | Not shown. | Chips at the top: **All lots**, then one chip for each of today's lots. A chip filters every block on the page (`?lot=<pk>`). A head lad who runs one lot sees only that lot. |
| Figures | The same. | The same. The numbers get bigger; the tiles do not. |
| Today | One row for each lot. | The same. A yard has few lots, so this block does not get long. Each row adds a line: "22 done · 2 did not work", or "26 planned" and the first riders. |
| Needs attention | One row for each horse, maximum 8. | When there are more than 8 rows: **one row for each kind**, with the count ("No work 4+ days · 7 horses"). Tap a row to show the names. Not confirmed lots stay as one row each. |
| This week | One row for each horse, grouped by lot. | **One summary row for each lot**: for each day, horses worked ÷ horses in the lot ("23/24"), and the fast sessions in the week. Tap a lot to show its horses (first 10, then "Show all 24 horses"). With a lot chip on, that lot's horses show open. |
| Not in work | Each horse. | Horses that need action first, each on its own row. The others fold into one row for each status ("Resting · 24 more, review dates set"). |
| Riders today | Each rider with their horses. | One row: "11 riders · most: Sam 9, Jo 8, Alex 8". Tap to show all. |

Rules for the lot summary row:

- A day before today: dark when every horse worked, orange when 3 or more did not work.
- Today and later days: grey. Planned work is not a fault (fault M6).

The limit (25) is a constant in the code, not a setting. We can change it after the yard uses the page.

### 4.4 Not in work

The owner's point: a horse that is not in work can still need an action. Today these horses are only on the Lots tab and the horse page.

**Who shows:** horses with the status Resting or Box rest (T6).

**What each row shows:** name, status and days in it ("Box rest 42 days"), an active or monitoring condition from Health, and one action badge. The **Change** button opens the existing status pop-up (`training_horse_edit`), where the trainer can put the horse back in work or set a new review date.

**The action badges** (facts, not advice):

| Badge | When |
|---|---|
| Review due today | Review on = today. |
| Review overdue N days | Review on is before today. |
| Vet follow-up <day> | A `VetVisit.follow_up_date` in the next 7 days, or overdue. |
| No review date | Box rest with no review date, or Resting for 28 days or more with no review date. |

A horse with no badge folds into its status group (4.3).

**Data change (T5, decided):** add `HorseTraining.review_on = DateField(null=True, blank=True)` with label "Review on". It is in the status pop-up, under the status. When the status changes to one that goes on the board, the date is cleared. One migration; history records it.

**Link to Needs attention:** "Review due" and "Review overdue" also count in the Needs attention figure, and on the main dashboard through `training/attention.py`.

**Not going out today** stays in the Today card. That list is horses on the board that do not work today. Not in work is horses off the board.

## 5. First-run checklist

When Training is not set up, the Overview shows a checklist card at the top, in place of the figures:

1. Work types — ✓ (seeded in phase 1).
2. Make your lots — link to Lots.
3. Put horses in work — link to Lots → add horses.
4. Choose how the board starts — link to Set up.
5. Start today's board — button.

Each step shows ✓ when its data exists. The card goes away when steps 2 to 4 are done. No new model: the checks are counts.

## 6. "Clean interface" work across the tabs

The Overview alone does not fix "it feels basic". These changes give the section one shape:

| # | Change | Tab |
|---|---|---|
| C1 | One header include, `training/_header.html`: title, date or sub-title, one primary button and up to two more buttons. Every tab uses it. | All |
| C2 | Each tab has one primary button: Overview → Open today's board; Board → Mark lot done (per lot, as now) and Start; Lots → Add lot; Log → Record training; Set up → none. | All |
| C3 | Set up: split into cards with headings: Board, Work types, Venues, Riders. Each card has its own **Add** button. | Set up |
| C4 | Empty states: each tab says what to do next, with a button, in place of a blank table. | All |
| C5 | Tab bar: add **Overview** as the first tab. | All |

## 7. Data and speed

- One view, `training_overview`, in `training/views.py`, with `TrainingEnabledMixin` and `feature_required('training')`.
- Read the data once: today's and tomorrow's `LotDay` rows with their activities (`prefetch_related`), the `HorseTraining` rows that are on the board, and one `load.rows_for(horse_ids, today, window=14)` call. All blocks use these lists. No query in a loop.
- Re-use: `board.py` for the day's rows, `load.chips()` and the fact functions, `attention.collect` for the Needs attention rows (one source for the main dashboard and the Overview).
- Query budget: tests with 10 horses and with 150 horses in 5 lots. The query count must be the same for both (target 15 or fewer). The main dashboard budget (34) does not change, because the review date is read in the query that `attention.py` already makes.
- A large yard: 150 horses × 14 days is about 2,100 rows. That is small. The folded rows are built on the server; there is no extra request when a user opens one.
- No polling. The page is fresh on each load. A **Refresh** is the browser's own.

## 8. Phases

Each phase is one pull request, with tests and the usual checks.

| Phase | Content | Done when |
|---|---|---|
| A | The Stable Staff migration (3.1). The Overview tab and the landing change (T1, T2): header, figures, Today, Needs attention, **Not in work** with the review date (4.4, T5). The lot filter and the large-yard folds for these blocks (4.3). `_header.html` (C1) and the new tab (C5). | The sidebar link opens the Overview. The figures and cards show the right data in tests, with 10 horses and with 150 horses in 5 lots. Query count test passes for both. No price shows on the page (test). A Stable Staff login opens the Overview and marks a lot done. Phone and desktop screenshots checked. |
| B | This week grid with the lot summary rows (4.3), Tomorrow, Riders today. Faults M1 to M6. | The grid matches the horse page strip for each horse in tests. No page scroll sideways at 375 wide. |
| C | First-run checklist (section 5), C2 to C4 on the other tabs. | A new yard sees the checklist. Each tab has one primary button and an empty state. |

## 9. Risks

| Risk | What we do |
|---|---|
| The Overview becomes slow with many horses. | One read of each table; a query count test; the week grid uses 14 days of rows, not 60. |
| The Overview and the main dashboard show different attention items. | Both use `training/attention.py`. One test checks that both lists are the same. |
| Staff open the Overview and must tap again for the board. | "Open today's board" is the first, largest button. T3 can change the phone item to Board later. |
| Too much on one page. | Each block answers one question in section 2. A block with no data is not shown (except Today). |
