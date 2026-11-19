# Training dashboard (Overview) — plan

**Status:** Plan. Nothing is built. The owner must decide T1 to T4 (section 3) before the build starts.
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

The page gives facts. It gives no advice and claims no risk (principle 5.9 of the parent plan).

## 3. Owner decisions

| # | Question | Recommended | Why |
|---|---|---|---|
| T1 | Where does the Overview go? (a) A first tab, **Overview**, that is the Training landing page. (b) A band of figures above the tabs on every Training page. | **(a) A first tab.** | A band pushes the board down. On a phone the board would start below the first screen. The board is the page staff use most during the morning. A tab keeps the board clean. The tab bar and header stay the same on every tab, so the section still feels like one product. |
| T2 | What does the sidebar **Training** link open? | **Overview.** | It is the summary. "Open today's board" is the first button on it, so the board is one tap away. |
| T3 | What does the phone bottom-bar Training item open? | **Overview**, the same as the sidebar. | One rule is easier to learn. The large "Open today's board" button is at the top of the phone screen. If riders find the extra tap slow, change this item to Board later. |
| T4 | Who sees the Overview? | **Everyone with `training` view.** Riders see the same page. | No data on it is private. Prices never show (D11). |

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
- Query budget: a test with 30 horses in 3 lots, and a fixed query count (target 15 or fewer). The main dashboard budget (34) does not change.
- No polling. The page is fresh on each load. A **Refresh** is the browser's own.

## 8. Phases

Each phase is one pull request, with tests and the usual checks.

| Phase | Content | Done when |
|---|---|---|
| A | The Overview tab and the landing change (T1, T2): header, figures, Today, Needs attention. `_header.html` (C1) and the new tab (C5). | The sidebar link opens the Overview. The four figures and two cards show the right data in tests. Query count test passes. Phone and desktop screenshots checked. |
| B | This week grid, Tomorrow, Riders today. Faults M1 to M5. | The grid matches the horse page strip for each horse in tests. No page scroll sideways at 375 wide. |
| C | First-run checklist (section 5), C2 to C4 on the other tabs. | A new yard sees the checklist. Each tab has one primary button and an empty state. |

## 9. Risks

| Risk | What we do |
|---|---|
| The Overview becomes slow with many horses. | One read of each table; a query count test; the week grid uses 14 days of rows, not 60. |
| The Overview and the main dashboard show different attention items. | Both use `training/attention.py`. One test checks that both lists are the same. |
| Staff open the Overview and must tap again for the board. | "Open today's board" is the first, largest button. T3 can change the phone item to Board later. |
| Too much on one page. | Each block answers one question in section 2. A block with no data is not shown (except Today). |
