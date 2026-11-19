# CTO proposal: build plan for fastest revenue at lowest cost

Date: 19 November 2026. "pw" = person-weeks of a founder or developer working with AI coding agents. All efforts are my estimates.

## Summary

The yard product is deep and well tested: 69 models, about 90,000 lines of Python, about 180 test files, audit trail, MFA, GDPR tools, encrypted backups, Xero sync. Nothing outside the yard is built. There is no tenant key on any table, no sign-up, no Stripe, no WhatsApp, no owner logins, no racing features and no AI. The fastest revenue does not need multi-tenancy. It needs **separate hosted installs for the first 5–8 warm-intro customers** (from January 2027), **Stripe Connect pay-now links**, and a **cheap owner-update page** (email plus photos, WhatsApp only as a nudge). Multi-tenancy (shared schema, organisation key, Postgres row-level security as a backstop) follows in Q2 2027, then self-serve sign-up and AI import. **Do not build vet practice software in the next 18 months.** Sell C-vet as a visit and yard layer next to the practice's own system, and add a Provet or ezyVet link only when three paying practices ask for it. The plan is about 90 pw over 18 months. That fits the founder plus one developer from month 3, with about 30% left over for bugs and support.

## What is really built (from the code)

| Area | State | Gap to sell |
|---|---|---|
| Yard core, invoicing, Xero, health, breeding, fields, stables | Built and tested | None for a single yard |
| Tenancy | None. `BusinessSettings` is a singleton. 30 unique constraints are global. 868 ORM calls outside tests | Large. Every model and query needs to know its organisation |
| C-vet | Built (phases 1–6). It already has its own `Practice` scoping (`for_practice`) | Practice-to-yard link is one-to-one ("gets a yard key when there are many yards") |
| Owner access | Emails only (invoices, statements, health digests). Owners have no login | Owner page needed |
| Payments | A static `card_payment_url` field | Stripe Connect |
| Racing | Nothing (one "racing yard" client type in C-vet) | Light racing module |
| Import | A one-off CSV script for the first yard | Import tool for any customer |
| Hosting | Railway (web, worker, beat) and Supabase via pgbouncer in transaction mode | A per-install script; a staging environment |

## 1. Build order, December 2026 to May 2028

| # | When | Work | pw | Revenue it unlocks |
|---|---|---|---|---|
| 1 | Dec–Jan | Go-live leftovers: staging, uptime alerts, a tested restore, legal pages, CI on Postgres. A **one-command install** (own database, bucket and domain per customer). Bill customers by hand with Stripe invoices | 5 | Set-up fees plus core subscriptions from warm-intro studs, livery yards and trainers. **First cash in Jan–Feb 2027** |
| 2 | Jan–Feb | **Stripe Connect pay-now links** (card, Bacs, Pay by Bank) with an application fee, built as Connect from day one so each install earns the fee | 4 | About £320 per yard per year in fees (payments plan). Also a strong reason to buy |
| 3 | Feb–Apr | **Owner updates v1**: an owner page reached by a magic link, phone photo and note posting, weekly email digest, an AI-drafted monthly report | 6 | Owner updates add-on (£2 per horse). Matters most to trainers and studs. **From April 2027** |
| 4 | Mar–Apr | **Racing light**: partnership and syndicate groups (built on fractional ownership), a daily work and exercise board, "next run" and race-result notes typed in by staff | 4 | Race training add-on (£2 per horse) |
| 5 | Apr–Jul | **Multi-tenancy**: organisation model, scoped managers, row-level security, cross-tenant tests, per-tenant numbering and settings, support impersonation with an audit record, move the hosted installs onto it | 12 | Lets us sell without paying per install. Hosting cost per customer falls |
| 6 | Jul–Aug | **Self-serve sign-up and Stripe Billing** (per-horse metering, modules, trial) | 5 | Livery yards at scale (via LiveryList and the NTF). **From August 2027** |
| 7 | Aug–Sep | **AI-assisted import** from Yardman, miStable and spreadsheet exports | 4 | Shorter sales cycle. Not paid for directly |
| 8 | Sep–Oct | Direct Debit auto-collection, WhatsApp nudges, external penetration test | 6 | Higher payments revenue; lower churn |
| 9 | Oct–Dec | **C-vet for other practices**: many yards per practice, practice sign-up, a free farrier app | 8 | C-vet subscriptions; farriers as a channel into yards |
| 10 | Jan–May 2028 | Provet or ezyVet link (only if three paying practices ask), stud extras (nominations, foal registration export), AI invoice checks | 12–16 | Vet expansion; stud add-on depth |
| | | **Total** | **~70 plus 20 buffer** | |

Capacity is my estimate. The founder with agents gives about 0.6 pw per week. A developer from March 2027 (about £70k a year loaded, the model's figure) gives about 0.9 pw per week. That is about 105 pw available. Do not hire the second developer the model plans for until monthly recurring revenue passes about £15k.

## 2. Minimum product for each wedge segment (what to cut)

- **Racing trainers:** horses, owners and syndicates, health, a daily work board, owner updates. Cut: training-fee billing (Weatherbys already does it), automatic entries and declarations from BHA data (data licence cost and risk), gallop GPS, fields.
- **Studs:** the breeding work already built (covers, scans, foaling, EHV reminders), invoicing, owner updates for mare owners. Cut until year 2: stallion nomination contracts, sales-prep workflow.
- **Livery:** the core, invoicing, pay-now links, the owner page, fields and stables (built already). Cut: marketplace, feed ordering, a traceability ledger.
- **Everyone:** no native app store apps. The installable web app is enough. No public API until a customer pays for it.

## 3. Multi-tenancy: recommendation

| Option | Cost | Verdict |
|---|---|---|
| Separate hosted installs | About 1 pw for the script; about £40–70 per month per install (estimate). Each upgrade runs N times | **Use now for the first 5–8 customers** with a set-up fee. Stop at 10 installs |
| Schema per tenant (django-tenants) | 8–10 pw | **No.** It sets `search_path` on each connection, which is fragile behind Supabase's transaction-mode pooler. Migrations run once per tenant. Celery tasks must switch schema |
| **Shared schema with an `organisation` key, plus Postgres row-level security** | 12 pw | **Recommended.** One database, one migration run. Row-level security ([Postgres docs](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)) blocks a missed filter. Set the tenant per transaction (`SET LOCAL`), which works with transaction pooling |

Risks: (1) A missed filter leaks data. Mitigate with row-level security, a test that every model has the key, and cross-tenant read tests on every view. (2) Celery tasks and emails run without a request. Pass the organisation into every task. (3) Global uniques (invoice numbers, names) must become per-tenant. (4) A noisy tenant slows the others. Add per-tenant rate limits.

## 4. Vet practice software

| Path | Cost and time | Realistic? |
|---|---|---|
| Deepen C-vet as the visit and yard layer | 8 pw (about £15k), 3 months | **Yes.** About 70% is built. It sits beside the practice's own system, as Tim uses it |
| Integrate with Provet Cloud or ezyVet | 6–10 pw each plus 3–6 months of partner approval. Both gate API access to approved partners ([Provet](https://provet.cloud/integration-partners), [ezyVet](https://developers.ezyvet.com/apply.html)) | **Yes, later**, once paying practices ask |
| Full practice system (diary, clinical records, medicines and controlled drugs register, labs, insurance claims, stock, data migration) | 150–250 pw, £500–650k, 2–3 years to be credible against Provet and ezyVet | **No.** The model shows about £630k cost against about £66k revenue in 2031. Revisit only after a practice group partner funds it |

## 5. Owner updates: cheapest reliable design

1. **The owner page is the product.** A signed magic link, no password. It shows a timeline per horse with photos, notes, health and invoices. Photos already go to S3.
2. **A weekly email digest** carries the updates. Cost is under £0.001 per email (estimate).
3. **WhatsApp only as a nudge**: one utility message, "New update on Jasper", linking to the page, and only for owners who opt in. Meta charges per message ([Meta pricing](https://developers.facebook.com/docs/whatsapp/pricing)). Third parties report that the free utility messages inside the 24-hour window ended on 1 October 2026 ([WATI](https://support.wati.io/en/articles/11561662-message-based-pricing-all-you-need-to-know)). Budget £0.02–0.04 per message.
4. Message cost for a 40-horse race yard (estimate): 4 people per horse × 2 nudges a month = 320 messages, about £6–13 a month, against £80 of add-on revenue. Cap nudges at one a day per owner.

## 6. AI in the product

| Feature | Build | Will customers pay? |
|---|---|---|
| Photo and voice note to an owner update (the phone dictates, AI tidies the text and tags the horse) | 2 pw | **Yes, inside the owner updates add-on.** Trainers' biggest chore |
| Auto-written monthly owner report | 1 pw | **Yes**, same add-on. Studs and trainers |
| AI import mapping from competitor exports | 3 pw | Not directly. It wins deals and cuts onboarding cost |
| Invoice checks (missed farrier or vet charges, odd rates) | 2 pw | **Probably.** It finds money for livery yards. Sell it in the core as the reason to upgrade |
| Voice to clinical note in C-vet | 2 pw | Vets will pay, but only after the trial proves it. Keep audio on the phone as now (C-vet decision 19) |

AI model cost is pennies per report (estimate; check [Anthropic pricing](https://www.anthropic.com/pricing)). Price it in the add-ons, not as a separate meter.

## 7. Technical risks and cheap controls

| Risk | Control | Cost |
|---|---|---|
| Data leak between tenants | Row-level security, cross-tenant tests in CI, external penetration test before self-serve | About £5–8k test |
| Personal data (UK GDPR) | GDPR export and erasure are built. Add a data processing agreement and a sub-processor list. Keep data in UK or EU regions. Register with the [ICO](https://ico.org.uk/for-organisations/data-protection-fee/) | About £1–2k legal |
| Vet records | The practice owns clinical records. Keep them separate from the yard, as built. Follow the [RCVS guidance on records](https://www.rcvs.org.uk/setting-standards/advice-and-guidance/code-of-professional-conduct-for-veterinary-surgeons/supporting-guidance/clinical-and-client-records/) | In design |
| Downtime or data loss | Uptime alerts, a monthly restore test, point-in-time recovery, a status page | About £50 a month |
| Payments | Stripe holds the money and does the checks on each yard, so Cgate stays outside FCA rules. Check webhook signatures. Use idempotency keys | In build |
| One-person dependency | Branch protection, a paid part-time reviewer, runbooks | About £1k a month |

Bottom line: sell hosted installs now, build Connect and owner updates next, then multi-tenancy. Keep vet work as a companion layer.
