"""Exploratory harness for reminder tasks. python manage.py shell < verify_reminders.py
Uses locmem email backend so we can inspect recipients without sending."""
from datetime import timedelta
from decimal import Decimal
from django.test import override_settings
from django.core import mail
from django.utils import timezone
from core.models import Owner, Location, Horse, RateType, OwnershipShare, Placement, BusinessSettings
from health.models import VaccinationType, Vaccination, FarrierVisit, BreedingRecord
from invoicing.models import Invoice

today = timezone.now().date()
print("today =", today)

# --- clean slate for the models we touch ---
Vaccination.objects.all().delete(); FarrierVisit.objects.all().delete()
BreedingRecord.objects.all().delete(); Invoice.objects.all().delete()

BusinessSettings.get_settings()
loc = Location.objects.first() or Location.objects.create(site="S", name="F")
grass = RateType.objects.filter(name="Grass").first() or RateType.objects.create(name="Grass", daily_rate=Decimal("5"))

def owner(name, email="x@example.com"):
    return Owner.objects.create(name=name, email=email)

def horse_with_owner(name, o, active=True):
    h = Horse.objects.create(name=name, is_active=active)
    OwnershipShare.objects.create(horse=h, owner=o, share_percentage=100, is_primary_contact=True)
    return h

def run(task):
    with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
        mail.outbox = []
        result = task()
        return result, [(m.subject, tuple(m.to)) for m in mail.outbox]

vt = VaccinationType.objects.create(name="Flu", interval_months=12, reminder_days_before=30)

print("\n=== VACCINATION ===")
o_due = owner("Vax Due"); h_due = horse_with_owner("VaxDue", o_due)
Vaccination.objects.create(horse=h_due, vaccination_type=vt, date_given=today-timedelta(days=345), next_due_date=today+timedelta(days=20))  # reminder_date = due-30 = 10d ago -> FIRE
o_early = owner("Vax Early"); h_early = horse_with_owner("VaxEarly", o_early)
Vaccination.objects.create(horse=h_early, vaccination_type=vt, date_given=today, next_due_date=today+timedelta(days=40))  # reminder_date=+10 -> NOT yet
o_ov = owner("Vax Overdue"); h_ov = horse_with_owner("VaxOverdue", o_ov)
Vaccination.objects.create(horse=h_ov, vaccination_type=vt, date_given=today-timedelta(days=370), next_due_date=today-timedelta(days=5))  # overdue -> FIRE?
o_inact = owner("Vax Inactive"); h_inact = horse_with_owner("VaxInactive", o_inact, active=False)
Vaccination.objects.create(horse=h_inact, vaccination_type=vt, date_given=today, next_due_date=today+timedelta(days=5))  # inactive -> excluded
from notifications.tasks import (send_vaccination_reminders, send_farrier_reminders,
    send_overdue_invoice_reminders, send_ehv_reminders, check_invoice_status)
print(run(send_vaccination_reminders))

print("\n=== FARRIER ===")
o_f_due = owner("Far Due"); h_f_due = horse_with_owner("FarDue", o_f_due)
FarrierVisit.objects.create(horse=h_f_due, date=today-timedelta(days=30), work_done="full_set", next_due_date=today+timedelta(days=10))  # in window -> FIRE
o_f_far = owner("Far Future"); h_f_far = horse_with_owner("FarFuture", o_f_far)
FarrierVisit.objects.create(horse=h_f_far, date=today, work_done="full_set", next_due_date=today+timedelta(days=20))  # outside 14d -> NOT
o_f_ov = owner("Far Overdue"); h_f_ov = horse_with_owner("FarOverdue", o_f_ov)
FarrierVisit.objects.create(horse=h_f_ov, date=today-timedelta(days=50), work_done="full_set", next_due_date=today-timedelta(days=5))  # OVERDUE -> gap?
print(run(send_farrier_reminders))

print("\n=== OVERDUE INVOICE ===")
o_inv = owner("Inv Owner");
inv = Invoice.objects.create(owner=o_inv, invoice_number="RMD001", period_start=today-timedelta(days=60), period_end=today-timedelta(days=40), due_date=today-timedelta(days=10), status=Invoice.Status.SENT, total=Decimal("100"))
o_noemail = Owner.objects.create(name="Inv NoEmail", email="")
inv2 = Invoice.objects.create(owner=o_noemail, invoice_number="RMD002", period_start=today-timedelta(days=60), period_end=today-timedelta(days=40), due_date=today-timedelta(days=10), status=Invoice.Status.OVERDUE, total=Decimal("50"))
o_paid = owner("Inv Paid");
Invoice.objects.create(owner=o_paid, invoice_number="RMD003", period_start=today-timedelta(days=60), period_end=today-timedelta(days=40), due_date=today-timedelta(days=10), status=Invoice.Status.PAID, total=Decimal("70"))
print("run 1:", run(send_overdue_invoice_reminders))
print("run 2 (same day, should be throttled):", run(send_overdue_invoice_reminders))
inv.refresh_from_db(); print("inv1 last_overdue_reminder_at set:", inv.last_overdue_reminder_at is not None)
inv2.refresh_from_db(); print("inv2 (no-email) last_overdue_reminder_at (should be None, rolled back):", inv2.last_overdue_reminder_at)
# simulate 8 days passing
Invoice.objects.filter(pk=inv.pk).update(last_overdue_reminder_at=timezone.now()-timedelta(days=8))
print("run 3 (8 days later, should re-send):", run(send_overdue_invoice_reminders))

print("\n=== EHV ===")
o_m = owner("Mare Owner"); mare = Horse.objects.create(name="EHVMare", sex="mare", is_active=True)
OwnershipShare.objects.create(horse=mare, owner=o_m, share_percentage=100, is_primary_contact=True)
# covering date so that month-5 EHV due is ~today (within window). ehv months are 5,7,9 from covering.
br = BreedingRecord.objects.create(mare=mare, stallion_name="Sire", date_covered=today - timedelta(days=30*5), status='confirmed')
print("ehv dates:", br.ehv_vaccination_dates)
print("run 1:", run(send_ehv_reminders))
br.refresh_from_db(); print("sent months:", br.ehv_reminders_sent)
print("run 2 (should skip already-sent):", run(send_ehv_reminders))

print("\n=== check_invoice_status ===")
print(check_invoice_status())
inv.refresh_from_db(); print("inv1 status now:", inv.status)
