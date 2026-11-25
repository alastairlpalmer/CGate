# Training board redesign — plan

**Status:** Built (section 9). Revision 2 of the plan. Training stays behind `TRAINING_ENABLED`. The owner has not yet decided R1 to R8 (section 6): the build uses the recommended answers.
**Date:** 2026-11-25.
**Parent plans:** [`TRAINING_MODULE_PLAN.md`](TRAINING_MODULE_PLAN.md) and [`TRAINING_DASHBOARD_PLAN.md`](TRAINING_DASHBOARD_PLAN.md). The principles in section 5 of the module plan do not change.
**Method:** the workflow of [open-claude-design](https://github.com/stefan1ne/open-claude-design): a brief, the brand tokens that exist, a first pass that is visible early, a five-point critique, then a revision (section 2).

---

## 1. The brief

The owner's priorities for the Training section, in the owner's order:

| # | Priority | In this plan |
|---|---|---|
| P1 | It is very easy to plan exercise for horses. | Section 5.1 |
| P2 | It is very easy to record exercise for horses. The records go on a profile page. | Sections 5.2 and 5.3 |
| P3 | It is easy to add notes about how a horse worked and how it is going. | Section 5.4 |
| P4 | It is easy to share information about the lots with the team. | Section 5.5 |

The result must be functional, easy to operate, and professional.

## 2. How the design repository was used

The repository is a design tool, with rules for an agent that designs. This plan uses its rules, not its code.

| Rule from the repository | How this plan uses it |
|---|---|
| Lock the brief before you draw (`discovery.ts`, rule 1). | Section 1. The questions the brief leaves open are in section 6, each with a recommended answer. |
| Use the brand that exists. Do not invent colours (`craft/color.md`). | Section 4. Every colour and font is a token from `tailwind.config.js`. No new colour. |
| A tool screen: "information density is the feature" (`discovery.ts`, A). | One line of facts for each horse. Numbers in tabular figures. No decoration. |
| Show a first pass early (`discovery.ts`, E). | `docs/training-redesign/wireframe-1.png`, made with the app's stylesheet. |
| Critique on five points, with evidence. Fix each weak point (`skills/critique`). | Section 7. |
| One accent, used with discipline (`craft/color.md`). | Saddle stays for the primary action and for attention only. The new **Done** button on a row is a secondary button. |
| Rules against generic design (`craft/anti-ai-slop.md`). | No gradients, no emoji icons, no card with a coloured left border, no invented figures. Sample text in the wireframe is yard language. |
| Typography rules (`craft/typography.md`). | No new type sizes. Capitals only where the app already uses its label style. |

## 3. Review: what is there now

The section has five tabs (Overview, Board, Lots, Log, Set up) and a Training card on the horse page. The rules are sound: one record table, only Done counts, a lot's change does not overwrite a horse's own change. The faults are in the steps a person must do.

Checked on 2026-11-25 with a sample yard (24 horses, 3 lots, 21 days of records), at 1280 and 375 wide.

| # | Fault | Priority |
|---|---|---|
| F1 | A plan shows one day at a time. To see or plan Thursday, you go to the next day and start its board. No page shows the week by lot. | P1 |
| F2 | To change one horse's work for a day, you open a form with 10 inputs. | P1 |
| F3 | A horse's row on the board cannot show an instruction ("sit in behind Quill"). The Notes input holds the result of the work, and the board does not show it. | P1, P4 |
| F4 | The board has no **Done** for one horse. A rider who comes back cannot tick one horse. Only **Mark lot done** (all horses) or the full form can do it. | P2 |
| F5 | The record form shows 10 inputs with equal weight. Notes is the last one. Status and Reason are drop-down lists. | P2, P3 |
| F6 | The horse page shows 14 days of work, with notes cut at 140 characters. The full history is the Log table, with notes cut at 80 characters. There is no training page for one horse. | P2 |
| F7 | There is no place for a note that is not about one piece of work ("Coming to hand. Aim for a run in 3 weeks."). | P3 |
| F8 | "How it went" is free text only. You cannot see quickly how a horse is going. | P3 |
| F9 | The Print and TV links are hidden on a phone, and small grey text on a computer. | P4 |
| F10 | You cannot send the lots to the team. Yards send the list as a message. | P4 |
| F11 | There is no note for the whole day ("Farrier here at 10:00"). | P4 |
| F12 | The printed sheet shows no instruction for a horse. | P4 |

What works well and stays: **Mark lot done** in one tap; the sticky lot header; **Reopen lot**; the workload facts; the 28-day strip; the Overview; the pop-up sheet for every form.

## 4. Design direction

- **Persona:** a systems designer. The board is a work list. Each horse is one row with two lines at most.
- **Tokens (from `tailwind.config.js`, no change):** background `parchment` #F5F2EA; cards white; text `charcoal` #2C2C2C; secondary text `sage` #56737A; structure `light-sage` #E4EAEB; brand `forest` #3D5A63; the one accent `saddle` #A0522D. Headings DM Sans, body Source Sans 3.
- **Accent rule:** saddle is for the main action of a screen (**Mark lot done**, **Save**) and for attention (Not confirmed, a "Poor" rating). Nothing else.
- **Words, not only colour** (principle 5.13): each status and each rating is a word.
- **Phone first** (principle 5.8): tap targets of 44 px or more; 6 or more horse rows on the first screen of a 375 × 667 phone.
- **One distinctive detail:** the second line of a board row reads like a line of a yard's work list. Before work: "Sam · Boots · Sit in behind Quill". After work: "Sam · Good · Strong to the line."

## 5. The changes

### 5.1 Plan (P1)

| # | Change | Fixes |
|---|---|---|
| A1 | **A Plan tab.** One page shows the week: the lots down the side, Monday to Sunday across. Each cell shows the lot's work, venue, number of horses and status for that day. A day with no board shows the usual work from the week plan, with a dashed border, and a **Start** button. A cell opens that lot on that day's board. With Full access, **Change** opens the lot's pop-up. Arrows go to the week before and after. On a phone the days are a list, one card for each day. No sideways scroll. | F1 |
| A2 | **A new input, Instruction,** on a record: what the horse must do. It shows on the board row, the printed sheet, the TV view and the shared text. "Copy the last board" does not copy it, because it is for one day. | F3 |
| A3 | **The record sheet has a plan layout.** For a planned record after today, the sheet shows Work, Rider and Instruction first. | F2 |
| A4 | **The board row shows the plan:** rider, the horse's own work, gear and the instruction, on the second line. | F3 |

### 5.2 Record (P2)

| # | Change | Fixes |
|---|---|---|
| B1 | **Done on each row.** A planned row dated today or earlier has a **Done** button: one tap. The **⋯** menu of a Done row has **Undo done**. | F4 |
| B2 | **Did not work** moves into the **⋯** menu (tap it to see the reasons) and into the record sheet. The row has space for **Done**, and the rule is one for every screen width. | F4 |
| B3 | **The lot header counts:** "2 of 7 done" when a horse is done or did not work and the lot is open. | F4 |
| B4 | **The record sheet shows the answer first.** Status is three buttons (Planned, Done, Did not work). The reasons are buttons. Then **How it went**, then **Notes**. Work, date, rider, venue, minutes, effort and gear are under **Work details**, with their values in the heading so nothing is hidden. A new record still starts with Horse, Work and Date. | F5 |

### 5.3 The training profile (P2)

A new page for each horse: `/training/horse/<id>/`. Everyone with View on Training can open it.

| Block | Content |
|---|---|
| Header | Name (a link to the horse page), status and since, lot, usual rider and gear. Buttons: **Record training** (main), **Add note**, **Change** (status). |
| Facts | One card, four figures: days worked in the last 7, fast work in the last 7, last work, last fast work. The workload facts ("No work for 4 days") show as now. |
| Last 8 weeks | A calendar: one row for each week, Monday to Sunday, the same letter squares as the 28-day strip. |
| Coming up | Planned work after today, with the instruction. |
| Work and notes | Every record and every note, newest first, 30 to a page. Notes show in full. Filters: All, Notes only, Did not work. |

Links to it: the horse page's Training card (**Training profile** replaces "See all records"), the board's **⋯** menu, and the horse names on the Overview, Lots and Log.

### 5.4 Notes (P3)

| # | Change | Fixes |
|---|---|---|
| C1 | **How it went:** Good, OK or Poor on a Done record. Optional. It shows as a word on the board row, the profile and the Log, and it is in the CSV. | F8 |
| C2 | **Notes show where people look.** The board row shows the start of the note. The profile shows all of it. | F6 |
| C3 | **A note about the horse**, not about one piece of work: a date and text, with who wrote it. **Add note** is on the profile and in the board's **⋯** menu. The newest note shows on the horse page's Training card. | F7 |

### 5.5 Share (P4)

| # | Change | Fixes |
|---|---|---|
| D1 | **Share** on the board, on every screen size. It makes the day's lots as plain text. On a phone it opens the share sheet (WhatsApp, Messages, Mail). On a computer it copies the text. The same menu has **Print** and **TV view**. | F9, F10 |
| D2 | **Share this lot** at the foot of each lot: the same, for one lot. | F10 |
| D3 | **A note for the day**, at the top of the board. It is on the printed sheet, the TV view, the shared text, the Plan tab and the Overview. | F11 |
| D4 | The printed sheet and the TV view show each horse's instruction. | F12 |

Example of the shared text:

```
Lots, Wed 25 Nov
Farrier here at 10:00. Gallop closed after 2nd lot.

2nd lot, 09:00. Canter, Woodchip gallop
2 canters, steady. Trot back.
1. Marlow - Jo
2. Pennant - Sam (Boots). Sit in behind Quill
3. Quill - Sam. Walk and trot. Easy day after Sunday
Not going out: Osprey (shoe off or farrier)
```

### 5.6 Data

One migration, `training/migrations/0011_board_redesign.py`. No older migration changes.

| Model | Change |
|---|---|
| `Activity` | `rating` (1 Poor, 2 OK, 3 Good; empty allowed; only on Done). `instructions` (text, 200 characters at most). Both have history. |
| `TrainingNote` (new) | `horse`, `date`, `text`, `created_by`, `created_at`, `updated_at`. A note is not training work, so principle 5.1 (one record table) holds. It follows `FarrierNote` and `FoalNote`. |
| `DayNote` (new) | `date` (one for each date), `text`, `updated_by`, `updated_at`. Empty text deletes it. |

## 6. Decisions for the owner

The plan uses the recommended answer until the owner says otherwise.

| # | Question | Recommended, and used | Why |
|---|---|---|---|
| R1 | The words for "How it went"? | **Good, OK, Poor.** Three, optional. | Short words fit a phone row. More levels make people stop and think. |
| R2 | Is Instruction separate from Notes? | **Yes.** | The trainer writes the instruction before work. The rider writes the note after. One box would make the rider delete the instruction. |
| R3 | Does **Did not work** stay on the row? | **No. It moves to the ⋯ menu and the sheet.** | A phone row has space for one button. **Done** is the more usual tap for one horse. The other way: keep both on a computer only. |
| R4 | Where does the week plan go? | **A Plan tab.** No week strip on the board. | See the critique (section 7): a strip cost 44 px above every board on a phone. |
| R5 | How does Share send? | **As text, with the phone's share sheet.** No message inside the app. | Yards use group messages now. Text works in every app. In-app messages to riders can come later. |
| R6 | Who can add a note? | **Full on Training.** View reads. | The same rule as records. |
| R7 | Is the profile a Training page or a tab on the horse page? | **A Training page**, linked from the horse page. | The horse page must stay short (the horse page review). Training people stay in Training. |
| R8 | Does a "Poor" rating make an attention item? | **No.** It shows in the attention colour, and that is all. | Principle 5.9: facts, not advice. This can change after the yard uses it. |

## 7. Critique of the first pass, and revision 2

The first pass is `docs/training-redesign/wireframe-1.png`. Scores are out of 10, with the evidence.

| Point | Score | Evidence | Fix in revision 2 |
|---|---|---|---|
| Direction held | 7 | Frame 1 has a row of four equal buttons (Share, Print, TV view, Week). The ratings use a new pill style, a fourth kind of badge. | One **Share** button with a menu. The rating is a word in the row's line, not a pill. |
| Hierarchy | 6 | Frame 1: five blocks sit above the first horse (date, week strip, buttons, day note, lot header). The first horse row starts at 430 of 667 px. This breaks principle 5.8. Frame 3: four fact cards of equal weight use 130 px. | Remove the week strip. The Plan tab does that job (R4). The facts go in one card. |
| Detail | 6 | The lot header wraps to four lines at 375 wide. "Not conf." is an abbreviation. Each history row uses three lines, and a Done badge on every row is noise. | The header's second line cuts with "…". No abbreviations. A history row uses two lines; a badge shows only for Planned and Did not work. |
| Function | 7 | One tap marks a horse done. The sheet asks status, how it went and notes first. But the first pass had no Undo for a single Done, and the plan layout of the sheet was not drawn. | **Undo done** in the ⋯ menu. Three sheet layouts: new, record, plan. |
| Restraint | 6 | Two week tools (a strip and a grid). Rating buttons on every Done row were considered and rejected: 120 horses would give 480 small buttons. | One week tool. The row stays quiet: **Done** marks it; a tap on the horse says how it went. |

A second critique runs on the real screens after the build (section 9).

## 8. Build list

One pull request. Behind `TRAINING_ENABLED`, as all of Training.

| Part | Where |
|---|---|
| Data | `training/models.py`, migration `0011` |
| Record sheet | `training/forms.py`, `templates/training/partials/_activity_fields.html` |
| Board | `training/views.py`, `templates/training/board.html`, `services.unmark_done` |
| Profile and notes | `training/profile.py`, `templates/training/horse_profile.html`, note views and forms |
| Plan tab | `training/plan.py`, `templates/training/plan.html`, `_tabs.html` |
| Share | `training/share.py`, `static/js/share_text.js`, `board_sheet.html` |
| Other screens | `_horse_card.html`, `log.html` (rating, CSV), Overview (day note, links) |
| Styles | `static/css/input.css`, built to `styles.css` |
| Help | `templates/help/articles/training.html` |
| Tests | `training/test_redesign.py`, `static/js/tests/share_text.test.js` |

Checks before the push: `ruff check .`, `makemigrations --check`, `manage.py check`, the test suite, the JavaScript tests, and each changed screen at 1280 and 375 wide.

## 9. What is built

Everything in section 5 is built, in one pull request, behind `TRAINING_ENABLED`.

| Part | Where |
|---|---|
| Data: `Activity.rating`, `Activity.instructions`, `TrainingNote`, `DayNote`. | `training/models.py`, migration `0011_board_redesign.py` |
| The record sheet: three layouts, Status and the reasons as buttons, How it went, Notes. | `templates/training/partials/_activity_fields.html`, `_activity_status.html`, `_activity_result.html`, `training/forms.py` |
| The board: Done on a row, Undo done, Did not work in the menu, the work-list line, the count in the lot header. | `templates/training/board.html`, `services.unmark_done`, `views.activity_mark` |
| The training profile and notes about a horse. | `training/profile.py`, `templates/training/horse_profile.html`, the note views in `training/views.py` |
| The Plan tab. | `training/plan.py`, `templates/training/plan.html` |
| Share, the note for the day, the sheets. | `training/share.py`, `static/js/share_text.js`, `services.set_day_note`, `board_sheet.html` |
| The horse page card, the Log and its CSV, the Overview links, bulk add. | `_horse_card.html`, `log.html`, `overview.py`, `bulk.py` |
| The help guide. | `templates/help/articles/training.html` |
| Tests: 43, and 6 for the script. | `training/test_redesign.py`, `static/js/tests/share_text.test.js` |

Screens of the build are in `docs/training-redesign/`: `board-phone.png`, `sheet-phone.png`, `plan-desktop.png`, `profile-desktop.png`.

### 9.1 Departures from the plan

| # | Change | Why |
|---|---|---|
| X1 | The profile address is `/training/horse/<id>/`, not `/training/horses/<id>/`. | The sidebar marks **Horses** for an address with `/horses/` in it. This page belongs to Training. |
| X2 | The note for the day shows under the date, as text. It is not a card. | A card used 78 px above the first lot on a phone. |
| X3 | On a board row, the instruction comes before the gear. After the work, the row shows how it went and the note, and not the gear. | On a phone the gear pushed the instruction off the row. After the work, nobody needs the gear. |
| X4 | "Your horses today" is now "Your horses". | It shares a phone row with the Rider filter and **Share**. |
| X5 | On a phone, the Plan tab shows a past day, and a day with no lots, as one line. | The plan is for today and the days after. |
| X6 | The lot header counts only when a horse is Done ("1 of 7 done"). | "0 of 7 done" for a lot with one horse that did not work said nothing. |

### 9.2 The second critique, on the real screens

Checked in a browser at 375 and 1280 wide, with the sample yard. A script did the main flow on a phone screen: **Done** on one horse, the sheet with Good and a note, **Did not work** in the menu, **Undo done**, and **Copy the lots as text**. The flow gave no script error.

| Point | Score | Evidence |
|---|---|---|
| Direction held | 8 | One row style on every lot. A rating is a word. No new colour. |
| Hierarchy | 8 | On a 375 × 667 phone the first horse row starts at 302 px when the day has no note: 6 rows show (principle 5.8). A note for the day of two lines shows 5 rows. The trainer chooses to write the note, and riders need it first. |
| Detail | 7 | Six faults found and fixed in the build: a hidden text box that showed; a Done button 44 px high on a computer; "0 of 7 done"; tabs cut on a phone; the gear pushed the instruction off the row; the sheet sent the rating two times. Not fixed: on a short phone the list of reasons can go under the bottom bar, as the old list did. Scroll the page to see the last reasons. |
| Function | 8 | One horse done: 1 tap. Done with how it went and a note: 3 taps and the text. The lots to the team: 2 taps. A horse's whole history with notes: 1 tap from the board menu, the Overview, the Lots tab or the Log. |
| Restraint | 8 | No week strip. No rating buttons on the rows. One new tab. |

## 10. Not in this plan

- Messages inside the app to each rider ("your horses tomorrow").
- Start many days in one step from the Plan tab.
- Drag a horse between lots (module plan, D10).
- An attention item from ratings (R8).
- Offline use at the yard (module plan, D9).
