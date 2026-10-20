"""Plain-text parts, unsubscribe links, preferences and bounce webhooks.
docs/LAUNCH_READINESS_PLAN.md, P1 work package 6."""

import base64
import hashlib
import hmac
import json
import time
from datetime import date, timedelta
from decimal import Decimal

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import BusinessSettings, Horse, Owner
from core.roles_testutils import make_admin
from health.models import Vaccination, VaccinationType
from invoicing.models import Invoice
from notifications.emails import send_invoice_email, send_vaccination_digest
from notifications.mail import SKIPPED, html_to_text, unsubscribe_token

RESEND_SECRET = 'whsec_' + base64.b64encode(b'test-signing-key-123').decode()


def _owner(**extra):
    data = dict(name="Siobhán O'Brien", email='siobhan@example.com')
    data.update(extra)
    return Owner.objects.create(**data)


def _vaccination(owner):
    horse = Horse.objects.create(name='Mail Horse')
    from core.models import OwnershipShare
    OwnershipShare.objects.create(horse=horse, owner=owner, share_percentage=100)
    vtype = VaccinationType.objects.create(name='Flu')
    return Vaccination.objects.create(
        horse=horse, vaccination_type=vtype,
        date_given=date.today() - timedelta(days=350), next_due_date=date.today() + timedelta(days=10),
    )


def _invoice(owner):
    return Invoice.objects.create(
        owner=owner, invoice_number='MAIL-1', period_start=date(2026, 3, 1), period_end=date(2026, 3, 31),
        subtotal=Decimal('10'), total=Decimal('10'), status=Invoice.Status.SENT,
    )


class PlainTextTests(TestCase):
    def test_html_to_text(self):
        text = html_to_text(
            '<html><head><style>p{color:red}</style></head><body>'
            '<p>Dear O&#x27;Brien,</p><p>Pay <a href="https://pay.example/1">here</a>.<br>Thanks</p>'
            '<table><tr><td>Total</td><td>&pound;10.00</td></tr></table></body></html>'
        )
        self.assertNotIn('<', text)
        self.assertNotIn('color:red', text)
        self.assertIn("Dear O'Brien,", text)
        self.assertIn('here (https://pay.example/1)', text)
        self.assertIn('£10.00', text)

    def test_every_email_has_text_and_html(self):
        BusinessSettings.get_settings()
        owner = _owner()
        send_invoice_email(_invoice(owner))
        send_vaccination_digest(owner, [_vaccination(owner)])
        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertNotIn('<p', message.body)
            self.assertTrue(message.body.strip())
            self.assertEqual(message.alternatives[0][1], 'text/html')


@override_settings(SITE_URL='https://yard.example')
class UnsubscribeTests(TestCase):
    def setUp(self):
        BusinessSettings.get_settings()
        self.owner = _owner()

    def test_reminder_carries_unsubscribe_headers_and_link(self):
        send_vaccination_digest(self.owner, [_vaccination(self.owner)])
        message = mail.outbox[0]
        url = f'https://yard.example/email/unsubscribe/{unsubscribe_token(self.owner)}/'
        self.assertEqual(message.extra_headers['List-Unsubscribe'], f'<{url}>')
        self.assertEqual(message.extra_headers['List-Unsubscribe-Post'], 'List-Unsubscribe=One-Click')
        self.assertIn(url, message.alternatives[0][0])

    def test_invoices_carry_no_unsubscribe(self):
        send_invoice_email(_invoice(self.owner))
        self.assertNotIn('List-Unsubscribe', mail.outbox[0].extra_headers)

    @override_settings(SITE_URL='', ALLOWED_HOSTS=['localhost'])
    def test_no_header_when_the_site_address_is_unknown(self):
        send_vaccination_digest(self.owner, [_vaccination(self.owner)])
        self.assertNotIn('List-Unsubscribe', mail.outbox[0].extra_headers)

    def test_page_asks_then_turns_off(self):
        url = reverse('email_unsubscribe', args=[unsubscribe_token(self.owner)])
        page = self.client.get(url)
        self.assertContains(page, 'Turn off reminders')
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.email_health_reminders)  # GET changes nothing
        self.client.post(url)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.email_health_reminders)

    def test_one_click_post_from_a_mail_client(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            reverse('email_unsubscribe', args=[unsubscribe_token(self.owner)]),
            {'List-Unsubscribe': 'One-Click'},
        )
        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.email_health_reminders)

    def test_forged_token_is_refused(self):
        response = self.client.post(reverse('email_unsubscribe', args=[f'{self.owner.pk}:forged']))
        self.assertEqual(response.status_code, 404)

    def test_turned_off_owner_gets_no_reminders_but_still_invoices(self):
        self.owner.email_health_reminders = False
        self.owner.save()
        self.assertEqual(send_vaccination_digest(self.owner, [_vaccination(self.owner)]), SKIPPED)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(send_invoice_email(_invoice(self.owner)))


@override_settings(EMAIL_WEBHOOK_TOKEN='tok-123', RESEND_WEBHOOK_SECRET=RESEND_SECRET)
class BounceWebhookTests(TestCase):
    def setUp(self):
        BusinessSettings.get_settings()
        self.owner = _owner()

    def _postmark(self, payload, token='tok-123'):
        return self.client.post(
            reverse('email_events_postmark', args=[token]), json.dumps(payload), content_type='application/json',
        )

    def _resend(self, payload, stamp=None, key=b'test-signing-key-123'):
        body = json.dumps(payload).encode()
        stamp = str(int(stamp if stamp is not None else time.time()))
        sig = base64.b64encode(hmac.new(key, f'msg_1.{stamp}.'.encode() + body, hashlib.sha256).digest()).decode()
        return self.client.post(
            reverse('email_events_resend'), body, content_type='application/json',
            HTTP_SVIX_ID='msg_1', HTTP_SVIX_TIMESTAMP=stamp, HTTP_SVIX_SIGNATURE=f'v1,{sig}',
        )

    def _flagged(self):
        self.owner.refresh_from_db()
        return self.owner.email_bounced_at is not None

    def test_postmark_hard_bounce_flags_the_owner(self):
        response = self._postmark({'RecordType': 'Bounce', 'Type': 'HardBounce', 'Email': 'SIOBHAN@example.com',
                                   'Description': 'Mailbox does not exist'})
        self.assertEqual(response.status_code, 204)
        self.assertTrue(self._flagged())
        self.assertEqual(self.owner.email_bounce_reason, 'Mailbox does not exist')

    def test_postmark_soft_bounce_is_ignored(self):
        self._postmark({'RecordType': 'Bounce', 'Type': 'SoftBounce', 'Email': 'siobhan@example.com'})
        self.assertFalse(self._flagged())

    def test_postmark_spam_complaint_flags(self):
        self._postmark({'RecordType': 'SpamComplaint', 'Email': 'siobhan@example.com'})
        self.assertTrue(self._flagged())

    def test_postmark_wrong_token_is_refused(self):
        self.assertEqual(self._postmark({'RecordType': 'SpamComplaint', 'Email': 'siobhan@example.com'}, token='nope').status_code, 403)
        self.assertFalse(self._flagged())

    def test_resend_signed_bounce_flags(self):
        response = self._resend({'type': 'email.bounced', 'data': {'to': ['siobhan@example.com'], 'bounce': {'type': 'Permanent'}}})
        self.assertEqual(response.status_code, 204)
        self.assertTrue(self._flagged())

    def test_resend_transient_bounce_is_ignored(self):
        self._resend({'type': 'email.bounced', 'data': {'to': ['siobhan@example.com'], 'bounce': {'type': 'Transient'}}})
        self.assertFalse(self._flagged())

    def test_resend_bad_signature_and_old_timestamp_are_refused(self):
        payload = {'type': 'email.complained', 'data': {'to': ['siobhan@example.com']}}
        self.assertEqual(self._resend(payload, key=b'wrong-key').status_code, 403)
        self.assertEqual(self._resend(payload, stamp=time.time() - 3600).status_code, 403)
        self.assertFalse(self._flagged())

    def test_bounced_address_gets_nothing_until_fixed(self):
        self.owner.email_bounced_at = timezone.now()
        self.owner.save()
        self.assertFalse(send_invoice_email(_invoice(self.owner)))
        self.assertEqual(send_vaccination_digest(self.owner, [_vaccination(self.owner)]), SKIPPED)
        self.assertEqual(len(mail.outbox), 0)

    def test_owner_page_flags_it_and_it_can_be_cleared(self):
        self.owner.email_bounced_at = timezone.now()
        self.owner.email_bounce_reason = 'Mailbox full'
        self.owner.save()
        self.client.force_login(make_admin('bounceadmin'))
        page = self.client.get(reverse('owner_detail', args=[self.owner.pk]))
        self.assertContains(page, 'not arriving')
        self.client.post(reverse('owner_email_ok', args=[self.owner.pk]))
        self.assertFalse(self._flagged())

    def test_changing_the_address_clears_the_flag(self):
        self.owner.email_bounced_at = timezone.now()
        self.owner.save()
        self.client.force_login(make_admin('bounceadmin2'))
        self.client.post(reverse('owner_update', args=[self.owner.pk]), {
            'name': self.owner.name, 'email': 'new@example.com', 'phone': '', 'address': '',
            'account_code': '', 'invoice_reference': '', 'email_health_reminders': 'on', 'notes': '',
        })
        self.assertFalse(self._flagged())
