# Yardway: UK market size and revenue model

Date: 7 September 2026. Currency: GBP, ex VAT, per year unless stated.

## Summary

- The UK business market for Yardway is small and niche. Serviceable market (SAM) is about **£4.8m per year** in subscriptions across about 7,500 accounts.
- Total addressable market (TAM) for business users is about **£8.7m per year**. A consumer tail (home-kept horses) adds about £5.4m but converts badly and is excluded from SAM.
- A realistic three-year outcome is **£130k to £430k annual recurring revenue (ARR)**, with a base case of about **£270k ARR from about 360 paying yards**.
- Break-even at a fixed cost of £110k per year needs about **200 paying accounts**. The base case reaches this early in year 3.
- The largest untapped revenue is not subscriptions. UK livery fees are about **£1.3bn per year**. A payments layer on Yardway invoices can add 15% to 50% to revenue per account.
- Blocker: Yardway is single-tenant today. It has no organisation model, no self-serve sign-up and no subscription billing. This must be built before any launch.

## 1. What Yardway is today

Yardway is a Django app built for one UK grazing and stud business with seven named sites. It does:

- Horses, owners, fractional ownership, placements at a daily rate
- Monthly pro-rata invoicing, PDF and email, part payments, aged debtors, Xero sync
- Health: vaccinations, farrier, worming, worm egg counts, vet visits
- Breeding: covering, scans, foaling, EHV reminders
- Yard costs, feed stock, priced services directory
- Field maps with Land App and RPA boundary import
- Roles and permissions, PWA mobile layout, Celery reminders

It does not do: lesson or arena bookings, staff rota, owner self-service app, card payments, racing entries.

Best fit: grazing, DIY and full livery yards, and studs with a breeding cycle. Weak fit: riding schools, racing yards.

## 2. Market facts used

| Fact | Value | Source |
|---|---|---|
| Horses in Britain | 726,000 | BETA National Equestrian Survey 2023 |
| Horse-owning households | 331,000 | BETA 2023 |
| Horses kept away from home | 72.9% of owners | BETA 2019 |
| Leisure horses at livery yards | about 60% | Animals journal 2021, citing BETA |
| Livery yards in the UK | about 10,000 (trade estimate) | trade press; BHS approves 290 standalone yards and 500 riding schools with livery |
| Yards visible in directories | 720 to 750 | LiveryList, poidata |
| Average owner spend on livery | £257 per month | BETA 2023 |
| DIY livery average | £201 per month (range £75 to £500) | LiveryList 2026 pricing survey, 768 responses |
| Full stabled livery average | £695 per month | LiveryList 2026 |
| Full ridden livery average | £1,010 per month | LiveryList 2026 |
| Riding schools in the UK | 1,497 (down from 1,747 in 2018) | BHS |
| Licensed racehorse trainers | about 600; 14,555 horses in training | BHA, Sept 2024 |
| Registered thoroughbred breeders | 3,017 (down 35% since 2009) | TBA and PwC 2023 |
| UK equestrian economic value | £5bn wider equestrian; £9.1bn with racing | BETA 2023, British Equestrian |
| UK SME software spend | £1,200 to £2,400 per year | Federation of Small Businesses |

Yard count check. 726,000 horses x 60% at livery = about 435,000 horses at livery. At 10,000 yards this is 43 horses per yard, which is too high for a typical yard. So there are probably 15,000 to 25,000 places that offer livery, but most are very small. The 10,000 figure is a fair proxy for yards that run as a business.

## 3. Competitors and price points

| Product | Pricing | Notes |
|---|---|---|
| Equestrian Systems | £50 to £135 per month | Riding school first; livery included |
| Yardman (Stable IT) | £75 + VAT per month for a 50-horse yard | Racing, studs, livery; long established |
| LIVERYLive | Per active horse per month; free SOLO plan for owners | Partnered with LiveryList directory |
| At The Yard | Three tiers by horse count, up to 40 horses | UK and Australia |
| LiveryLogic | Six-week free trial | Billing, facilities, vendor marketplace |
| YardForge | Free core tools | New entrant |
| EquineM, Equicty, EquiStab | Not published | European |

Market price anchor: £30 to £90 per month per yard, or £1 to £3 per horse per month. Yardway pricing assumption: three tiers at £29, £49 and £89 per month by horse count. Blended average revenue per account (ARPA) is £600 per year for yards and £900 for multi-site studs.

## 4. Market size

### TAM and SAM by segment

| Segment | TAM accounts | SAM accounts | ARPA | TAM | SAM |
|---|---|---|---|---|---|
| Commercial livery yards | 10,000 | 6,000 | £600 | £6.00m | £3.60m |
| Studs, breeding and grazing operations | 1,200 | 1,000 | £900 | £1.08m | £0.90m |
| Riding schools with livery | 1,497 | 500 | £600 | £0.90m | £0.30m |
| Racing trainers | 600 | 0 | £1,200 | £0.72m | £0 |
| **Business total** | **13,300** | **7,500** | **£640 blended** | **£8.70m** | **£4.80m** |
| Consumer tail: home-kept horses | 90,000 households | 0 | £60 | £5.38m | £0 |

SAM rules:
- Livery yards: about 8 or more horses and monthly invoicing. This removes the smallest yards.
- Studs: the stud count is a low-confidence estimate. Thoroughbred studs are perhaps 400. Sport-horse, native and grazing operations add about 800.
- Riding schools: only the livery side. Equestrian Systems owns lesson booking.
- Racing: excluded. Trainers need entries and declarations.
- Consumer: excluded from SAM. Use as a free tier and lead source, as LIVERYLive does.

### Payments layer

Livery fees flowing through UK yards are about £1.3bn per year. For a SAM yard with 22 horses at £257 per month:

| Case | Share of fees paid through Yardway | Net take | Extra revenue per yard per year |
|---|---|---|---|
| Open banking | 30% | 0.5% | £102 |
| Card | 50% | 1.0% | £339 |

This is a 15% to 50% uplift on a £640 ARPA. It also cuts churn, because the yard's cash collection depends on the product.

## 5. Revenue scenarios, three years

Assumptions: ARPA £640 per year, gross margin 85%, monthly churn 2.5%, customer acquisition cost (CAC) £500 per paying yard, payments add-on at the open banking case.

| Scenario | Gross adds Y1/Y2/Y3 | Paying accounts end Y3 | Share of SAM | ARR subscriptions | ARR with payments | CAC spend over 3 years |
|---|---|---|---|---|---|---|
| Low | 60 / 90 / 110 | 180 | 2.4% | £115k | £133k | £130k |
| Base | 120 / 180 / 220 | 360 | 4.8% | £230k | £267k | £260k |
| High | 200 / 300 / 350 | 585 | 7.8% | £374k | £434k | £425k |

Unit economics:

| Metric | Value |
|---|---|
| ARPA per month | £53 |
| Lifetime value (LTV) | £1,813 |
| LTV to CAC | 3.6 |
| CAC payback | 11 months |
| Break-even accounts at £110k fixed cost | 202 |

Year-by-year accounts, base case: 103 at end Y1, 231 at end Y2, 359 at end Y3.

## 6. What a full launch costs

| Item | Estimate | Note |
|---|---|---|
| Multi-tenant rebuild | 4 to 6 months of one developer | Organisation model, data isolation, self-serve sign-up, Stripe subscriptions, onboarding import, support tooling |
| Fixed run cost | £90k to £120k per year | One developer, hosting, support, tools |
| Marketing | £500 per paying yard | LiveryList and Yard Owner Hub partnership, Facebook yard-owner groups, county shows, BHS and ABRS+ channels, SEO content on livery pricing and invoicing |
| Three-year CAC spend, base | £260k | See table above |

Time to break-even in the base case: about month 26.

## 7. Risks

- Yard closures. Two-thirds of yards raised prices in the last year and 80% expect more increases. Small yards are closing. Churn may run above 2.5% per month.
- Crowded low end. Five UK products already sell to the same yards, and two offer free tiers.
- Horse-owning households fell from 374,000 in 2019 to 331,000 in 2023.
- Software adoption in yards is low. Many run on spreadsheets and WhatsApp. This is both the opportunity and the sales friction.

## 8. Where Yardway can win

- Studs and grazing operations. Breeding records, mare and foal rates, field rest tracking and RPA boundary import are not in the competing products. This segment is small but underserved and pays more.
- Multi-site yards. Site-level capacity, maps and per-site feed stock.
- Xero users. Yardway already syncs invoices and payment status.
- Owner statements and aged debtors. Cash collection is the yard owner's main pain in the SEIB and LiveryList surveys.

## 9. Confidence

| Number | Confidence |
|---|---|
| Horses, households, livery prices | High: BETA and LiveryList surveys |
| Livery yard count | Low: 10,000 is a trade estimate with no published method |
| Stud and grazing operation count | Low: no published count; built from TBA breeder numbers |
| Competitor prices | Medium: two published, others tiered by horse count |
| ARPA, churn, CAC | Assumptions from comparable UK vertical SaaS; validate in the first 50 sales |

## Sources

- BETA National Equestrian Survey 2023, via Your Horse and Equestrian Trade News
- LiveryList and YardWise, 2026 UK Livery Pricing Survey, December 2025
- SEIB livery yard cost survey, via British Equestrian
- Thoroughbred Breeders' Association and PwC, Economic Impact Study 2023
- British Horseracing Authority, Racing Report September 2024
- BHS riding school counts, via LiveryList Yard Owner Hub
- Equestrian Systems, Yardman, LIVERYLive, At The Yard, LiveryLogic and YardForge public pages
- Federation of Small Businesses software spend, via SIIT
