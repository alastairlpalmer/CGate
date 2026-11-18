# Prism: competitor review

Date: 18 November 2026. Product: Prism ([prism.horse](https://www.prism.horse/about)), Prism Pay Pty Ltd, South Melbourne, Australia.

## Summary

- Prism is the main horse management platform for Australian **racing trainers**. It also sells to syndicators, studs, agistment (livery) farms, breakers and owners.
- Prism says **75% of Australia's top thoroughbred trainers** use it. This is a vendor claim. We could not verify it.
- Prism did not win on features alone. It won because of **five moves**:
  1. **Data was already there.** Prism loads a daily file from Racing Australia. Every trainer and horse in Australia has a profile before the trainer signs up.
  2. **Owners pull trainers in.** The owner app shows all of an owner's horses, at every trainer. An owner can invite a trainer who is not on Prism.
  3. **Owner updates are the hook.** Trainers send photos, video and race reports to owners. Prism says it has sent 151 million owner updates.
  4. **Money flows through it.** Daily rates and charge rules make the bills. Prism syncs to Xero and MYOB and takes payments.
  5. **Built with leading trainers.** Prism designed the workflow with top stables, for example Freedman Racing. Small stables then follow the big names.
- For C-gate, moves 2, 3 and 4 copy directly to UK livery yards and studs. Move 1 needs a UK data source. Move 5 is our pilot yard.
- See [`../ROADMAP.md`](../ROADMAP.md) for the plan that comes from this review.

## How we did this review

The session network blocks `prism.horse`, `apps.apple.com` and `apps.xero.com`. We used:

- Web search results that quote Prism's site, help centre and app store pages.
- Four of Prism's public PDF manuals on Amazon S3: Owner Portal, Registration and Set Up, Finance, Staff.

Most facts below come from Prism itself. Treat them as vendor claims. Some sources are old (see "Confidence").

## 1. Who Prism sells to

| Segment | What Prism gives them |
|---|---|
| Trainers | Stable management: horses, staff, treatments, trackwork, transport, nominations and acceptances, finance, owner updates |
| Syndicators | A portal to manage large owner groups. They post their own updates and forward trainer updates to owners in one click. Syndicator statements. |
| Agistment, breaking, farm | An "Agistment + Farm Management" module: owners, horses, accounts, staff, suppliers |
| Studs | Breeding, pregnancy and foaling, pedigree |
| Owners | A free owner app: all horses in one place, at all trainers. Feed of updates. Pay bills. |
| Suppliers | Separate logins with permissions (vets, farriers, transport) |

## 2. Products and apps

| Product | What it is |
|---|---|
| Prism web portal | The main system for the stable office |
| Prism – Horse Management (iOS, Android) | Mobile app for trainers and staff |
| Prism Horse Owners (iOS, Android) | Owner app. Chronological feed, follow horses at non-Prism trainers, merge accounts, archive horses, pay bloodstock bills |
| Prism Plus and Prism Plus Tablet | Yard-floor tablet app. QR and microchip scanning, welfare flags, extra dashboards |
| Prism Finance | Expenses, owner splits, statements, Xero and MYOB sync |
| Prism Pay | Payments engine for owner bills |
| Website builder | White-label stable websites from free templates. Custom design, CRM, marketing and SEO cost extra |

## 3. Features in detail

### 3.1 Owner portal and communications

- Five sections: Notifications, Racing, Horse, Trainer, Comms.
- Horse profile: location, status, barn, box, trainer, statistics, racing history, media, reports. With trainer permission, owners also see trackwork, procedures and transport.
- Owner chooses the channel for each notification type: web, mobile push or email.
- Notification types: nomination, acceptance, scratching, result, location or status change, new horse, new photo or video or audio, new report.
- **Add horse:** the owner asks the trainer for access to a horse. The trainer approves.
- **Invite trainer:** if the trainer is not on Prism, the owner sends an invitation from the horse page.

### 3.2 Finance (from the 115-page Finance manual)

- **Daily rates** with sub-rates. Prism adds day-rate charges each day.
- **Rules and presets.** A procedure, transport, farrier or dentist event makes a charge by a rule. Prism adds the expenses each day.
- **Group expenses** by location, by status, or by arrival date.
- Split an expense. Credit notes by owner or by horse. Files attached to transactions. A floating "add anywhere" button.
- **Ownership:** owners on a horse, share percentages, access requests, archive an owner.
- Tax rates and chart of accounts synced from Xero.
- **Statement run in 11 steps.** The system checks before it bills:
  1. Incomplete tasks
  2. Horses with no daily charge
  3. Horses with no ownership
  4. Missing fields
  5. Expenses to confirm
  6. Owner summary and reversals
  7. Discounts
  8. Credit notes
  9. Preview
  10. Review each owner statement
  11. Complete and send by email
- Statements have a "Pay here" button. Owners can pay by card, bank transfer or direct debit on the due date (Thoroughbred Payments partnership). Payments reconcile to Prism and Xero.

### 3.3 Yard floor (Prism Plus)

- Print A4 QR cards for each barn, box and horse. Scan a card on the tablet to open that list or horse.
- Scan a horse's microchip with a Bluetooth reader. Prism finds the horse.
- Add a horse from the Australian Stud Book. Prism copies the microchip number to the profile.
- **Welfare flags.** Staff flag a welfare concern. The flag escalates for fast action.

### 3.4 Staff

- Invite staff by email with name, location and position.
- Permissions per module: view, create, update, delete.
- Staff states: active, awaiting approval, invited, unregistered.
- Higher tiers: tasks, timesheets, rostering, payroll, recruitment, and automatic scheduling of trackwork, vet work and transport.

### 3.5 Racing (not relevant to C-gate's core market)

- Nominations, acceptances, fields, form and results, from the Racing Australia feed.
- Pre-race and post-race reports.

## 4. Clients

| Client or claim | Source | Confidence |
|---|---|---|
| "75% of Australia's top thoroughbred trainers" | Prism trainers page | Low: vendor claim |
| Freedman Racing: on the platform, with a Prism-built website | Prism news post, about 2017 | Medium: old, vendor source |
| "Designed with several leading trainers" | Prism manuals | Medium |
| 151 million owner updates sent | Prism owners page | Low: vendor claim |

We found no public confirmation for other named stables. Ask Australian contacts before you quote any name.

## 5. Integrations

| Integration | What it does |
|---|---|
| Racing Australia daily registration file (9am each day) | Horse movements, changes, ownership transfers, race data. Pre-builds every trainer and horse profile. |
| Australian Stud Book | Add a horse by lookup. Copies the microchip number. |
| Bluetooth microchip scanners | Find a horse by its chip on the tablet app |
| Xero (listed on Xero App Store as "Prism Pay") | Invoices, expenses, payments, tax rates, chart of accounts |
| MYOB | Invoicing and accounts |
| Thoroughbred Payments | Card, bank transfer and direct debit; reconciliation to Prism and Xero |
| Racing Collective | Horses for sale on stable websites |
| iOS and Android | Three native apps plus a tablet app |

## 6. Pricing

- Per module, per month, by horse count (AUD): **$30** for 1–14 horses, **$90** for 15–49, **$125** for 50–99. Top tier ("Ultimate") price not found.
- 14-day free trial.
- A free "Starter" account for trainers (about 2019): own horses, race history, statistics, news, automatic nominations and acceptances. We did not confirm that it still exists.
- Free owner app. The owner is the network, not the payer.

## 7. Why this worked: the growth loop

```
Racing Australia data ──► every trainer and horse has a profile on day one
        │
        ▼
Trainer signs up (free starter) ──► sends updates to owners
        │                                   │
        ▼                                   ▼
Finance + payments make Prism        Owners get the free app
hard to leave (switching cost)              │
        ▲                                   ▼
        │                     Owner sees horses at other trainers
        │                     and invites those trainers
        └───────────────────────────────────┘
```

Key points:

1. **No empty screens.** The hardest part of yard software is the first data entry. Prism removed it with a national data feed.
2. **The owner is the sales channel.** Owners have horses at many trainers. Each owner can bring in more trainers at no cost to Prism.
3. **Communication first, then money.** Updates make owners happy. Finance and payments then keep the trainer.
4. **Modules let small stables start cheap.** A 10-horse stable pays $30 per month for one module.

## 8. What this means for C-gate

| Prism move | UK livery and stud version | C-gate today |
|---|---|---|
| National data feed | No single UK feed. Options: Weatherbys (thoroughbreds, studs, racing), passport issuing organisations, UELN and microchip numbers, CSV import from other products | CSV import (`import_data`); microchip and passport fields on the horse |
| Owner app and network | Owner portal. Owners with horses at two yards ask the second yard to join. | No owner login. Email to owners only. |
| Owner updates | Photo and note updates from the yard to owners | Horse photos. Health and invoice reminder emails. |
| Daily rates and charge rules | Same | Placements at a daily rate. Health records make charges. Bulk charges. "Add margin". |
| Statement checks | Invoice run checks before billing | Monthly pro-rata invoicing, credit notes, dunning, reconciliation report |
| Payments | Stripe Connect (Direct Debit first) | Planned: `docs/payments/payments-plan.md` |
| Xero and MYOB | Xero (UK) first. QuickBooks second. | Xero sync |
| QR and microchip scan | Same. Microchips are compulsory for all horses in England. | Microchip field. No QR cards or scanning. |
| Welfare flags | Same | Dashboard attention list. No staff flag. |
| Supplier logins | Farrier, vet, dentist, physio | Farrier page. C-vet for vets. |
| Staff tasks and rota | Daily yard jobs, turnout, feeds, rota | Barns, stables and turnout. No task list or rota. |
| Website builder | Yard site with live vacancies and horses for sale | `website/` (Astro site for our own farm) |
| Built with leading stables | Pilot with our own yard, then 5 reference yards | One yard |

**Do not copy:** racing nominations, acceptances, trackwork and race reports. The UK market sizing excludes racing trainers.

## 9. Confidence

| Fact | Confidence | Why |
|---|---|---|
| Features in sections 3.1–3.4 | Medium to high | Prism's own manuals and help centre. Manuals are labelled draft. |
| Integrations | Medium | Prism pages and app store listings. Thoroughbred Payments post is about eight years old. |
| Pricing | Medium | Search results that quote the pricing page. Check the live page. |
| Market share and client names | Low | Vendor claims only |

## Sources

- [Prism – About](https://www.prism.horse/about), [Trainers](https://www.prism.horse/solutions/trainers), [Owners](https://www.prism.horse/solutions/owners), [Features](https://www.prism.horse/features), [Pricing](https://www.prism.horse/pricing), [Plus](https://www.prism.horse/plus), [FAQs](https://www.prism.horse/resources/faqs)
- Prism manuals: [Owner Portal](https://s3-ap-southeast-2.amazonaws.com/prismhorse/assets/manuals/PRISM+MANUAL+-+OWNER+MODULE.pdf), [Registration and Set Up](https://s3-ap-southeast-2.amazonaws.com/prismhorse/assets/manuals/PRISM+MANUAL+-+REGISTRATION+AND+SET+UP.pdf), [Finance](https://s3-ap-southeast-2.amazonaws.com/prismhorse/assets/manuals/PRISM+MANUAL+-+FINANCE.pdf), [Staff](https://s3-ap-southeast-2.amazonaws.com/prismhorse/assets/manuals/PRISM+MANUAL+-+STAFF.pdf)
- [Prism partners with Thoroughbred Payments](https://www.prism.horse/news/29499/prism-partners-with-thoroughbred-payments-to-improve-the-industries-payment-processing-and-transparency)
- [Introducing Prism Starter Accounts](https://www.prism.horse/news/40075/introducing-prism-starter-accounts:-access-prism-for-free)
- [Freedman Racing boosts its operations through Prism](https://template3.webbuilder.prism.horse/news/54640/freedman-racing-boosts-its-operations-through-prism)
- Prism help centre: [Scan a microchip](https://help.prism.horse/portal/en/kb/articles/scan-a-horse-microchip), [QR cards](https://help.prism.horse/portal/en/kb/articles/scan-a-barn-box-horse-card), [Get started with Finance](https://help.prism.horse/portal/en/kb/articles/how-to-get-started-with-prism-finance), [Communications](https://help.prism.horse/portal/en/kb/articles/get-started-communications)
- App stores: [Prism – Horse Management](https://apps.apple.com/au/app/prism-horse-management/id1106220250), [Prism Horse Owners](https://apps.apple.com/au/app/prism-horse-owners/id6446160495), [Prism Plus](https://play.google.com/store/apps/details?id=horse.prism.plus&hl=en)
- [Prism Pay on the Xero App Store](https://apps.xero.com/au/app/prism-pay)
- [Prism on Capterra Australia](https://www.capterra.com.au/software/176434/prism)
