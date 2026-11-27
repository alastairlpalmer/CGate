from django.db import models


class ContactMessage(models.Model):
    """A message from the public Contact page.

    Saved before the email is sent, so a message is never lost when email
    fails. The team reads them in the admin, and by email when
    MARKETING_CONTACT_EMAIL is set.
    """

    class Topic(models.TextChoices):
        DEMO = 'demo', 'Book a demo'
        PRICING = 'pricing', 'A question about pricing'
        OTHER = 'other', 'Something else'

    topic = models.CharField(max_length=20, choices=Topic.choices, default=Topic.DEMO)
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    yard_name = models.CharField('yard name', max_length=160, blank=True)
    horses = models.PositiveIntegerField('number of horses', null=True, blank=True)
    message = models.TextField(max_length=4000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    emailed_at = models.DateTimeField(null=True, blank=True)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.get_topic_display()})'
