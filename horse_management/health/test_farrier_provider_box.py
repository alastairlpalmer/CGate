"""The Farrier box on the farrier forms lists active farriers only.

A vet (TLP Vet Services) was saved as the farrier on a record, because the
box listed every service provider.
"""

from datetime import date

from django.test import TestCase
from django.urls import reverse

from billing.models import ServiceProvider
from core.models import Horse
from core.roles_testutils import make_admin
from health.forms import BulkFarrierVisitForm, FarrierVisitForm
from health.models import FarrierVisit


class FarrierBoxTests(TestCase):
    def setUp(self):
        self.farrier = ServiceProvider.objects.create(name='Sam Shoe', provider_type='farrier')
        self.old_farrier = ServiceProvider.objects.create(name='Old Shoe', provider_type='farrier', is_active=False)
        self.vet = ServiceProvider.objects.create(name='TLP Vet Services', provider_type='vet')
        self.horse = Horse.objects.create(name='Bramble')

    def test_new_record_lists_active_farriers_only(self):
        for form_class in (FarrierVisitForm, BulkFarrierVisitForm):
            with self.subTest(form=form_class.__name__):
                choices = list(form_class().fields['service_provider'].queryset)
                self.assertEqual(choices, [self.farrier])

    def test_a_vet_cannot_be_saved_as_the_farrier(self):
        form = FarrierVisitForm({
            'horse': self.horse.pk, 'date': '2026-11-01', 'service_provider': self.vet.pk,
            'work_done': 'trim', 'cost': '0',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('service_provider', form.errors)

    def test_editing_an_old_record_keeps_its_provider(self):
        record = FarrierVisit.objects.create(
            horse=self.horse, date=date(2026, 9, 19), service_provider=self.vet, work_done='trim',
        )
        choices = list(FarrierVisitForm(instance=record).fields['service_provider'].queryset)
        self.assertIn(self.vet, choices)
        self.assertIn(self.farrier, choices)
        self.assertNotIn(self.old_farrier, choices)

    def test_the_add_page_shows_no_vet(self):
        self.client.force_login(make_admin('boss'))
        response = self.client.get(reverse('farrier_create'))
        self.assertContains(response, 'Sam Shoe')
        self.assertNotContains(response, 'TLP Vet Services')
