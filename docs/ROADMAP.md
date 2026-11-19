# C-gate product roadmap

Date: 18 November 2026. Owner: product. Review: each month.

## How to read this document

- This roadmap lists **product features**. The launch and security work is in [`LAUNCH_READINESS_PLAN.md`](LAUNCH_READINESS_PLAN.md) (P0 to P3). This roadmap refers to those items. It does not repeat them.
- Many items come from the review of Prism, the market leader in Australia: [`market/prism-competitor-review.md`](market/prism-competitor-review.md). The column "From Prism" shows the source.
- Sizes are for one developer. One "week" is about five working days.
- Each phase leaves the app working for our own yard.

## The idea in one paragraph

Prism won Australia with a loop. The yard sends updates to owners. Owners get a free app. Owners with horses at other yards invite those yards. Finance and payments then make the product hard to leave. C-gate already has strong finance. The gap is the **owner side**. This roadmap builds the owner side first, then payments, then the multi-yard network.

## Summary

| Phase | Goal | Needs multi-tenancy? | Size |
|---|---|---|---|
| 0 | Module boundaries: clean lines in the code, per-yard module switches, separate interfaces, editions by yard type ([`MODULES_PLAN.md`](MODULES_PLAN.md)) | No | 6–7 weeks with parallel agents; runs beside phase 1 |
| 1 | Faster yard office: charge rules, invoice run checks, QR cards, welfare flags, grazing management, offline mode | No | 9–11 weeks |
| 2 | Owner updates and owner portal, with WhatsApp and SMS | No (one yard) | 8–10 weeks |
| 3 | Owner payments | Phases 1–2 of the payments plan: no. Connect fees: yes | 7 weeks, then 3 weeks after P2 |
| R | Racing yards and owner managers: training fees, trackwork, race entries and results, race reports, syndicates, prize money, medication records | Pilot with one trainer: no. To sell: yes | 7–9 weeks with parallel agents |
| 4 | SaaS and the growth loop: many yards, free tier, owners across yards, import | Yes ([`MULTI_TENANCY_PLAN.md`](MULTI_TENANCY_PLAN.md)) | 7–10 weeks with parallel agents for multi-tenancy, then 4–6 weeks for the growth-loop features |
| 5 | Later: staff tasks and rota, yard websites, data feeds, native apps, Australia | Yes | Not sized |

---

## Phase 0 — Module boundaries (6–7 weeks, beside phase 1)

Goal: C-gate can serve different yard types (grass livery, full livery, stud, racing) and different people (staff, owners, suppliers) without separate copies of the code. The full plan is [`MODULES_PLAN.md`](MODULES_PLAN.md).

Why first: half the code is in `core`, and 18 files in `core` import the modules above it. The multi-tenancy build gives one agent per app, so clean lines make that build faster and safer. The owner portal and payments are new code, so they must start inside the new lines.

| # | Work | From Prism | Size |
|---|---|---|---|
| 0.1 | **Module map and checks.** Agree the modules. `import-linter` in report-only mode. | — | 2–3 days |
| 0.2 | **Dashboard out of `core`.** Modules register their dashboard widgets and export parts. | — | 1 week |
| 0.3 | **Split `core`** into base, places and maps, stables and turnout. Database tables stay where they are. | — | 2 weeks |
| 0.4 | **Module registry and per-yard switches.** `features.py` built from the registry. | Modules sold separately | 1 week |
| 0.5 | **Separate interfaces:** yard office, owner portal, supplier portal, platform admin. `import-linter` blocks PRs. | Separate trainer, owner and supplier logins | 1–1.5 weeks |
| 0.6 | **Editions** by yard type: preset modules and words | Solutions for trainers, syndicators, agistment, studs | 3–4 days |

Order with the other plans: steps 0.1–0.3, then multi-tenancy wave 0, then steps 0.4–0.6, then multi-tenancy waves 1–5. Build charge rules (1.1) with the event pattern from the modules plan.

---

## Phase 1 — Faster yard office (9–11 weeks)

Goal: the monthly invoice run takes less time and has fewer errors. Yard staff find horses faster and can work in fields with no signal. Grass livery yards manage their land in C-gate.

| # | Feature | From Prism | C-gate today | Build | Size |
|---|---|---|---|---|---|
| 1.1 | **Charge rules** | Rules and presets: procedure, transport, farrier, dentist events make charges | Health records make charges. Bulk charges. "Add margin". No editable rules. | A rules table: event type → service item, price or margin, who pays (owner shares or yard). Apply when a vet, farrier, worming, dentist or transport record is saved. Show the charge for review. | 1.5 weeks |
| 1.2 | **Invoice run checks** | 11-step statement wizard with checks | Monthly pro-rata run, credit notes, reconciliation report | A check page before the run: horses with no placement or rate; horses with no owner shares, or shares that do not add to 100%; unbilled charges to confirm; owners with no email; missing fields. Each line links to the fix. | 1.5 weeks |
| 1.3 | **Group charges by filter** | Group expense by location, status, arrival | Bulk charges by selected horses | Add filters: by site or field, by status, by "arrived in period". | 3 days |
| 1.4 | **QR cards** | Print QR cards for barns, boxes, horses; scan on tablet | Barns and stables. No QR. | Print A4 or label sheets of QR codes per stable, field and horse. Scan with the phone camera to open the record. No app needed. | 1 week |
| 1.5 | **Find by microchip** | Bluetooth chip scanner; Stud Book lookup | Microchip field on the horse | Search by microchip number. Accept input from a Bluetooth scanner that types as a keyboard. Check the 15-digit format. | 2 days |
| 1.6 | **Welfare flags** | Welfare flags with escalation | Dashboard attention list | Any staff member flags a horse with a note and a level. The flag shows at the top of the dashboard and on the horse page. Email to the manager for an urgent flag. Clear with a note. Kept in the audit log. | 1 week |
| 1.7 | **Grazing management** | Agistment and farm management | Field maps, Land App and RPA boundaries, field usage periods, worm egg counts | Field rest and rotation plan. Grass cover. Jobs per field: poo-picking, topping, harrowing, fertiliser, ragwort. Worm egg counts by field. Horses per acre with a warning when a field is over its limit. This is the main reason to choose C-gate for grass livery. | 2–3 weeks |
| 1.8 | **Offline mode for staff** | Prism Plus tablet app | C-vet works offline (phase 1 of the C-vet plan) | Reuse the C-vet offline pattern for yard staff: horse list, checks, feeds, health records and welfare flags work with no signal and sync later. Show what is waiting to sync. Racing trackwork (R.3) uses the same pattern. | 2 weeks |

Done when: the yard owner runs one month with the checks and the rules, and the invoice error count goes down against the month before.

---

## Phase 2 — Owner updates and owner portal (8–10 weeks)

Goal: owners see their horses and get updates without a phone call. This is the Prism hook.

Depends on: Launch plan P2 item 2 (**per-object access checks**). An owner must see only their own horses. Build that item first, inside this phase.

| # | Feature | From Prism | C-gate today | Build | Size |
|---|---|---|---|---|---|
| 2.1 | **Per-object access** | Owner permissions per horse | Roles for staff only | Owner user type. Every owner view filters by `OwnershipShare`. Tests that try to read another owner's horse. | 1.5 weeks |
| 2.2 | **Owner sign-in** | Owner login by email | Staff sign-in, MFA, Google sign-in, invites | Invite an owner from the owner page. Magic link or password. Owner MFA optional. | 1 week |
| 2.3 | **Yard updates** | Trainer updates with photo, video, audio, reports; 151m sent | Horse photos | Staff post an update on a horse: text and photos, from a phone. Choose "send to owners". One update can go to many horses (for example "all horses in Top Field wormed today"). | 1.5 weeks |
| 2.4 | **Owner portal** | Owner portal: notifications, horse, comms | None | One shared address for all owners (for example `my.<domain>`). The owner account sits above yards from the start, so item 4.2 needs no rebuild. Phone-first pages: my horses, where each horse is (site, field, stable), next vaccination, farrier and worming dates, updates feed, invoices and statements, documents (passport, insurance). | 2 weeks |
| 2.5 | **Notification choices** | Each notification type by web, push or email | Owner email preferences | Per type: update, health due, location change, invoice issued, payment received. Per channel: email, web push (PWA). | 1 week |
| 2.6 | **Owner requests** | Add horse request to trainer | None | Owner asks for a service from the portal (for example "extra farrier visit", "bring in"). The request goes to the staff task list and can make a charge. | 1 week |
| 2.7 | **WhatsApp and SMS** | Owner notifications by web, push or email | Email only | Add WhatsApp (WhatsApp Business Platform) and SMS as channels for owner updates, reminders and invoice notices. The owner chooses the channel (2.5). Each message has a link to the owner portal. Check the cost per message and the template approval rules first. | 2 weeks |

Done when: 80% of owners at our yard have signed in once, and phone calls about "where is my horse" go down.

---

## Phase 3 — Owner payments (7 weeks, then 3 weeks after P2)

Goal: owners pay inside C-gate. The yard collects money faster. C-gate earns a fee on each payment.

The full design is in [`payments/payments-plan.md`](payments/payments-plan.md). This phase follows that plan.

| # | Feature | From Prism | Build | Size |
|---|---|---|---|---|
| 3.1 | **Pay-now link** on invoices, statements and the owner portal | "Pay here" button on statements | Payments plan phase 1 | 3 weeks |
| 3.2 | **Direct Debit** mandates and auto-collection on the due date | Direct debit on the due date | Payments plan phase 2 | 4 weeks |
| 3.3 | **Stripe Connect** for each yard, with C-gate's fee | Prism Pay | Payments plan phase 3. Needs Launch plan P2. | 3 weeks |
| 3.4 | **Xero reconciliation** of Stripe payments | Reconcile to Prism and Xero | Extend the current Xero sync | In 3.1–3.3 |

---

## Phase R — Racing yards and owner managers (7–9 weeks)

Goal: sell C-gate to racing trainers and to the people who manage racing syndicates and clubs. This is Prism's home market, so C-gate must match its core and win on owner experience and billing. Scope is in [`MODULES_PLAN.md`](MODULES_PLAN.md) section 6a.

Depends on: phase 0 steps 0.1–0.5 (module lines), phase 2 (owner updates and portal). To sell to more than one trainer, also multi-tenancy (phase 4.1).

Market: about 600 licensed trainers and 14,555 horses in training in the UK (BHA, September 2024). Assumed revenue per account: £1,200 per year. Add racing and syndicates to [`market/uk-market-sizing.md`](market/uk-market-sizing.md) before you set prices.

| # | Feature | From Prism | C-gate today | Build | Size |
|---|---|---|---|---|---|
| R.1 | **Racing edition** | Trainer solution | Stables, placements, day rates | Preset modules and words: box, lot, training fees, spelling | 3 days |
| R.2 | **Horses in training** | Horse status and location | Horse status, stables | Statuses: in training, spelling, injured, retired. Move between yard and spelling farm. | 3 days |
| R.3 | **Trackwork** | Trackwork and schedules | None | Daily lot list on a phone: rider, work, notes. Optional "share with owners". | 1.5 weeks |
| R.4 | **Race entries and results** | Racing Australia feed: nominations, acceptances, results | None | Read-only feed for the yard's horses from a Weatherbys data licence. Manual entry if the licence is not ready. Owner notifications for entry, declaration, non-runner, result. | 2 weeks, after the licence |
| R.5 | **Race reports** | Pre-race and post-race reports, media | Owner updates (phase 2) | Report types on owner updates. Video links. | 3 days |
| R.6 | **Syndicates (owner manager)** | Syndicator portal, one-click forwarding, large owner groups | `OwnershipShare` | Ownership groups with a member register. Forward trainer updates to all members. Manager login across yards. | 2 weeks |
| R.7 | **Prize money and distributions** | Finance module, owner splits | Split charges, credit notes | Record prize money. Take trainer and jockey percentages. Split the rest by share. Cash calls in advance. | 1.5 weeks |
| R.8 | **Member and owner statements** | Syndicator statements | Invoices, statements | Monthly statement per member: training fees, extras, prize money, cash calls | 1 week |
| R.9 | **Pilot trainer** | Built with leading trainers | — | One small or mid-size trainer and one syndicate manager. Weekly feedback for three months. | Non-code |
| R.10 | **Medication records** | Treatments and procedures | Health records: vet visits, conditions, worming | A medication record per horse: drug, dose, route, who gave it, batch number, withdrawal time and the first date the horse can race or compete. Warn when a horse is entered or declared inside a withdrawal time. Export for a racing or competition inspection. Also useful for competition horses at livery yards. | 1.5 weeks |

Not in scope: making entries and declarations inside C-gate. They stay in Weatherbys' own system unless an API allows them. No betting or form tools.

---

## Phase 4 — SaaS and the growth loop (about 3–4 months)

Goal: many yards on one platform. Owners bring in new yards. This is how Prism grew.

Depends on: the multi-tenancy build in [`MULTI_TENANCY_PLAN.md`](MULTI_TENANCY_PLAN.md) (organisation model, tenant isolation, Stripe Billing, support login). That plan also covers how to run the platform after the build. Wave 0 of it (the organisation key, about 1 week) can start now.

| # | Feature | From Prism | Build | Size |
|---|---|---|---|---|
| 4.1 | **Multi-tenancy** | One platform for all stables | [`MULTI_TENANCY_PLAN.md`](MULTI_TENANCY_PLAN.md) waves 0–5, plus Launch plan P2 items 7–10 and 12 | 7–10 weeks elapsed with parallel agents; 19–23 weeks for one developer |
| 4.2 | **One owner, many yards** | Owner sees all horses, at all trainers | The owner account sits above yards. The portal shows horses from each yard that the owner uses. Each yard sees only its own data. | 3 weeks |
| 4.3 | **Owner invites yard** | Owner invites a trainer who is not on Prism | From the portal, the owner adds "my other yard". C-gate emails that yard an invitation with a free trial. Track invites as a growth number. | 1 week |
| 4.4 | **Free starter tier** | Free Starter account | Free for 1–5 horses: horses, health reminders, owner updates. Paid tiers add invoicing, Xero, payments, maps. | 1 week after Stripe Billing |
| 4.5 | **Modular pricing by horse count** | $30 / $90 / $125 per module by horse count | Keep the three tiers in the market sizing (£29, £49, £89). Add paid add-ons: payments, breeding, maps, C-vet link. | In Stripe Billing work |
| 4.6 | **Fast onboarding import** | Profiles already built from Racing Australia | Import wizard: CSV and Excel from spreadsheets and from other UK products. Map columns. Dry run. Undo. | 3 weeks |
| 4.7 | **Supplier logins** | Multi-login for suppliers | One login type for farriers, dentists, physios and vets across many yards. Reuse the farrier page and C-vet. | 3 weeks |
| 4.8 | **Reference yards** | Built with leading trainers | Six pilot yards, chosen with care: one stud, one large full livery, one multi-site grazing, two DIY, and the racing pilot trainer from R.9. Weekly feedback for three months. | Non-code |

---

## Phase 5 — Later (not sized)

| # | Feature | From Prism | Note |
|---|---|---|---|
| 5.1 | **Staff tasks and rota** | Tasks, timesheets, rostering, auto scheduling | Daily yard jobs from turnout, feeds and health due dates. Tick off on a phone. Rota by person. Timesheets export for payroll. |
| 5.2 | **Yard board mode** | Prism Plus Tablet | A tablet view for the tack room: today's jobs, flags, who is in which stable. Use with QR cards (1.4). |
| 5.3 | **Yard websites** | Website builder, horses for sale | Reuse the Astro `website/` as templates. Show live livery vacancies and horses for sale from C-gate. Leads go into C-gate. |
| 5.4 | **UK data feeds** | Racing Australia daily file, Stud Book lookup | Weatherbys racing data comes forward to R.4. Later: Weatherbys stud book data for studs, and Equine Register (ChipChecker, Digital Stable) for horse identity. Check API access, terms and cost before you commit. |
| 5.5 | **Native apps** | Three iOS and Android apps | Only if the PWA blocks something (for example push on old iPhones or Bluetooth scanners). Start with a wrapper around the PWA. |
| 5.6 | **QuickBooks and Sage** | MYOB | The second accounting link for the UK. |
| 5.7 | **Australia** | Prism owns racing | Agistment and breaking farms are the entry point, not racing. Needs AUD, GST, MYOB, Racing Australia or Stud Book data. Prism already sells an agistment module, so check the gap first. |

## Not on the roadmap

- **Racing entries and declarations made inside C-gate.** They stay in Weatherbys' own system unless an API allows them. Phase R shows them read-only.
- **Betting and form tools.**
- **Lesson and arena bookings.** Riding schools are a weak fit.

## Decisions made

Made on 18 November 2026:

- **Racing yards and owner managers are in the product** (phase R).
- **Split `health` into care and breeding later**, not now.
- **One shared owner portal address** for all yards, with the owner account above yards from the start.

## Decisions needed from you

0. **Weatherbys data licence.** Contact Weatherbys for terms and cost before phase R starts. Item R.4 depends on it.

1. **Phase order.** This plan builds owner updates (phase 2) before payments (phase 3). Prism's lesson is that updates bring owners in, and owners are the growth channel. Payments can go first if cash collection is the bigger pain at our yard.
2. **Free tier.** Do we give a free tier for small yards and home owners (4.4)? It is the cheapest lead source, but it adds support load.
3. **Multi-tenancy date.** Recommendation: do wave 0 of [`MULTI_TENANCY_PLAN.md`](MULTI_TENANCY_PLAN.md) now. Start the full build when Launch plan P1 is done and 3–5 yards have agreed to pay. Until then, give each new yard its own deployment.
