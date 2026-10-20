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

## What this does not reach

- **Xero.** A separate system with its own retention. Handle it by hand.
- **Emails already sent**, in the yard's mailbox and in the email provider's logs.
- **Backups**, until they age out (12 months at most).
- **Sentry and PostHog.** Sentry is set not to send personal data
  (`core/monitoring.py`). PostHog holds staff usage events, not owner data.
