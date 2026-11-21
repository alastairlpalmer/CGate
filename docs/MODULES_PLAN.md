# Modules plan: separate the code and the interfaces

Date: 18 November 2026. Status: proposal.

## Summary

- C-gate will serve different businesses (grass livery, full livery, studs, racing yards, and owner and syndicate managers) and different people (yard staff, owners, suppliers). This needs clear lines inside the code.
- **Keep one app and one database** (a "modular monolith"). Do not split into microservices. Do not build a separate app for each yard type.
- Separate on **three axes**:
  1. **Who uses it** → separate interfaces (yard office, owner portal, supplier portal, platform admin).
  2. **What kind of business** → editions: presets of modules and words. Not separate code.
  3. **What it does** → modules: Django apps with strict rules about what each one may use.
- Do steps 1–3 **before** the multi-tenancy build ([`MULTI_TENANCY_PLAN.md`](MULTI_TENANCY_PLAN.md)). Its parallel agents work one app each, and today half the code is in one app.
- Total: about **6–7 weeks elapsed** with parallel agents and one reviewer.

## 1. The code today

| App | Lines (no tests, no migrations) | Templates | Note |
|---|---|---|---|
| `core` | 25,433 | Most of the 256, in the shared `templates/` folder | About half the app. Horses, owners, places, stables, turnout, users, roles, dashboard, settings, documents, exports. |
| `health` | 6,591 | 50 | Care and breeding together |
| `invoicing` | 4,420 | 14 | |
| `cvet` | 3,511 | 11 | Already separate: own users, own data rules, own middleware |
| `billing` | 2,356 | 15 | |
| `xero_integration` | 1,555 | 2 | |
| `notifications` | 1,093 | 12 | |

### Problems

1. **The base depends on the modules above it.** 18 files in `core` import from `invoicing`, `health`, `billing`, `notifications`, `xero_integration` or `cvet`. The worst are the dashboard (`core/dashboard/*`, `core/views/dashboard.py`), finances, exports, GDPR and `core/models.py`. You cannot change or remove one module without touching the base.
2. **Access is per role, not per yard.** `core/features.py` lists 16 feature areas. A role can hide an area. Nothing says "this yard has breeding, that yard does not".
3. **Templates are mostly in one shared folder.** It is not clear which module owns which page.

### What already works

- The farrier login and the C-vet login each stay on their own pages (`ProviderPortalMiddleware`, `VetPortalMiddleware`). This is the pattern for the owner portal.
- `features.py` already has the right idea: one list of areas, used by menus, permissions and the role editor.
- The shared design parts (`templates/includes`, `templates/partials`, Tailwind) can serve every interface.

## 2. Target structure

```
INTERFACES (one per audience; each has its own layout, menu, URL prefix, login rule)
  Yard office  │  Owner portal  │  Supplier portal (farrier, C-vet)  │  Platform admin
─────────────────────────────────────────────────────────────────────────────────────
COMPOSITION (may use any module; nothing uses it)
  Dashboard  │  Menus  │  Search  │  Exports  │  GDPR tools
─────────────────────────────────────────────────────────────────────────────────────
MODULES (each can be on or off for a yard)
  Places & maps │ Stables & turnout │ Care │ Breeding │ Racing │ Syndicates
  Charges │ Invoicing │ Payments │ Accounting links │ Messages & updates │ Staff tasks
─────────────────────────────────────────────────────────────────────────────────────
BASE (always on)
  Organisation │ Horse │ Owner & shares │ People, roles & access │ Audit │ Files │ Jobs
```

### Rules

1. **A module may use the base. The base never uses a module.**
2. **A module does not import another module's models.** It calls that module's service functions, or it listens for an event. Example: Care sends "vet visit saved". Charges listens and makes the charge (roadmap item 1.1).
3. **The composition layer** may use any module. It builds the dashboard, menus, search and exports from parts that each module registers.
4. **Interfaces** contain pages and layouts only. Business rules live in modules.
5. A CI check (`import-linter`) enforces rules 1–3. It starts in report-only mode, then blocks PRs.

### Module list

| Module | Django app (target) | Today | Depends on |
|---|---|---|---|
| Base | `core` (smaller) | `core` | — |
| Places & maps | `places` | `core` (locations, boundaries, maps) | Base |
| Stables & turnout | `stables` | `core` (stables, turnout) | Base, Places |
| Care | `care` | `health` (vaccinations, farrier, worming, vet visits, conditions) | Base |
| Breeding | `breeding` | `health` (coverings, scans, foaling) | Base, Care (by service) |
| Charges | `billing` | `billing` | Base |
| Invoicing | `invoicing` | `invoicing` | Base, Charges (by service) |
| Payments | `payments` | Not built | Base, Invoicing (by service) |
| Accounting links | `xero_integration` | `xero_integration` | Base, Invoicing (by service) |
| Messages & updates | `notifications` | `notifications` | Base |
| Staff tasks | `tasks` | Not built | Base |
| Racing | `racing` | Not built | Base, Care, Charges (by service) |
| Syndicates (owner manager) | `syndicates` | Not built. `OwnershipShare` covers simple shares today. | Base, Charges, Invoicing and Messages (by service) |
| C-vet | `cvet` | `cvet` | Base. It keeps its own data owner, the practice. |

Moving `health` into `care` and `breeding` is optional. Do it only if breeding grows. The rules matter more than the folder names.

## 3. Module registry

Each module declares itself in one file, `<app>/module.py`:

| Field | Example |
|---|---|
| Key, name, description | `breeding`, "Breeding", "Coverings, scans, foaling" |
| Always on? | Base: yes. Breeding: no. |
| Pages and menu items, per interface | Yard office: "Breeding" under "Manage". Owner portal: "Foals". |
| Dashboard widgets | "Mares due to foal" |
| Settings page | Breeding settings |
| Permission areas and levels | Replaces the hand-written entries in `features.py` |
| Setup steps for a new yard | "Add your mares" |
| Events it sends and listens for | Sends `foal_born`. Listens for nothing. |
| Price plans that include it | Stud plan; add-on for others |

A page shows only when **both** are true:
- the yard has the module switched on (per organisation), and
- the user's role allows the area (as today).

`features.py` then builds its list from the registry. The role editor, menus and permission checks keep working.

## 4. Editions (yard types)

An edition is a preset. It sets the modules, the words and the setup steps for a new yard. A yard can add or remove modules later. An edition is not separate code.

| Edition | Modules on | Words |
|---|---|---|
| Grass livery | Places & maps, Care, Charges, Invoicing, Messages, Owner portal | Field, livery, owner |
| Full livery | Grass livery + Stables & turnout, Staff tasks | Stable, livery |
| Stud | Full livery + Breeding | Mare, covering, foal |
| Racing yard | Stables & turnout, Care, Charges, Invoicing, Messages, Racing, Owner portal | Box, training fees, trainer, lot, syndicate |
| Owner manager (syndicates, racing clubs) | Syndicates, Messages, Charges, Invoicing, Owner portal. Racing in read-only mode. | Syndicate, member, share, cash call, distribution |

Words come from one table per edition, used by templates through a template tag. Example: `{% term "field" %}` shows "Field" for livery and "Paddock" for an Australian yard.

## 5. Interfaces

| Interface | Users | URL | Layout and menu | Login rule |
|---|---|---|---|---|
| Yard office | Yard staff | `/` | Current layout. Menu from the registry. | Membership of the yard, MFA for administrators |
| Owner portal | Owners and syndicate members | **One shared address for all yards**, for example `my.<domain>` | Phone first. Short menu: horses, updates, races, invoices, documents. | Owner account that sits above yards. Sees only horses where the owner has a share, at every yard and syndicate they use. |
| Supplier portal | Farriers, dentists, physios | `/supplier/` | Today's farrier page, extended | Supplier account, per yard link |
| C-vet | Vet practices | `/cvet/` | As today | Practice membership |
| Platform admin | C-gate staff | `/platform/` | Yards, plans, support login | C-gate staff only, MFA, audit |

Each interface has its own base template that extends one shared shell. All use the same design parts. One middleware decides which interface a user may enter. This replaces the two portal middlewares with one rule table.

## 6. Order of work

| Step | Work | Parallel? | Size with agents |
|---|---|---|---|
| 1 | **Module map and checks.** Agree the module list above. Add `import-linter` with the rules in report-only mode. List today's breaks. | No | 2–3 days |
| 2 | **Dashboard out of `core`.** Move `core/dashboard/*`, finances, exports and GDPR tools into a composition app. Modules register widgets and export parts. This removes most of the 18 bad imports. | Partly | 1 week |
| 3 | **Split `core`.** Move places and maps, and stables and turnout, into their own apps. **Keep the database table names** (`db_table` and `SeparateDatabaseAndState` migrations), so no live data moves. Rehearse on a copy of the live database. | Yes, one agent per new app, after one agent does the migration pattern | 2 weeks |
| 4 | **Module registry and per-yard switches.** `module.py` in each app. Organisation-level on/off (one row per yard until multi-tenancy, then per organisation). `features.py` built from the registry. | Yes, one agent per module | 1 week |
| 5 | **Interfaces.** One rule table for which user enters which interface. Separate base templates. Move templates into their app folders. Turn `import-linter` to blocking. | Yes | 1–1.5 weeks |
| 6 | **Editions.** Presets and the words table. Edition choice in setup. | No | 3–4 days |
| **Total** | | | **about 6–7 weeks** |

Then new modules start inside these lines from the first line of code: owner portal (roadmap phase 2), payments (phase 3), staff tasks, racing.

### How this fits with the other plans

```
Now ──► Modules steps 1–3 ──► Multi-tenancy wave 0 ──► Modules steps 4–6 ──► Multi-tenancy waves 1–5
          (clean lines)         (organisation key)       (switches per yard,     (agents work one
                                                          interfaces, editions)    module each)
```

- Roadmap phase 1 (charge rules, invoice checks, QR cards, welfare flags) can run beside steps 1–3. Build charge rules (1.1) with the event pattern in rule 2.
- Step 4 gives the module switches that Stripe Billing needs for add-ons (multi-tenancy wave 4).

## 6a. Racing and syndicate modules

Decided on 18 November 2026: racing yards and owner managers are in the product. Build both as modules after steps 1–5, so they start inside the new lines.

### Racing module (`racing`)

| Part | What it does | Reuses |
|---|---|---|
| Horses in training | Status: in training, spelling (out at grass), injured, retired. Box and barn. | Stables & turnout, Places |
| Training fees | Daily training fee by horse, plus extras | Placements and day rates, Charges |
| Trackwork | Daily lot list. Who rode, what work, notes. On a phone. | Base, Messages for owner updates |
| Race entries and results | Upcoming entries, declarations and results for the yard's horses. Read-only feed. | Weatherbys data licence (check terms and cost first). Manual entry as a fallback. |
| Race reports | Pre-race and post-race reports, video links, sent to owners | Messages & updates (roadmap phase 2) |
| Prize money | Record prize money. Split by ownership share after the trainer's and jockey's percentages. | Charges, Invoicing (credit lines) |

Not in scope: making entries and declarations inside C-gate. Those stay in Weatherbys' own system unless an API allows them.

### Syndicates module (`syndicates`), the "owner manager"

| Part | What it does | Reuses |
|---|---|---|
| Ownership groups | A syndicate or racing club owns one or more horses. Many members, each with a share. Member register with joining and leaving dates. | Owner & shares (base) |
| Forward updates | One click forwards the trainer's update to all members | Messages & updates |
| Cash calls | Ask members for their share of costs in advance | Invoicing, Payments |
| Distributions | Share prize money and sale proceeds by share | Charges, Invoicing |
| Member statements | Monthly statement per member | Invoicing |
| Manager login | A syndicate manager works across the yards where the group's horses are trained | Membership (multi-tenancy plan), owner account above yards |

## 7. Risks

| Risk | Control |
|---|---|
| Step 3 moves models between apps and could rename or drop tables | Keep `db_table`. Use state-only migrations. Rehearse on a copy of live data. Check with `schema_check`. |
| Events hide the flow of work. Bugs are harder to trace. | Few events, each named and listed in the registry. Log each event with `org_id`. Tests per event. |
| Too many small modules | Split only where a yard type needs to switch a module off, or where a module has its own users |
| The work stops feature work | Steps are small PRs on `main`. Roadmap phase 1 runs in parallel. |

## 8. Decisions

Made on 18 November 2026:

1. **Racing: yes.** Racing yards and owner managers (syndicates, racing clubs) are in the product as modules and editions. See section 6a.
2. **Split `health`: later.** Keep care and breeding in `health` until breeding grows.
3. **Owner portal address: one shared address** for all owners, for example `my.<domain>`. The owner account sits above yards from the start.

Still open:

4. **Weatherbys data.** Ask Weatherbys for the terms and cost of a data licence for entries and results before the racing build starts.
