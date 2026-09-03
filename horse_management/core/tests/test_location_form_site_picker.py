"""LocationForm: the Arable usage option and the site drop-down.

The site field is a <select> of every site already in use plus an
"Add a new site…" row that reveals a text box. A plain ``site=<text>``
post still works so older callers and the popup test data keep working.
"""

from django.test import TestCase
from django.urls import reverse

from core.forms import LocationForm, SitePickerWidget, get_site_choices
from core.models import Location
from core.roles_testutils import make_admin

POPUP = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_TARGET': 'popup-body'}


def _valid(**overrides):
    data = {
        'name': 'Flat Whitakers', 'site': 'Somerford', 'usage': 'horses',
        'description': '', 'capacity': '',
    }
    data.update(overrides)
    return data


class ArableUsageTests(TestCase):

    def test_arable_is_a_usage_choice(self):
        self.assertEqual(Location.Usage.ARABLE, 'arable')
        self.assertIn(('arable', 'Arable'), Location.Usage.choices)

    def test_form_offers_arable(self):
        form = LocationForm()
        self.assertIn(('arable', 'Arable'), list(form.fields['usage'].choices))

    def test_arable_saves(self):
        form = LocationForm(data=_valid(usage='arable'))
        self.assertTrue(form.is_valid(), form.errors)
        location = form.save()
        self.assertEqual(location.usage, 'arable')
        self.assertEqual(location.get_usage_display(), 'Arable')

    def test_arable_shows_on_list_and_detail(self):
        location = Location.objects.create(name='Long Acre', site='Somerford', usage='arable')
        self.client.force_login(make_admin())
        for url in (reverse('location_list'), reverse('location_detail', args=[location.pk])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'Arable')
                self.assertContains(response, 'text-amber-800')


class SiteChoicesTests(TestCase):

    def setUp(self):
        Location.objects.create(name='A', site='Somerford')
        Location.objects.create(name='B', site='Somerford')
        Location.objects.create(name='C', site='colgate')
        Location.objects.create(name='D', site='Archived Site', is_archived=True)

    def test_choices_are_distinct_sorted_and_end_with_new(self):
        choices = get_site_choices()
        self.assertEqual(choices[0], ('', '---------'))
        self.assertEqual(
            [v for v, _ in choices[1:-1]],
            ['Archived Site', 'colgate', 'Somerford'],
        )
        self.assertEqual(choices[-1], (SitePickerWidget.NEW, SitePickerWidget.NEW_LABEL))

    def test_include_adds_a_missing_site(self):
        values = [v for v, _ in get_site_choices(include='Brand New')]
        self.assertIn('Brand New', values)


class SitePickerFormTests(TestCase):

    def setUp(self):
        self.location = Location.objects.create(name='Flat Whitakers', site='Somerford')
        Location.objects.create(name='Top', site='Colgate')

    def test_site_renders_as_a_select_with_existing_sites(self):
        html = str(LocationForm(instance=self.location)['site'])
        self.assertIn('<select name="site"', html)
        self.assertIn('<option value="Somerford" selected>Somerford</option>', html)
        self.assertIn('<option value="Colgate">Colgate</option>', html)
        self.assertIn(f'<option value="{SitePickerWidget.NEW}">', html)
        self.assertIn('name="site_new"', html)
        self.assertNotIn('<input type="text" name="site"', html)

    def test_existing_site_is_selected_when_editing(self):
        form = LocationForm(instance=self.location)
        self.assertIn('Somerford', [v for v, _ in form.fields['site'].widget.choices])

    def test_picking_an_existing_site(self):
        form = LocationForm(data=_valid(site='Colgate'), instance=self.location)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().site, 'Colgate')

    def test_new_site_comes_from_the_text_box(self):
        form = LocationForm(
            data=_valid(site=SitePickerWidget.NEW, site_new='  California Farm '),
            instance=self.location,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().site, 'California Farm')

    def test_new_site_left_blank_is_an_error(self):
        form = LocationForm(data=_valid(site=SitePickerWidget.NEW, site_new=''))
        self.assertFalse(form.is_valid())
        self.assertIn('site', form.errors)
        # The text box stays open so the error lands next to it.
        html = str(form['site'])
        self.assertIn(f'<option value="{SitePickerWidget.NEW}" selected>', html)

    def test_plain_text_site_still_accepted(self):
        form = LocationForm(data=_valid(site='Somewhere Else'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().site, 'Somewhere Else')

    def test_typed_site_survives_a_validation_error(self):
        form = LocationForm(data=_valid(name='', site=SitePickerWidget.NEW, site_new='New Farm'))
        self.assertFalse(form.is_valid())
        html = str(form['site'])
        self.assertIn(f'<option value="{SitePickerWidget.NEW}" selected>', html)
        self.assertIn('value="New Farm"', html)


class SitePickerViewTests(TestCase):

    def setUp(self):
        self.location = Location.objects.create(name='Flat Whitakers', site='Somerford')
        self.client.force_login(make_admin())

    def test_popup_edit_shows_the_site_dropdown(self):
        response = self.client.get(reverse('location_update', args=[self.location.pk]), **POPUP)
        self.assertContains(response, '<select name="site"')
        self.assertContains(response, 'Add a new site')
        self.assertNotContains(response, 'Main site name (e.g.')

    def test_full_form_shows_the_site_dropdown(self):
        response = self.client.get(reverse('location_update', args=[self.location.pk]))
        self.assertContains(response, '<select name="site"')

    def test_create_with_a_new_site_via_the_view(self):
        response = self.client.post(reverse('location_create'), _valid(
            name='Long Acre', site=SitePickerWidget.NEW, site_new='California Farm',
            usage='arable',
        ))
        self.assertEqual(response.status_code, 302)
        created = Location.objects.get(name='Long Acre')
        self.assertEqual((created.site, created.usage), ('California Farm', 'arable'))
