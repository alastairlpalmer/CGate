# Data retention

What Yardway keeps, for how long, and how it is removed. Give this to the
solicitor writing the privacy policy: the policy must say the same thing.

The code that applies these rules is `horse_management/core/gdpr.py`,
`core/tasks.py` and `core/softdelete.py`. The periods are settings, so a
deployment can change them without a code change.

## Retention schedule

| Data | Kept for | Why | How it is removed |
|---|---|---|---|
| Owner name and postal address | Until 6 years after their newest invoice (`OWNER_RETENTION_YEARS`) | HMRC requires invoices, which show them, to be kept for six years | The weekly retention job replaces the name with "Former owner #N" and blanks the address, in the live row and in the record history. Only archived owners. |
| Owner email, phone, notes, account code | While the owner is a client. On request, sooner. | Needed to send invoices and reminders | **Erase** on the owner's page (archived owners). Also removed by the retention job. |
| Owner documents (passports, insurance) | While the owner is a client. On request, sooner. | Needed for the livery agreement | Deleted with their files by **Erase** and by the retention job. |
| An owner with no invoices | Until erased, or 6 years after archiving | Nothing needs to be kept | **Erase** removes everything at once. |
| Invoices, payments, charges | 6 years | HMRC | Kept. After 6 years they no longer name the owner (see first row). |
| Deleted charges, yard costs, documents | 30 days in Recently Deleted (`SOFT_DELETE_RETENTION_DAYS`) | Recover mistakes | Nightly purge, files included. |
| Voided payments | As invoices | Accounting record | Kept. |
| Record history (invoices, payments, owners, roles, business settings) | As the record | Audit trail | Owner history is cleared with the owner (rows above). |
| Sign-in and account events | 2 years (`AUDIT_EVENT_RETENTION_DAYS`) | Security investigation | Weekly retention job. |
| Staff accounts | While employed | Access | Deactivate on leaving; delete when no longer needed. |
| Linked Google account (Google's account ID, Google email, Workspace domain, date last used) | While the link exists | Sign in with Google | The user disconnects it on Account Security, or an administrator does on Settings → Users. Deleted with the staff account. No Google token is kept. |
| Farrier login (Farrier role) | While the farrier works for the yard | The farrier page | Remove the login from the provider (Settings → Service providers), then deactivate the user. The farrier's visits, prices and records stay, as the yard's farrier records. |
| Farrier notes and the yard's list for the farrier | As the horse record | Advice on the horse; the list is the yard's request | Kept with the horse. Deleted with the horse. |
| Farrier visits, lines and the farrier's prices | As invoices (6 years) | The lines are the source of owners' charges and of the yard's payment to the farrier | Kept. A line on a live invoice cannot be changed from the farrier page. |
| Off-site backups | 7 daily, 4 weekly, 12 monthly | Disaster recovery | Age out under the backup retention (`core/backup/retention.py`). Erased data is gone from all backups within 12 months. |

## A subject access request

1. Open the owner's page.
2. **Personal Data → Download**. The ZIP holds every record about them,
   their earlier contact details and their documents.
3. Send it to them by a secure route. The download is recorded in the
   audit log (without their name).

## A request to be forgotten

1. Make sure nothing is owed and no horse is on the yard under them.
2. Archive the owner.
3. **Personal Data → Erase**.
   - With invoices: email, phone, notes, account code and documents go now,
     including earlier copies. Name and address stay on the invoices until
     the retention period ends, then go automatically. Tell the person this:
     UK GDPR article 17(3)(b) allows it.
   - Without invoices: everything goes now.
4. **Outside Yardway, by hand:** delete or archive their contact in Xero,
   and delete emails to and from them in the yard's mailbox.

## Who outside the yard sees personal data

| Recipient | What they see | What they never see | Where |
|---|---|---|---|
| The yard's farrier (Farrier role, `farrier_portal`) | Horse names, profile photos, field names, farrier due dates, their own visits and prices | Owners (names, contact details, what they owe), invoices, vet and health records, other farriers' data | `/farrier/` only. `core.middleware.ProviderPortalMiddleware` sends every other page back to `/farrier/`. Their summary email after a visit lists horses and prices, never owners. |

A farrier login stays signed in for up to 30 days of no use
(`PROVIDER_SESSION_IDLE_AGE`), so the farrier does not type a password at
the yard. Every session still ends 30 days after sign-in
(`SESSION_ABSOLUTE_AGE`). The QR code on the yard door holds only the page
address. Scanning it, a farrier picks their name and is signed in with no
password (docs/FARRIER_PAGE_PLAN.md, section 15): anyone with the code can do
the same, so they could see horse names, photos and fields. Each such sign-in
is in the audit log.

Give the farrier a short written note before the first visit: what they
can see, that they must not share it, and that the yard can end the access
at any time (docs/FARRIER_PAGE_PLAN.md, section 9).

## What this does not reach

- **Xero.** A separate system with its own retention. Handle it by hand.
- **Emails already sent**, in the yard's mailbox and in the email provider's logs.
- **Backups**, until they age out (12 months at most).
- **Sentry and PostHog.** Sentry is set not to send personal data
  (`core/monitoring.py`). PostHog holds staff usage events, not owner data.
