"""With no real mail backend on a live server, an app email is not counted
as sent (pre-launch review)."""

from django.test import SimpleTestCase, override_settings

from notifications.mail import EmailNotSetUp, message


class MailNotSetUpTests(SimpleTestCase):
    @override_settings(DEBUG=False, EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend')
    def test_the_console_backend_on_a_live_server_raises(self):
        with self.assertRaises(EmailNotSetUp):
            message('Hi', '<p>Hi</p>', ['a@example.com']).send()

    @override_settings(DEBUG=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_a_real_backend_sends(self):
        self.assertEqual(message('Hi', '<p>Hi</p>', ['a@example.com']).send(), 1)
