"""Retries, the failed-task record, once-a-day scheduled tasks and the
health check. docs/LAUNCH_READINESS_PLAN.md, P1 work package 7."""

from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from celery.exceptions import Retry
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import jobs
from core.models import BusinessSettings, Document, FailedTask, Owner, ScheduledRun
from core.roles_testutils import make_admin, make_user_with_access
from core.tasks import purge_soft_deleted
from invoicing.models import Invoice
from invoicing.tasks import claim_invoice_for_sending, send_invoice_email_task
from notifications.tasks import check_invoice_status, send_document_expiry_reminders

CHECK_STATUS = 'notifications.tasks.check_invoice_status'


class ScheduledOnceADayTests(TestCase):
    def test_second_copy_the_same_day_does_nothing(self):
        first = check_invoice_status.apply()
        second = check_invoice_status.apply()
        self.assertTrue(first.result.startswith('Updated'))
        self.assertEqual(second.result, jobs.ALREADY_RAN)
        run = ScheduledRun.objects.get(task_name=CHECK_STATUS)
        self.assertEqual(run.status, ScheduledRun.Status.SUCCESS)
        self.assertEqual(run.run_key, timezone.localdate().isoformat())

    def test_the_same_message_redelivered_runs_again(self):
        # A worker died mid-task; the broker hands the same id back.
        self.assertIsNotNone(jobs.claim_scheduled_run(CHECK_STATUS, 'id-1'))
        self.assertIsNotNone(jobs.claim_scheduled_run(CHECK_STATUS, 'id-1'))
        self.assertIsNone(jobs.claim_scheduled_run(CHECK_STATUS, 'id-2'))

    def test_plain_call_takes_no_claim(self):
        check_invoice_status()
        check_invoice_status()
        self.assertFalse(ScheduledRun.objects.exists())

    def test_a_failed_run_can_run_again_the_same_day(self):
        with mock.patch('notifications.tasks.Invoice.objects.filter', side_effect=ValueError('boom')):
            check_invoice_status.apply(throw=False)
        run = ScheduledRun.objects.get(task_name=CHECK_STATUS)
        self.assertEqual(run.status, ScheduledRun.Status.FAILED)
        failure = FailedTask.objects.get(task_name=CHECK_STATUS)
        self.assertIn('ValueError: boom', failure.error)
        self.assertEqual(failure.retries, 0)  # A bug: not retried.

        again = check_invoice_status.apply()
        self.assertTrue(again.result.startswith('Updated'))
        run.refresh_from_db()
        self.assertEqual(run.status, ScheduledRun.Status.SUCCESS)

    @override_settings(CRON_HEARTBEAT_URL='https://hc.example/ping/{task}')
    def test_heartbeat_ping_after_success(self):
        with mock.patch('requests.get') as get:
            check_invoice_status.apply()
        get.assert_called_once_with('https://hc.example/ping/check_invoice_status', timeout=5)


class RetryTests(TestCase):
    """Eager mode does not run a retry, so each try is driven with
    ``apply(retries=n)``, which is what the worker does on redelivery."""

    def test_connection_error_asks_for_a_retry(self):
        with mock.patch('notifications.tasks.Invoice.objects.filter',
                        side_effect=ConnectionRefusedError('database went away')):
            with self.assertRaises(Retry):
                check_invoice_status.apply(task_id='try-1')
        self.assertFalse(FailedTask.objects.exists())
        # The retry keeps the day's claim and succeeds.
        retry = check_invoice_status.apply(task_id='try-1', retries=1)
        self.assertTrue(retry.result.startswith('Updated'))

    def test_last_try_failing_is_recorded(self):
        with mock.patch('notifications.tasks.Invoice.objects.filter',
                        side_effect=ConnectionRefusedError('down')):
            result = check_invoice_status.apply(retries=3, throw=False)
        self.assertEqual(result.state, 'FAILURE')
        failure = FailedTask.objects.get()
        self.assertEqual(failure.retries, 3)
        self.assertIn('down', failure.error)


class InvoiceSendRetryTests(TestCase):
    def setUp(self):
        BusinessSettings.get_settings()
        self.owner = Owner.objects.create(name='Retry Owner', email='retry@example.com')
        self.invoice = Invoice.objects.create(
            owner=self.owner, invoice_number='RTY-1', period_start=date(2026, 3, 1),
            period_end=date(2026, 3, 31), subtotal=Decimal('10'), total=Decimal('10'),
        )
        self.assertTrue(claim_invoice_for_sending(self.invoice.pk))

    def test_worker_tries_again_after_a_failed_send(self):
        with mock.patch('notifications.emails.send_invoice_email', return_value=False):
            with self.assertRaises(Retry):
                send_invoice_email_task.apply(args=[self.invoice.pk])
        self.invoice.refresh_from_db()
        self.assertIsNotNone(self.invoice.send_queued_at)  # Still claimed.
        self.assertEqual(self.invoice.send_error, '')
        with mock.patch('notifications.emails.send_invoice_email', return_value=True):
            send_invoice_email_task.apply(args=[self.invoice.pk], retries=1)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.SENT)

    def test_worker_gives_up_and_keeps_the_invoice_in_drafts(self):
        with mock.patch('notifications.emails.send_invoice_email', return_value=False):
            result = send_invoice_email_task.apply(args=[self.invoice.pk], retries=3)
        self.assertEqual(result.result, 'failed')
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.DRAFT)
        self.assertIsNone(self.invoice.send_queued_at)
        self.assertIn('Sending failed', self.invoice.send_error)

    def test_plain_call_tries_once(self):
        with mock.patch('notifications.emails.send_invoice_email', return_value=False) as send:
            self.assertEqual(send_invoice_email_task(self.invoice.pk), 'failed')
        self.assertEqual(send.call_count, 1)

    def test_bounced_address_is_not_tried(self):
        Owner.objects.filter(pk=self.owner.pk).update(email_bounced_at=timezone.now())
        with mock.patch('notifications.emails.send_invoice_email') as send:
            self.assertEqual(send_invoice_email_task(self.invoice.pk), 'failed')
        send.assert_not_called()
        self.invoice.refresh_from_db()
        self.assertIn('not arriving', self.invoice.send_error)
        self.assertIsNone(self.invoice.send_queued_at)


class DocumentExpiryClaimTests(TestCase):
    def test_emails_only_documents_this_run_claimed(self):
        BusinessSettings.objects.update_or_create(pk=1, defaults={'email': 'yard@example.com'})
        BusinessSettings.get_settings()
        owner = Owner.objects.create(name='Doc Owner')
        soon = timezone.localdate() + timedelta(days=5)
        mine = Document.objects.create(owner=owner, title='Insurance', expiry_date=soon,
                                       file='documents/a.pdf')
        Document.objects.create(owner=owner, title='Passport', expiry_date=soon,
                                file='documents/b.pdf', expiry_reminder_sent=True)
        self.assertIn('1 document', send_document_expiry_reminders())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(mine.title, mail.outbox[0].alternatives[0][0])
        self.assertNotIn('Passport', mail.outbox[0].alternatives[0][0])
        self.assertEqual(send_document_expiry_reminders(), 'Sent 0 document expiry reminders')


class HealthCheckTests(TestCase):
    def test_ok_before_any_scheduled_run(self):
        response = self.client.get('/_health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['checks']['database'], 'ok')
        self.assertEqual(response.json()['checks']['scheduled_tasks'], 'none yet')

    def test_stale_scheduled_tasks_fail_the_check(self):
        ScheduledRun.objects.create(
            task_name=CHECK_STATUS, run_key='2026-01-01', task_id='x',
            status=ScheduledRun.Status.SUCCESS, finished_at=timezone.now() - timedelta(hours=30),
        )
        response = self.client.get('/_health/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['checks']['scheduled_tasks'], 'stale')

    def test_recent_run_is_ok(self):
        ScheduledRun.objects.create(
            task_name=CHECK_STATUS, run_key='2026-01-01', task_id='x',
            status=ScheduledRun.Status.SUCCESS, finished_at=timezone.now() - timedelta(hours=2),
        )
        self.assertEqual(self.client.get('/_health/').json()['checks']['scheduled_tasks'], 'ok')

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL='redis://127.0.0.1:1/0')
    def test_redis_down_fails_the_check(self):
        response = self.client.get('/_health/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['checks']['redis'], 'error')

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL='redis://127.0.0.1:6379/0')
    def test_redis_up(self):
        with mock.patch('redis.Redis.ping', return_value=True):
            self.assertEqual(self.client.get('/_health/').json()['checks']['redis'], 'ok')


class BackgroundTasksPageTests(TestCase):
    def setUp(self):
        self.failure = FailedTask.objects.create(
            task_name='invoicing.tasks.send_invoice_email_task', args=[42],
            error='SMTPServerDisconnected: gone', retries=3,
        )

    def test_admin_sees_failures_and_the_schedule(self):
        self.client.force_login(make_admin('tasksadmin'))
        page = self.client.get(reverse('background_tasks'))
        self.assertContains(page, 'Send invoice email task')
        self.assertContains(page, 'SMTPServerDisconnected')
        self.assertContains(page, 'Nightly backup')
        settings_page = self.client.get(reverse('app_settings'))
        self.assertContains(settings_page, '1 failed')

    def test_run_again_queues_it_with_the_same_arguments(self):
        admin = make_admin('tasksadmin2')
        self.client.force_login(admin)
        with mock.patch('celery.current_app.send_task') as send_task:
            self.client.post(reverse('failed_task_resolve', args=[self.failure.pk]), {'action': 'run_again'})
        send_task.assert_called_once_with('invoicing.tasks.send_invoice_email_task', args=[42], kwargs={})
        self.failure.refresh_from_db()
        self.assertEqual(self.failure.resolution, 'run again')
        self.assertEqual(self.failure.resolved_by, admin)

    def test_dismiss(self):
        self.client.force_login(make_admin('tasksadmin3'))
        self.client.post(reverse('failed_task_resolve', args=[self.failure.pk]), {'action': 'dismiss'})
        self.failure.refresh_from_db()
        self.assertEqual(self.failure.resolution, 'dismissed')
        self.assertNotContains(self.client.get(reverse('background_tasks')), 'SMTPServerDisconnected')

    def test_non_admin_is_refused(self):
        self.client.force_login(make_user_with_access('tasksuser', settings='full'))
        self.assertEqual(self.client.get(reverse('background_tasks')).status_code, 403)
        response = self.client.post(reverse('failed_task_resolve', args=[self.failure.pk]), {'action': 'dismiss'})
        self.assertEqual(response.status_code, 403)


class TaskRecordPurgeTests(TestCase):
    @override_settings(SCHEDULED_RUN_RETENTION_DAYS=90, FAILED_TASK_RETENTION_DAYS=90)
    def test_old_records_are_removed(self):
        old = timezone.now() - timedelta(days=100)
        ScheduledRun.objects.create(task_name='a', run_key='old', task_id='1', started_at=old)
        ScheduledRun.objects.create(task_name='a', run_key='new', task_id='2')
        FailedTask.objects.create(task_name='a', error='x', resolved_at=old)
        FailedTask.objects.create(task_name='a', error='open', failed_at=old)
        counts = purge_soft_deleted()
        self.assertEqual((counts['ScheduledRun'], counts['FailedTask']), (1, 1))
        self.assertEqual(list(ScheduledRun.objects.values_list('run_key', flat=True)), ['new'])
        self.assertEqual(FailedTask.objects.get().error, 'open')
