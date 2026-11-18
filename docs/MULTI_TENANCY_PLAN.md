# Multi-tenancy plan: build and run

Date: 18 November 2026. Status: proposal. Replaces the outline in [`LAUNCH_READINESS_PLAN.md`](LAUNCH_READINESS_PLAN.md) P2 items 1–5 and 11 when approved.

## Summary

- **Goal:** one C-gate platform serves many yards. Each yard sees only its own data. Owners, farriers and vets can work across yards.
- **Design:** one shared Postgres database. Every yard-owned row has an `organisation` key. Two guards stop leaks: a default query filter in Django, and Postgres row-level security (RLS) under it.
- **Time with one developer:** 19–23 weeks.
- **Time with parallel AI agents:** **7–10 weeks elapsed.** It is not 1–2 weeks. The code is fast to write, but four steps cannot run in parallel: the foundation, human review of security code, the move of live data, and the external penetration test. Section 4 explains this.
- **When to start:** add the organisation key now (wave 0, about 1 week). Start the full build when Launch plan P1 is done and 3–5 yards have agreed to pay. Until then, give each new yard its own deployment.

## 1. The code today

| Item | Count | What changes |
|---|---|---|
| Data models | 66 | Yard-owned models get an `organisation` key |
| ORM calls (`.objects.`) | 3,634 | Filtered by the default manager. Each call site is checked. |
| Raw SQL | 2 | Rewritten or given an explicit filter |
| Celery tasks | 17 | Run once per organisation |
| Management commands | 16 | Take `--org`, or loop over organisations |
| Unique rules (`unique=True`, `UniqueConstraint`) | 26 | Become unique per organisation |
| Settings singletons | 3 (`BusinessSettings.get_settings()`, `XeroConnection.get_connection()`, `SiteSettings`) | Read the current organisation. About 30 call sites. |
| Templates | 256 | Little change. Branding and names come from the organisation. |
| Test files | 172 | Gain a default organisation in the test base class |

Already in place: C-vet keeps the practice as a separate organisation (`cvet.Practice`). `PracticeYardLink` already notes that it will get a yard key.

## 2. Design

### 2.1 Choice

| Option | For | Against | Decision |
|---|---|---|---|
| **Shared database, `organisation` key on each row, plus RLS** | Owners, farriers and vets across yards (roadmap 4.2, 4.7). One deploy, one migration. | One missed filter leaks data. Two guards fix this. | **Use this** |
| Schema per yard (`django-tenants`) | Fewer query changes | Cross-yard owners and suppliers are hard. Each migration runs once per yard. | No |
| Deployment per yard | Strongest separation | Does not scale past about 10 yards. No cross-yard owner view. | Only as a stopgap, before the build |

### 2.2 Parts

1. **`Organisation`**: name, slug, plan, status (trial, active, suspended, closed), data region, created date.
2. **`Membership`**: user, organisation, role. One user can belong to many yards. This replaces the global `UserRole` for yard roles.
3. **Current organisation**: a middleware runs after `AuthenticationMiddleware` and MFA. It reads the subdomain (`<slug>.<domain>`) or the session, checks membership, and sets a `contextvars` value.
4. **Default manager**: `OrgScopedManager` filters every query by the current organisation. **It fails closed:** with no organisation set, it raises an error. It does not return all rows. Code that must see all yards (platform admin, nightly loops) uses `Model.all_orgs` and is reviewed.
5. **Postgres RLS**: each yard table has a policy `organisation_id = current_setting('app.current_org')`. The request sets that value at the start of each transaction. The app's database user cannot bypass RLS. A missed Django filter then returns no rows instead of another yard's rows.
6. **Save guard**: a `pre_save` check refuses a row whose `organisation` is not the current organisation.
7. **Settings**: `BusinessSettings.get_settings()` and `XeroConnection.get_connection()` read the current organisation. Their call sites do not change.
8. **Files**: storage keys start with `org/<id>/`. Signed URLs check the organisation before they are issued.
9. **Background jobs**: each beat entry runs a small loop that starts one task per active organisation. Each task sets the organisation context first. Failed tasks record the organisation.
10. **Numbering**: invoice and credit note sequences become per organisation, still gap-free.
11. **Cross-yard people** (later, roadmap 4.2 and 4.7): an owner account and a supplier account sit above organisations. Each yard keeps its own `Owner` record. A link connects the account to that record. A yard never sees another yard's record.

## 3. Build plan in waves

Each wave ends with all tests green on `main`. The app keeps working for our yard after every wave. Work goes to `main` in small PRs behind tests, not on one long branch.

| Wave | Work | Can run in parallel? | Elapsed time |
|---|---|---|---|
| **0. Foundation** | `Organisation` and `Membership` models. `organisation` key on all yard models, nullable. Fill every row with organisation 1. Make the key required. Context variable and middleware. `OrgScopedManager` (logs a warning only at this stage). Test base class with a default organisation. | **No.** One agent, then a careful human review. Everything else depends on it. | 1–1.5 weeks |
| **1. Scope the queries** | One agent per app: `core`, `core/dashboard`, `billing`, `health`, `invoicing`, `notifications`, `xero_integration`, `cvet`. Each agent checks every query, view, form choice list, report and CSV export in its app. Each agent adds tests that try to read another yard's rows. | **Yes**, 8 agents | 1.5–2 weeks (review is the limit) |
| **2. Shared parts** | (a) Settings singletons and Xero per organisation. (b) File storage prefix and signed URL check. (c) Celery loop per organisation and commands with `--org`. (d) Unique rules and numbering per organisation. (e) Membership, yard switcher and invites. | **Yes**, 5 agents | 1.5 weeks |
| **3. Hard guards** | Turn the manager to fail closed. Add RLS policies and a database user that cannot bypass them. CI job that runs the full test suite with two organisations and checks every list page for leaks. | Partly. RLS and the CI job in parallel. | 1 week |
| **4. SaaS parts** | (a) Self-serve sign-up and setup steps. (b) Stripe Billing: plans by horse count, trial, failed payment, suspend. (c) Support login as a yard with an audit record. (d) Export and delete one organisation's data. | **Yes**, 4 agents | 1.5–2 weeks |
| **5. Go live** | Restore the live database to staging and run the migration there. Soak for one week with our yard's real use. Move production. External penetration test. Data processing agreement and updated privacy terms. | **No** | 2–3 weeks, plus lead time for the pen test |
| **Total** | | | **about 7–10 weeks** |

## 4. Why parallel AI agents do not make it 2 weeks

Parallel agents cut the coding time a lot: from about 20 weeks to a few weeks of agent work. They do not cut these steps:

1. **The foundation is serial.** Every other change depends on the `Organisation` model, the manager and the context. If agents start before it is stable, they all build on a moving base.
2. **Conflicts in large shared files.** `core/models.py` holds most models. Eight agents that each edit it cause merge conflicts. Wave 0 makes all model changes in one PR, so that later waves edit views, not models.
3. **Human review is the limit.** This is the most security-critical change in the app. One missed filter shows one yard's owners and invoices to another yard. A person must read each wave. The Launch plan still lists "no named reviewer" as a gap. Fix this first.
4. **Agents mark their own work as correct.** A test written by the same agent that wrote the code can miss the same case. Wave 3 adds guards that do not depend on any agent being right: fail-closed manager, RLS, and a two-organisation leak test across every page.
5. **Live data and outside parties.** The migration needs a staging rehearsal and a soak period. The pen test firm, the solicitor and Stripe's live-mode checks have their own lead times.

The honest range is **7 weeks** if review is fast and nothing surprising appears, and **10 weeks** if review takes 2–3 days for each wave or the staging rehearsal finds problems.

## 5. Running the platform after the build

### 5.1 New yards

1. The yard signs up at `<domain>/signup`: name, email, horse count.
2. C-gate creates the organisation in **trial** status, makes the person the administrator, and asks them to set up MFA.
3. Setup steps, in order: business details and VAT, sites and fields (Land App or RPA import), rates, horses and owners (import wizard), Xero link, invite staff, invite owners.
4. A checklist on the dashboard shows what is left. Support sees the same checklist.
5. Trial ends after 30 days. Stripe asks for a card or Direct Debit. With no payment method, the yard goes **read-only** for 14 days, then **suspended**.

Measure: days from sign-up to first invoice sent (activation). Target: under 14 days.

### 5.2 Releases and database changes

- One deployment serves every yard. Every release reaches every yard.
- Staging holds a copy of production with personal data anonymised. Every release runs there first.
- Database changes follow **expand, then contract**: add the new column, deploy code that writes both, backfill, then remove the old column in a later release. No release locks a large table.
- Risky features go behind a per-organisation feature flag (`core/features.py`). Turn them on for our yard first, then for pilot yards, then for all.
- Railway "Wait for CI" and branch protection stay on.

### 5.3 Monitoring

- Every log line, Sentry event and failed task carries `org_id`. Never the yard name in a log line.
- Dashboards per organisation: request errors, slow pages, task failures, Xero sync errors, email bounces.
- Alerts: any cross-organisation access refused by RLS is a **page-level alert**. It means a filter was missed, even though RLS stopped the leak.
- Noisy yards: rate limits per organisation on imports, exports and PDF generation. A large import runs as a background task.

### 5.4 Support

- Staff at C-gate use **support login** to see a yard as its administrator. Each use needs a reason, lasts at most 60 minutes, shows a banner, and writes an audit record that the yard can see.
- Support never edits money records (invoices, payments) in support login. The yard does this, or support uses a logged admin action.
- One help inbox, tagged by organisation.

### 5.5 Backups, restore and data rights

- The whole database is backed up as today, plus Supabase point-in-time recovery.
- **Restore one yard:** a command exports one organisation's rows and files to an encrypted archive, and imports it into a fresh organisation. Test this every quarter, as `BACKUP_RESTORE.md` does for the full database.
- **Leaving yard:** the yard downloads a full export (CSV and PDFs). C-gate keeps the data for 30 days, then deletes it. A deletion record goes to the audit log.
- Owner data requests (access, erasure) stay the yard's job. The yard is the controller and C-gate is the processor. The tools in `core/gdpr.py` work per organisation.

### 5.6 Billing for yards

- Stripe Billing holds the subscription. The plan comes from the horse count on the first day of each month.
- Add-ons (payments, breeding, maps, C-vet link) are Stripe subscription items.
- Failed payment: Stripe retries for 14 days and emails the yard. Then the yard goes read-only. C-gate never deletes data because of a failed payment.
- Owner payments (payments plan) use Stripe Connect. Each yard's money goes to its own Stripe account.

### 5.7 Security

- The two-organisation leak test runs in CI on every PR.
- Every query that uses `all_orgs` needs a code owner review (`CODEOWNERS`).
- Quarterly: access review of C-gate staff, support login log review, restore test.
- Yearly: external penetration test, update of the security questionnaire answers.
- Incidents: a status page, and email to the affected yards' administrators within 24 hours of a confirmed data incident. The ICO gets a report within 72 hours if personal data is at risk.

### 5.8 Legal

- Terms of service for yards.
- Data processing agreement for each yard, signed on sign-up.
- Public list of sub-processors (Railway, Supabase, storage, email, Sentry, Stripe, Xero).
- Data region: UK or EU for all providers. Check each one (Launch plan P1 dashboard item 8).

### 5.9 Weekly numbers

| Number | Why |
|---|---|
| New sign-ups, and activated yards | Shows if sign-up and setup work |
| Weekly active yards | Shows real use |
| Invoices sent and money collected through C-gate | The core value |
| Owners signed in | The growth loop (roadmap phase 2 and 4) |
| Yards invited by owners | The growth loop |
| Churned yards and the reason | Shows what to fix |
| Support tickets per yard | Shows where the product is hard |

### 5.10 When to scale further

| Trigger | Action |
|---|---|
| Database CPU above 60% at peak for a week | Larger database plan. Read replica for reports. |
| One yard is more than 20% of all rows | Check its indexes and queries. Consider its own database later. |
| More than 1,000 yards, or a customer requires it | Move that customer to a dedicated database with the same code (the `organisation` key makes this a copy, not a rebuild) |
| A non-UK customer | Region choice per organisation |

## 6. Decisions needed

1. **Who reviews each wave?** Without a named reviewer, the 7–10 weeks becomes longer, or the risk goes up.
2. **Start wave 0 now?** It is about 1 week and it does not change the app for users. It makes every later step cheaper.
3. **Web address per yard:** a subdomain per yard (`yourfarm.<domain>`), or one address with a yard switcher? Subdomains are clearer for owners. One address is simpler for staff who work at many yards. This plan supports both.
