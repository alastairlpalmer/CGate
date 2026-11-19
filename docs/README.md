# Documentation

## Security and operations

| Document | What it covers |
|---|---|
| [`LAUNCH_READINESS_PLAN.md`](LAUNCH_READINESS_PLAN.md) | Every launch requirement checked against the code, with a phased plan for the gaps. |
| [`GO_LIVE_CHECKLIST.md`](GO_LIVE_CHECKLIST.md) | **Start here.** The dashboard work that is left, in the order to do it, with time estimates. |
| [`../SECURITY.md`](../SECURITY.md) | How to report a vulnerability, the controls in place, and the known gaps. |
| [`REVIEWER_PACK.md`](REVIEWER_PACK.md) | Orientation for an external code or security reviewer: architecture, data map, threat list. |
| [`DATA_RETENTION.md`](DATA_RETENTION.md) | What personal data is kept, for how long, and how to answer access and erasure requests. |
| [`BACKUP_RESTORE.md`](BACKUP_RESTORE.md) | What to back up, how, and how to prove a restore works. |
| [`CUTOVER.md`](CUTOVER.md) | The Vercel → Railway migration runbook. |

## Design and planning

| Document | What it covers |
|---|---|
| [`CODEBASE_REVIEW.md`](CODEBASE_REVIEW.md) | Full review of the codebase, numbered by part. Several tests cite it. |
| [`LOCATION_MAPPING_PLAN.md`](LOCATION_MAPPING_PLAN.md) | Design for coordinates, field boundaries and the map tab. |
| [`BARNS_AND_STABLES_PLAN.md`](BARNS_AND_STABLES_PLAN.md) | Phased plan for barns, numbered stables and day turnout. |
| [`GOOGLE_SIGN_IN_PLAN.md`](GOOGLE_SIGN_IN_PLAN.md) | Review of the sign-in and invitation code, and a phased plan for sign-in with Google. |
| [`DASHBOARD_REDESIGN_PLAN.md`](DASHBOARD_REDESIGN_PLAN.md) | Dashboard redesign. |
| [`POPUP_EDIT_PLAN.md`](POPUP_EDIT_PLAN.md) | Popup editing. |
| [`FARRIER_PAGE_PLAN.md`](FARRIER_PAGE_PLAN.md) | Farrier page test (Option A): our farrier records visits on a phone; charges go on owners' monthly invoices; the yard pays the farrier monthly. |
| [`C_VET_PLAN.md`](C_VET_PLAN.md) | C-vet, the vet's app built on C-gate: the two products, who owns which data, the offline rules, the phases, and what is built: the offline app (phase 1), the ownership seam that keeps clinical notes with the practice (phase 2), the API and sync (phase 3), the screens for a visit (phase 4), spoken notes with the yard as the practice's client (phase 5), the bill to the yard with charges and dates back into C-gate (phase 6), and the review of the screens. |
| [`VET_TRIAL_PLAN.md`](VET_TRIAL_PLAN.md) | Vet trial with Tim, after the farrier test: horse list and treatment screen, voice notes typed by the app, no-signal mode, invoices in the practice's name, records back to Yardway. |
| [`HORSE_IDENTITY_AND_PEDIGREE_PLAN.md`](HORSE_IDENTITY_AND_PEDIGREE_PLAN.md) | Identity and pedigree on the horse page: microchip, UELN and passport checks, the parents and grandparents tree, and how to get the data in from Weatherbys or the passport. |
| [`UX_REVIEW_PLAN.md`](UX_REVIEW_PLAN.md) | UX and visual review (Nov 2026): findings and a phased plan for hierarchy, consistency and phone lists. |
| [`UI_AUDIT.md`](UI_AUDIT.md) | Interface audit. |

## QA history

Point-in-time records. They cite the scripts in `horse_management/tools/qa/`.

- [`QA_REPORT.md`](QA_REPORT.md)
- [`FULL_QA_REPORT.md`](FULL_QA_REPORT.md)
- [`MOBILE_QA_REPORT.md`](MOBILE_QA_REPORT.md)
- [`REMINDERS_QA_REPORT.md`](REMINDERS_QA_REPORT.md)
- [`BUGFIXES.md`](BUGFIXES.md)
