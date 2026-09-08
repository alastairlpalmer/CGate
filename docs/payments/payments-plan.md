# Yardway payments plan

Date: 8 September 2026. Currency: GBP. Provider fees quoted ex VAT. Stripe and GoCardless add 20% VAT to their fees for UK merchants.

## Summary

- **Use Stripe Connect.** One integration gives cards, Bacs Direct Debit, Pay by Bank (open banking), hosted checkout, KYC of each yard, and a platform fee on every payment. Xero already reconciles Stripe payments, so the yard's accountant sees nothing new.
- **Direct Debit is the main rail.** Livery is a fixed monthly amount from the same owner. Bacs Direct Debit costs 1% capped at £4 per payment. Cards cost 1.5% + 20p with no cap. On a £695 full livery invoice that is £4.00 against £10.63.
- **Money never touches Yardway.** The owner pays the yard's Stripe account. Stripe pays the yard's bank. Yardway takes an application fee from each payment. This keeps Yardway out of FCA payment-institution licensing.
- **Charge the yard, not the owner.** UK law bans surcharges on consumer card payments. Yardway's fee goes on the yard's side as a blended rate: Direct Debit 1.5% capped at £5, cards 2.0% + 20p. Yardway keeps about 0.5% of each payment.
- **Expected revenue:** about £320 per yard per year on top of the subscription, at 22 horses and 70% Direct Debit uptake. Across the base case of 360 yards that is about £115k per year.
- **Build in four phases.** Pay-now links first (3 weeks), then Direct Debit mandates and auto-collection (4 weeks), then Connect onboarding and application fees when multi-tenant lands, then the owner portal.

## 1. Which provider

| Provider | Cards | Direct Debit | Open banking | Platform fee on each payment | Yard onboarding and KYC | Xero support | Verdict |
|---|---|---|---|---|---|---|---|
| Stripe Connect | Yes | Yes, Bacs | Yes, Pay by Bank, UK only | Yes, application fee | Hosted, Stripe does KYC | Yes | **Use this** |
| GoCardless partner API | No | Yes, best in class | Yes, Instant Bank Pay | Yes, app fee | Hosted partner flow | Yes | Second choice if Direct Debit dominates and yards want smarter retries |
| Square, SumUp, Zettle | Yes | No | No | No platform model | Each yard signs up alone | Partial | No |
| Adyen for Platforms | Yes | Yes | Yes | Yes | Custom | Yes | Built for large volume. Too heavy for this stage |
| TrueLayer, Atoa | No | No | Yes | Some | Merchant sign-up | Some | Cheap open banking. Add later behind Stripe if needed |

Why Stripe: one API, one webhook feed, one reconciliation path, and all three payment rails a yard needs. GoCardless is the better pure Direct Debit product, but it cannot take a card from an owner who wants to clear an overdue balance today.

## 2. Provider fees

### Stripe, UK account, 2026

| Payment method | Fee | Notes |
|---|---|---|
| UK consumer card | 1.5% + 20p | Most owners |
| UK premium or business card | 1.9% + 20p | Corporate cards, some owners with business accounts |
| EEA card | 2.5% + 20p | Rare for livery |
| Non-EEA card | 3.25% + 20p | Rare |
| Bacs Direct Debit | 1%, minimum 20p, capped at £4 | 4 working days to confirm; up to 7 for a new mandate |
| Pay by Bank | Not published in a form I could verify | Assume about 1% capped at £4 until confirmed on the Stripe pricing page |
| Card dispute | £20 per dispute | Refunded if the yard wins |
| Connect Express account | £2 per active account per month, plus 0.25% + 10p per payout | Only when Yardway sets the yard's pricing. Zero if Stripe bills the yard directly |
| VAT | 20% on all fees | Reclaimable by VAT-registered yards |

### GoCardless, UK, 2026

| Item | Fee |
|---|---|
| Direct Debit, Standard plan | 1% + 20p, capped at £4 |
| Plus plan | from £50 per month, adds retries and variable amounts |
| Pro plan | from £250 per month |
| Instant Bank Pay | Plus or Pro plan. About half the cost of cards, capped at £4 for many |
| Partner app fee | Set by the partner, deducted from each payment |

### What each rail costs on a real invoice

| Invoice | Card 1.5% + 20p | Direct Debit 1% cap £4 | Saving with Direct Debit |
|---|---|---|---|
| DIY livery £201 | £3.22 | £2.01 | £1.21 |
| Average livery £257 | £4.06 | £2.57 | £1.49 |
| Full stabled £695 | £10.63 | £4.00 | £6.63 |
| Full ridden £1,010 | £15.35 | £4.00 | £11.35 |

Direct Debit wins on every livery invoice. Cards are for extras, first payments, and overdue balances.

## 3. What Yardway charges on top

### The legal limit

- The Consumer Rights (Payment Surcharges) Regulations, amended January 2018, ban any surcharge on a consumer's debit, credit or prepaid card, including Apple Pay and Google Pay.
- A surcharge on Direct Debit or bank transfer is allowed only up to the yard's actual cost.
- Almost every horse owner is a consumer. So the yard cannot add a card fee to the owner's invoice.

Result: Yardway's margin sits on the yard's side, as a fee the yard pays for getting paid.

### Three pricing models

| Model | How it works | For the yard | For Yardway |
|---|---|---|---|
| A. Pass-through plus platform fee | Yard pays Stripe's fee plus a Yardway application fee of 0.5% | Transparent but two line items | Margin visible, invites comparison with Stripe direct |
| B. Blended rate (recommended) | Yard pays one rate per rail. Yardway pays Stripe out of it | One number to understand | Margin hidden in the rate, same as Xero and Square |
| C. Flat add-on | £15 per month, Stripe at cost | Predictable | No link to volume. Small yards overpay, large yards underpay |

### Recommended rate card for yards

| Rail | Yard pays | Stripe costs Yardway | Yardway keeps |
|---|---|---|---|
| Bacs Direct Debit | 1.5%, capped at £5 | 1%, capped at £4 | 0.5%, capped at £1 |
| Pay by Bank | 1.5%, capped at £5 | about 1%, capped at £4 | about 0.5% |
| UK consumer card | 2.0% + 20p | 1.5% + 20p | 0.5% |
| Premium, EEA and non-EEA cards | Stripe rate + 0.5% | Stripe rate | 0.5% |
| Connect account fee £2 per month | Absorbed in the subscription | £2 | £0 minus £2 |

Keep the margin at 0.5%. Xero offers Stripe at 1.5% + 20p with no markup, and any yard can compare. The value Yardway sells is not the rate. It is mandates tied to owners, collection on the due date, automatic matching to the invoice and the ledger, retries, and no chasing.

### Revenue per yard

Assumptions: 22 horses, £257 average invoice, 12 invoices per horse per year, 70% by Direct Debit, 30% by card.

| Rail | Payments per year | Yardway margin each | Revenue |
|---|---|---|---|
| Direct Debit | 185 | £1.00 to £1.29 | about £220 |
| Card | 79 | £1.29 | about £100 |
| Total per yard | | | **about £320** |

At the base case of 360 yards that is about £115k a year. Yards with full livery and extras will bring more because the Direct Debit cap makes the rail cheaper for them and card use rises with extras.

### Packaging

- Payments are only available to paying subscribers. This protects the subscription.
- Show the rate card in the yard's settings before they connect Stripe. No hidden fees.
- Show the fee on every payment record and in the CSV export, so the yard's bookkeeper can post it.

## 4. How the money moves

1. The yard connects a Stripe Express account from Yardway settings. Stripe runs KYC. Yardway stores the account id.
2. The owner clicks the pay link on an invoice email or in the owner portal. Stripe Checkout runs on Stripe's page. The owner picks Direct Debit, Pay by Bank or card. A Direct Debit mandate is saved for next time.
3. The payment is created on the yard's connected account with an application fee for Yardway.
4. Stripe pays the yard's bank on the yard's payout schedule. Stripe pays Yardway's fee to Yardway's platform balance.
5. Stripe sends a webhook. Yardway creates a Payment record against the invoice and updates its status. If the yard uses Xero, Yardway posts the payment to Xero against the synced invoice.

Yardway never holds, receives or forwards the owner's money. That is what keeps Yardway outside FCA payment-institution authorisation. Stripe is the regulated party.

## 5. Fit with the current code

| Existing piece | Change |
|---|---|
| `BusinessSettings.card_payment_url` | Replace with a per-invoice hosted Checkout link. Keep the field as a fallback for yards that do not connect Stripe |
| `Invoice` | Add `stripe_checkout_id`, `stripe_payment_intent_id`, `collection_scheduled_for`. Keep status logic as is |
| `Payment.Method` | Add `stripe_card`, `stripe_bacs`, `stripe_bank`. Add `provider_fee` and `platform_fee` decimals and `provider_reference` |
| `Owner` | Add `stripe_customer_id` and `default_payment_method_id`. Add a `pay_by_direct_debit` flag the yard can see |
| `BusinessSettings` | Add `stripe_account_id`, `payments_enabled`, `auto_collect_direct_debit`, `collect_days_before_due` |
| Invoice email and PDF | Replace the "Or pay by card" line with a "Pay now" button and a Direct Debit note |
| `xero_integration` | On `payment_intent.succeeded`, create a Xero payment on the invoice and a bank-fee spend for the Stripe fee. Decide one source of truth: Yardway posts payments, Xero's own Stripe feed stays off |
| Celery beat | New nightly job: find invoices due within `collect_days_before_due` working days whose owner has a mandate and no scheduled collection, and create the Bacs payment intent. Second job: retry failed collections once after 5 working days |
| Webhooks | New endpoint with Stripe signature checks. Handle `checkout.session.completed`, `payment_intent.succeeded`, `payment_intent.payment_failed`, `mandate.updated`, `charge.dispute.created`, `charge.refunded`, `account.updated` |
| Roles | Payments sit under the existing finance feature gate |

Multi-owner invoices already split by ownership share, so each owner pays their own invoice. No change.

## 6. Phases

| Phase | Scope | Effort | Depends on |
|---|---|---|---|
| 1. Pay-now links | Stripe Checkout per invoice, card + Bacs + Pay by Bank, webhook to Payment, fee stored, Xero payment posted. Single yard on one Stripe account, no Connect yet | 3 weeks | Nothing. Can go live on the current single-tenant deployment |
| 2. Direct Debit auto-collect | Mandate saved at first payment or via a set-up link, nightly collection job, failure handling, retry, owner emails | 4 weeks | Phase 1 |
| 3. Connect platform | Express onboarding, application fees, rate card in settings, payout view, per-yard reconciliation | 3 weeks | Multi-tenant work from the market sizing report |
| 4. Owner portal and extras | Owner logs in by magic link, sees invoices and statements, manages mandate and card, pays extras. Pay by Bank as first choice on the pay page | 4 weeks | Phase 2 |

Phase 1 alone answers the first question a yard asks: can my owners pay this invoice by clicking a button. Phase 2 is where the churn protection and the margin come from.

## 7. Rules and risks

- **Direct Debit Guarantee.** An owner can claim a refund from their bank for any Direct Debit, and Stripe passes the indemnity claim to the yard. Give owners advance notice: Stripe emails it, but the invoice must show the amount and the collection date.
- **Advance notice timing.** Bacs needs about 3 working days before collection. Set `collect_days_before_due` to 5 working days so the money lands on or before the due date.
- **Card disputes.** £20 per dispute. Show the yard name and invoice number on the card statement descriptor to cut confusion disputes.
- **Strong Customer Authentication.** Stripe Checkout handles 3D Secure. Saved cards used off-session can fail SCA. Prefer Direct Debit for anything recurring.
- **PCI.** Use Stripe Checkout or Elements only. Yardway never sees a card number, which keeps it at the lowest PCI level, SAQ A.
- **VAT.** Yardway's fee is a standard-rated service. Invoice yards for platform fees monthly with VAT, or let Stripe's application fee reporting drive a monthly VAT invoice.
- **Terms.** Yardway is a platform, the yard is the merchant of record. The yard accepts the Stripe Connected Account Agreement during onboarding. Add a payments schedule to Yardway's terms with the rate card and the refund policy.
- **Yard closures.** A yard that closes with open mandates must cancel them. Add a "close payments" step that cancels mandates and stops scheduled collections.
- **Data.** Store only Stripe ids and last-four digits. Nothing else about the payment method.

## 8. Decisions to make

1. Blended rate or pass-through. Recommended: blended.
2. Margin. Recommended: 0.5% on every rail.
3. Who posts payments to Xero. Recommended: Yardway, with the yard's own Xero Stripe feed switched off.
4. Should Direct Debit be the default for every owner at onboarding. Recommended: yes, with card as the fallback.
5. Pilot. Recommended: run Phase 1 and 2 on the current single yard for two invoice cycles before building Connect.

## Sources

- Stripe UK pricing and Bacs Direct Debit documentation
- Stripe Connect pricing
- GoCardless pricing and partner app fees
- Consumer Rights (Payment Surcharges) Regulations 2012, amended 2018, via GoCardless and Stripe support notes on the PSD2 surcharge ban
- Stripe guide on PSD2 and marketplaces, on why platforms avoid holding funds
- Xero online invoice payments
