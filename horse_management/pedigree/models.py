"""Shared pedigree records (docs/HORSE_IDENTITY_AND_PEDIGREE_PLAN.md §6.3).

One ``PedigreeHorse`` for each horse that appears in any pedigree: the
horses on the yard (``horse`` is set) and the stallions, mares and
grandparents that never come to the yard. Each record points to its own
sire and dam, so a tree is a chain of links. A stallion's parents are
typed once, and every foal by him shows them.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from simple_history.models import HistoricalRecords

from .names import label as make_label
from .names import name_key

# How far up a save looks for a loop (a horse as its own ancestor).
LOOP_CHECK_GENERATIONS = 4


class PedigreeHorse(models.Model):

    class Sex(models.TextChoices):
        MALE = 'M', 'Male'
        FEMALE = 'F', 'Female'

    class Source(models.TextChoices):
        TYPED = 'typed', 'Typed'
        FOALING = 'foaling', 'Filled at foaling'
        BACKFILL = 'backfill', 'From the old sire and dam names'
        SPREADSHEET = 'spreadsheet', 'Spreadsheet'
        DOCUMENT = 'document', 'Read from a document'
        FEED = 'feed', 'Data feed'

    name = models.CharField(max_length=200, help_text="Registered name, without the suffix")
    name_key = models.CharField(max_length=200, db_index=True, editable=False)
    country = models.CharField(max_length=3, blank=True, help_text="Stud-book suffix, for example GB or IRE")
    year_of_birth = models.PositiveSmallIntegerField(null=True, blank=True)
    sex = models.CharField(max_length=1, choices=Sex.choices, blank=True)
    colour = models.CharField(max_length=20, blank=True)
    sire = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='progeny_as_sire',
    )
    dam = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='progeny_as_dam',
    )
    # "Known to be unknown" (a polo pony's sire, for example). Not missing.
    sire_unknown = models.BooleanField(default=False)
    dam_unknown = models.BooleanField(default=False)
    horse = models.OneToOneField(
        'core.Horse', null=True, blank=True, on_delete=models.SET_NULL, related_name='pedigree',
        help_text="Set when this horse is on the system",
    )
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.TYPED)
    checked = models.BooleanField(default=False, help_text="Compared with a passport or a stud book")
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords(excluded_fields=['updated_at', 'name_key'])

    class Meta:
        ordering = ['name', 'country', 'year_of_birth']
        indexes = [models.Index(fields=['name_key', 'country'])]

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        self.name = (self.name or '').strip()
        self.country = (self.country or '').strip().upper()
        self.name_key = name_key(self.name)
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'name' in update_fields:
            kwargs['update_fields'] = list(dict.fromkeys(list(update_fields) + ['name_key']))
        super().save(*args, **kwargs)

    # -- names ---------------------------------------------------------------

    @property
    def short_label(self):
        """'Copper Ridge (GB)'."""
        return make_label(self.name, self.country)

    @property
    def label(self):
        """'Copper Ridge (GB) 2004': unique enough to tell two records apart."""
        return make_label(self.name, self.country, self.year_of_birth)

    @property
    def on_yard(self):
        return self.horse_id is not None

    # -- checks --------------------------------------------------------------

    def clean(self):
        super().clean()
        errors = {}
        if self.sire_id and self.sire_id == self.pk:
            errors['sire'] = 'A horse cannot be its own sire.'
        if self.dam_id and self.dam_id == self.pk:
            errors['dam'] = 'A horse cannot be its own dam.'
        if self.sire_id and self.sire.sex == self.Sex.FEMALE:
            errors['sire'] = f'{self.sire.label} is a mare, so it cannot be a sire.'
        if self.dam_id and self.dam.sex == self.Sex.MALE:
            errors['dam'] = f'{self.dam.label} is male, so it cannot be a dam.'
        if errors:
            raise ValidationError(errors)
        if self.pk and self.creates_loop():
            raise ValidationError('This would make the horse its own ancestor.')

    def creates_loop(self):
        """True if ``self`` is among the ancestors of its new parents.

        Reads the ancestors from the database, never from copies held in
        memory, which can be older than the last save.
        """
        ids = {pid for pid in (self.sire_id, self.dam_id) if pid}
        for _ in range(LOOP_CHECK_GENERATIONS + 1):
            if self.pk in ids:
                return True
            if not ids:
                return False
            rows = PedigreeHorse.objects.filter(pk__in=ids).values_list('sire_id', 'dam_id')
            ids = {pid for pair in rows for pid in pair if pid}
        return self.pk in ids

    # -- use -----------------------------------------------------------------

    def progeny(self):
        return PedigreeHorse.objects.filter(models.Q(sire=self) | models.Q(dam=self))


class PedigreeMerge(models.Model):
    """One join of two pedigree records, kept so it can be undone.

    ``removed_snapshot`` holds every field of the record that was removed;
    the id lists say which records pointed to it as sire or dam.
    """

    kept = models.ForeignKey(PedigreeHorse, on_delete=models.CASCADE, related_name='merges')
    removed_snapshot = models.JSONField()
    moved_sire_ids = models.JSONField(default=list)
    moved_dam_ids = models.JSONField(default=list)
    filled_fields = models.JSONField(default=list)
    moved_horse_id = models.PositiveIntegerField(null=True, blank=True)
    merged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    merged_at = models.DateTimeField(auto_now_add=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-merged_at']

    def __str__(self):
        return f"{self.removed_label} into {self.kept}"

    @property
    def removed_label(self):
        snap = self.removed_snapshot or {}
        return make_label(snap.get('name', ''), snap.get('country', ''), snap.get('year_of_birth'))
