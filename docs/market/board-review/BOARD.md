# Board recommendation: Cgate

Date: 19 November 2026. Model: `BOARD_model.xlsx` in this folder (the scenario test files were not kept). Figures marked "est." are board estimates.

## 1. Decision

Cgate is software for the businesses that look after thoroughbreds between races: studs, grazing and pre-training yards, and the trainers they send horses to. We sell first to studs and grazing yards, because the product is already built for them. Five race trainers join as paid design partners on hosted installs. We earn money from a per-horse subscription made of three plans, paid add-ons, set-up fees and a small fee on payments. We raise £450k of SEIS/EIS from racing and equine angels in Q1 2027. A second £200k–£250k follows in H2 2028 only if milestones are met. **We build this as a profitable, bootstrapped-first business.** We seek venture money only if two engines are proven by **30 November 2028**: Ireland (10 paying Irish accounts) and the paid Owner Pass (3 syndicates paying). Even if both work, our model reaches only about £1.6m–£2.2m ARR in 2031 (2.6x–3.7x for investors). So venture money also needs a credible third market, such as Australia. The founder should plan for a good business worth about £4–6m (est.), not a unicorn.

## 2. Decisions on the disputes

| Dispute | Decision | Why |
|---|---|---|
| Race price | **Race Yard plan £5.50/horse/month, £99 minimum.** This covers the core, stables and training. Fields cost £1 extra. Owner updates cost £2 extra. | The CCO's £5.50 and the model's £7.60 are the same price. £7.60 just counts add-ons. A £149 minimum costs a 15-horse jump yard about £10 a horse, against Yardman's £75 a month for 50 horses. In the model, owner-update take-up falls from 80% to 60%, which gives an average of £7.20. |
| Plans | Yard £2.50 (min £29). **Stud & Farm £6.00** (min £99). Race Yard £5.50 (min £99). | A bundle should not cost more than its parts. The CCO's £6.50 did. |
| Set-up fee | Race and hosted installs £1,000. Studs and large yards £500. Self-serve £0. | This pays for the hosted install and the AI-led import. |
| C-vet | **Free in 2027** for vets who visit Cgate yards. **£60 per active vet a month** once 20 vets use it every week. Not in the forecast. | It is a channel and a source of records, not a product line yet. |
| Farrier | Free. No paid marketing. | It brings in yards. At £15 it made only about £31k a year. |
| Stud launch | **Now (Q1 2027), as hosted installs.** | Built today. Weakest competition. |
| Developers | Founder plus **1 senior developer from March 2027**, plus a multi-tenant contractor (1.25 FTE in 2027). Hire a second developer only at **£15k MRR**. | This follows the CTO's capacity plan (about 105 pw available for about 90 pw of work) and the CFO's hiring rules. |
| Build order | **Pay-now links → owner updates → multi-tenant.** Hosted installs are capped at **10**. | Paying design partners need owner updates first. Multi-tenancy is only needed at about 10 installs. |
| Vet practice software | Stopped. Restart only if a partner funds it or after 2 profitable quarters. | All four executives agree. |

## 3. Price list (2027)

| Item | Price |
|---|---|
| Yard (livery) | £2.50/horse/month, min £29. Fields +£1. Self-serve only. |
| Stud & Farm (core, fields, stables, breeding) | £6.00/horse/month, min £99 |
| Race Yard (core, stables, training) | £5.50/horse/month, min £99. Fields +£1. |
| Owner updates (paid by the yard) | £2/horse/month |
| **Owner Pass (paid by an owner or syndicate)** | £4/owner/month, or £3/horse/month billed to the syndicate. To be tested. |
| Set-up | £1,000 race/hosted; £500 stud; £0 self-serve |
| Payments | 0.5% fee, capped at £1 per payment (Stripe Connect) |
| C-vet | Free in 2027, then £60/vet/month |
| Farrier app | Free |
| Terms | 12-month contract. Annual prepay at 10% off. A paid 30-day start (£250), credited against the first year. Design partners get 20% off, fixed for 24 months. |

## 4. Product plan (December 2026 to May 2028)

| When | Build | Unlocks |
|---|---|---|
| Dec 2026–Jan 2027 | Staging, restore test, one-command hosted install, manual Stripe invoices | First set-up fees and subscriptions |
| Jan–Feb 2027 | Stripe Connect pay-now links | Payments; a reason to buy |
| Feb–Apr 2027 | Owner updates v1: magic-link owner page, photo and voice note to AI-drafted update, weekly email | £2 add-on; Owner Pass pilot |
| Mar–Apr 2027 | Racing light: syndicates, daily work board, race notes | Race Yard plan |
| Apr–Jul 2027 | Multi-tenant (shared schema with row-level security). Move installs onto it | Selling without per-install cost |
| Jul–Sep 2027 | Self-serve sign-up, Stripe Billing, AI import from Yardman and miStable | Livery from Q1 2028 |
| Oct–Dec 2027 | Horse transfer between customers, Owner Pass billing, euro and Irish VAT, C-vet for many yards | Network effect; Ireland ready |
| Jan–May 2028 | Export treatment records to an Equine MediRecord format, stud sale pack, Direct Debit, penetration test | Ireland; studs; trust |

## 5. The second engines

| Engine | Evidence | Size (est.) | Model test | Proof needed |
|---|---|---|---|---|
| **A. Ireland** (open mid-2028 with the same plans, in euros) | Ireland has about 8,600 racing-active horses, plus about 3,000 inactive ones in trainers' care. New HRI rules make trainers report every thoroughbred they hold ([HRI](https://www.hri.ie/corporate/press-office/press-release/horse-racing-ireland-publishes-industry-figures-for-first-six-months-of-2025)). About 360 trainers are IRTA members ([IRTA](https://issuu.com/anderson-co/docs/irta_diary_2025_issuu)). The latest Irish foal crop is about 7,900 ([Weatherbys Fact Book](https://issuu.com/weatherbys/docs/weatherbys_fact_book_2025)). Britain and Ireland together registered 11,374 live foals in 2025 ([Racing TV](https://www.racingtv.com/news/return-of-mares-2025-all-the-information-and-stats)). So Ireland breeds most of them. | +700 studs and +200 trainers. About £2.4m more SAM. | +£0.36m ARR in 2031 (Base) | **By 30 Jun 2028:** 10 paying Irish accounts. Cost to win one under £800. At least 3 horses moved between Cgate customers in Britain and Ireland. |
| **B. Owner Pass** (owners and syndicates pay) | Over 60% of horses are jointly owned ([William Hill](https://news.williamhill.com/horse-racing/the-different-ways-you-can-own-a-racehorse-explained/)). The RSA has about 9,000 people in syndicates ([Racing Post](https://www.racingpost.com/news/britain/you-must-have-a-variety-of-trainers-rsa-chair-says-reduction-in-number-of-licensed-operators-is-affecting-ownership-ajPRt6O73bQy)). Keeping owners is a priority for the ROA ([ROA](https://www.roa.co.uk/roa/owner-relations.html)). TLore charges $2–4 per horse ([Past the Wire](https://pastthewire.com/tjc-innovations-releases-tlore-owner-app/)). MyRacehorse raised $7m ([Business Wire](https://www.businesswire.com/news/home/20230602005202/en/Global-Horse-Racing-Ownership-Platform-MyRacehorse-Secures-7M-for-Expansion)). | About 14,000 horses × 4 owners = about 56k owner places in the UK. 10% at £4 = about £270k ARR. | +£85k ARR (+£2/race horse/month) | **By 31 Dec 2027:** 3 syndicates or partnerships paying. At least 15% of owners in pilot yards paying. Monthly churn under 3%. |

**Rejected as engines.** *The horse record sold to regulators and insurers* is already held by others. Equine MediRecord serves the BHA, the IHRB, the Hong Kong Jockey Club and the Preakness. It is owned by the PE-backed Business Infusions group after a €10m deal ([Irish Times](https://www.irishtimes.com/business/2022/08/04/equine-medirecord-raises-eight-figure-sum-to-drive-growth), [TDN](https://www.thoroughbreddailynews.com/preakness-150-partners-with-equine-medirecord-again-to-aid-welfare-protocols/)). Weatherbys runs the stud book and the vaccination app ([BEVA](https://www.beva.org.uk/News/Latest-News/Details/BHA--Weatherbys-Remind-Trainers-about-Vaccination-App-Copy-of-Communication)). We integrate with them rather than compete. *Payments* is a reason to buy, but it brings in only about 1.5% of revenue.

**Exit route.** The likeliest buyers are trade buyers. Business Infusions/Inova is a proven buyer of equine software. Weatherbys builds racing software and invests in technology ([ODT](https://www.odt.co.nz/node/2532360)). Vet groups and niche-software buyers are other options. **Third market, for venture only:** Australia (about 2,280 trainers in four states ([Racing Australia](https://publishingservices.racingaustralia.horse/Newsletters/2024_Racing_Australia_Fact_Book/4/)); the home of miStable) or the US (3,518 trainers and 17,103 foals ([TDN](https://www.thoroughbreddailynews.com/state-of-the-industry-the-habitat/), [Past the Wire](https://pastthewire.com/jc-17103-live-foals-in-2025/))).

## 6. Numbers

Base is the CFO's scenario (c) plus the board's changes. Return is to the pre-seed investor, at about £1.8m pre-money and a 4x ARR multiple. "Money needed" is the model's cash low point plus a 6-month buffer.

| Scenario | ARR 2031 | Money needed | First profitable year | Investor return (Base / High multiple) | Growth 2031 |
|---|---|---|---|---|---|
| CFO (c) as filed (check) | £1.17m | £0.66m | 2029 | 1.28x at £3m pre | 29% |
| **Board Low** | £0.66m | £0.95m | 2031 | 0.96x / 1.44x | 29% |
| **Board Base** | **£1.10m** | **£0.65m** | **2030** (2029 is about break-even) | **1.80x / 2.70x** | 29% |
| **Board High** | £1.54m | £0.58m | 2029 | 2.59x / 3.89x | 29% |
| Base + Ireland | £1.47m | £0.72m | 2029 | 2.33x / 3.49x | 31% |
| Low + Ireland | £0.88m | £1.00m | 2031 | 1.26x / 1.88x | 31% |
| Base + Owner Pass | £1.19m | £0.62m | 2029 | 1.96x / 2.95x | 29% |
| Base + both engines | £1.59m | £0.70m | 2029 | 2.55x / 3.82x | 31% |
| High + both engines | £2.23m | £0.63m | 2029 | 3.67x / 5.50x | 31% |

A 10x return needs about £24m of value, or about £4.9m ARR at 5x. That is about 4.5x Board Base, and still 2.2x the best case. Our verdict is to build it bootstrapped first. Board Base needs £0.65m: £450k now plus about £200k in 2028, from revenue-based finance or EIS.

## 7. First 90 days (to 17 February 2027)

| By | Action |
|---|---|
| 28 Nov 2026 | Apply for HMRC SEIS/EIS advance assurance. Instruct a lawyer on SEIS/EIS docs. Draft a one-page success plan and a paid-start contract. |
| 5 Dec | Make a list of 40 warm contacts: 10 studs, 6 grazing and pre-training yards, 10 trainers (10–60 horses), 4 syndicate managers, 4 vets, 3 Irish studs and trainers, 3 angel investors. |
| 19 Dec | Finish 10 interviews (the CCO's list). Test £5.50 and £6.00, the £99 minimum, the £4 Owner Pass, and who signs. |
| 19 Dec | Start the senior developer search (AI-native, Django). Offer 2–5% equity. |
| 9 Jan 2027 | Ship the one-command hosted install and staging. Bill the current client via Stripe. |
| 16 Jan | Finish 30 interviews. Sign 5 design partners: 2 studs, 1 grazing or pre-training yard, 2 trainers. Each pays a set-up fee. |
| 23 Jan | Pitch 15 racing and equine angels. Also contact the TBA, NTF and RSA about channel deals. |
| 13 Feb | Pay-now links live at the current client and 2 studs. Developer starts by 1 March. |
| 17 Feb | £450k pre-seed closed or committed. 3 paying accounts plus 5 signed partners. Owner updates v1 in testing with 2 trainers. Board review. |

## 8. Decisions only the founder can make

1. **Ambition.** Will you build a profitable company worth about £4–6m (est.) if the engines fail, rather than chase venture growth?
2. **Vet software.** Will you shelve the wish to become the practice's main system for at least 3 years?
3. **Ireland.** Will you personally open Ireland in 2028, with trips and introductions?
4. **The senior developer.** Who is it, and how much equity do they get?
5. **Dilution.** Will you take about 20% dilution for £450k at about £1.8m pre-money, rather than wait for more traction?
