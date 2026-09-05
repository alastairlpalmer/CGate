"""
Core models for horse management system.
"""

from datetime import date
from decimal import Decimal
from functools import cached_property
from pathlib import Path

from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone


def validate_file_size(value):
    """Reject uploads larger than 5MB."""
    try:
        size = value.size
    except (FileNotFoundError, OSError):
        # Existing DB path whose file is gone from storage (e.g. uploads
        # from the serverless era) — nothing new to validate, and raising
        # here would make every save of the record a 500.
        return
    if size > 5 * 1024 * 1024:
        raise DjangoValidationError("File size must be under 5MB.")


class Owner(models.Model):
    """Horse owner with contact information."""

    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    account_code = models.CharField(
        max_length=20,
        blank=True,
        help_text="Account code for accounting systems (e.g. Xero)"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @cached_property
    def active_horses(self):
        """Get horses currently placed with this owner."""
        return Horse.objects.filter(
            placements__owner=self,
            placements__end_date__isnull=True
        ).distinct()

    @cached_property
    def active_horse_count(self):
        return self.active_horses.count()

    @cached_property
    def active_horses_via_shares(self):
        """Get horses this owner has ownership shares in (active horses only)."""
        return Horse.objects.filter(
            ownership_shares__owner=self,
            is_active=True
        ).distinct()

    @cached_property
    def owned_horse_count(self):
        return self.active_horses_via_shares.count()


class LocationQuerySet(models.QuerySet):
    """Queryset helpers so callers can say what they mean about archiving."""

    def active(self):
        """Locations still in use — everything pickers and lists should show."""
        return self.filter(is_archived=False)

    def archived(self):
        """Locations taken out of use but kept for history and reports."""
        return self.filter(is_archived=True)


class BoundarySource(models.TextChoices):
    LANDAPP = 'landapp', 'Land App import'
    MANUAL = 'manual', 'Drawn by hand'
    IMPORT = 'import', 'Other import'


def validate_coordinate_pair(latitude, longitude):
    """The rules every coordinate pair must meet, for forms and scripts.

    Mirrors ``coordinate_constraints`` so a script that skips ``clean()``
    is still stopped at the database.
    """
    errors = {}
    if (latitude is None) != (longitude is None):
        missing = 'longitude' if longitude is None else 'latitude'
        errors[missing] = 'Enter both a latitude and a longitude, or neither.'
    if latitude is not None and not (-90 <= latitude <= 90):
        errors['latitude'] = 'Latitude must be between −90 and 90.'
    if longitude is not None and not (-180 <= longitude <= 180):
        errors['longitude'] = 'Longitude must be between −180 and 180.'
    if latitude is not None and longitude is not None and latitude == 0 and longitude == 0:
        errors['latitude'] = '0, 0 is not a real position — it usually means a link did not parse.'
    if errors:
        raise DjangoValidationError(errors)


def coordinate_constraints(prefix):
    """Database checks for a ``latitude``/``longitude`` pair on a model."""
    return [
        models.CheckConstraint(
            condition=Q(latitude__isnull=True) | Q(latitude__gte=-90, latitude__lte=90),
            name=f'{prefix}_latitude_range',
        ),
        models.CheckConstraint(
            condition=Q(longitude__isnull=True) | Q(longitude__gte=-180, longitude__lte=180),
            name=f'{prefix}_longitude_range',
        ),
        models.CheckConstraint(
            condition=(
                Q(latitude__isnull=True, longitude__isnull=True)
                | Q(latitude__isnull=False, longitude__isnull=False)
            ),
            name=f'{prefix}_coords_both_or_neither',
        ),
        models.CheckConstraint(
            condition=~Q(latitude=0, longitude=0),
            name=f'{prefix}_coords_not_null_island',
        ),
    ]


class Location(models.Model):
    """Physical location where horses are kept."""

    class Usage(models.TextChoices):
        HORSES = 'horses', 'Horses'
        MIXED = 'mixed', 'Mixed Grazing'
        RESTED = 'rested', 'Rested'
        HAY = 'hay', 'Hay'
        ARABLE = 'arable', 'Arable'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=200)
    site = models.CharField(
        max_length=100,
        help_text="Main site name (e.g., Colgate, Somerford, California Farm)"
    )
    usage = models.CharField(
        max_length=20, choices=Usage.choices, default=Usage.HORSES,
    )
    description = models.TextField(blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    is_archived = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Archived locations keep their history but are hidden from "
                  "lists and pickers.",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    # Where the location is. A point is enough for the nearest-location
    # chip and the map; a boundary polygon (phase 4) is optional and most
    # locations will never have one. Both null, or both set — see clean().
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        help_text="WGS84 latitude, −90 to 90.",
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True,
        help_text="WGS84 longitude, −180 to 180.",
    )
    # A GeoJSON *geometry* (Polygon or MultiPolygon), never a Feature.
    boundary = models.JSONField(null=True, blank=True)
    boundary_source = models.CharField(
        max_length=10, choices=BoundarySource.choices, blank=True, default='',
    )
    boundary_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LocationQuerySet.as_manager()

    class Meta:
        ordering = ['site', 'name']
        constraints = coordinate_constraints('location')

    def __str__(self):
        return f"{self.site} — {self.name}"

    def clean(self):
        super().clean()
        validate_coordinate_pair(self.latitude, self.longitude)
        if self.boundary is not None and not isinstance(self.boundary, dict):
            raise DjangoValidationError({'boundary': 'Boundary must be a GeoJSON geometry object.'})

    @property
    def has_coordinates(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def maps_url(self):
        """A Google Maps link built from the stored point (the pasted link in
        ``description`` is plain text, this one is clickable)."""
        if not self.has_coordinates:
            return ''
        from .geo import format_coords
        return "https://www.google.com/maps?q=" + format_coords(self.latitude, self.longitude).replace(' ', '')

    def archive_blockers(self):
        """Reasons this location cannot be archived right now.

        A location with horses on it must be emptied first; archiving it
        would hide somewhere the yard is still using.
        """
        blockers = []
        horses = self.current_horse_count
        if horses:
            blockers.append(
                f"{horses} horse{'s are' if horses != 1 else ' is'} still on "
                f"this location. Move {'them' if horses != 1 else 'it'} first."
            )
        return blockers

    def delete_blockers(self):
        """Reasons this location cannot be deleted.

        Deletion is only for locations with no records attached. Anything
        with placement or feed history must be archived, or the history
        goes too.
        """
        blockers = []
        placements = self.placements.count()
        if placements:
            blockers.append(
                f"{placements} placement record"
                f"{'s point' if placements != 1 else ' points'} at this location."
            )
        feed_outs = self.feed_outs.count()
        if feed_outs:
            blockers.append(
                f"{feed_outs} feed record"
                f"{'s point' if feed_outs != 1 else ' points'} at this field."
            )
        return blockers

    @cached_property
    def current_horses(self):
        """Get horses currently at this location."""
        return Horse.objects.filter(
            placements__location=self,
            placements__end_date__isnull=True
        ).distinct()

    @cached_property
    def current_horse_count(self):
        return self.current_horses.count()

    @cached_property
    def availability(self):
        """Return available spaces if capacity is set."""
        if self.capacity is not None:
            return self.capacity - self.current_horse_count
        return None


class SiteSettings(models.Model):
    """Per-site values keyed on the site name string used by ``Location.site``.

    There is no Site table: a site is the distinct ``Location.site`` string.
    Known limit — if a site is renamed by editing each of its locations,
    this row keeps the old name and goes stale until it is edited too.
    """

    site = models.CharField(max_length=100, unique=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    # How far from the centre still counts as "on this site".
    radius_m = models.PositiveIntegerField(default=1500)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['site']
        verbose_name_plural = 'site settings'
        constraints = coordinate_constraints('sitesettings')

    def __str__(self):
        return self.site

    def clean(self):
        super().clean()
        validate_coordinate_pair(self.latitude, self.longitude)

    @property
    def has_coordinates(self):
        return self.latitude is not None and self.longitude is not None

    @classmethod
    def for_site(cls, site):
        """The row for a site name, or None. Never creates one."""
        if not site:
            return None
        return cls.objects.filter(site=site).first()


class LocationBoundaryHistory(models.Model):
    """A boundary that an import replaced, kept so an overwrite can be undone.

    Written by ``core.boundary_import.apply_boundary`` whenever a location
    that already had a boundary receives a new one.
    """

    location = models.ForeignKey(
        Location, on_delete=models.CASCADE, related_name='boundary_history',
    )
    boundary = models.JSONField()
    source = models.CharField(max_length=10, blank=True, default='')
    replaced_at = models.DateTimeField(auto_now_add=True)
    replaced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        ordering = ['-replaced_at']
        verbose_name_plural = 'location boundary history'

    def __str__(self):
        return f"{self.location} boundary replaced {self.replaced_at:%Y-%m-%d}"


class LocationUsagePeriod(models.Model):
    """A contiguous span of time during which a Location had a single usage.

    Mirrors Placement: at most one open period (end_date null) per location.
    Records the field-usage history so we can analyse how many days a field
    was rested, held horses, was set for hay, etc. across a year.
    """

    class Source(models.TextChoices):
        AUTO = 'auto', 'Automatic'
        MANUAL = 'manual', 'Manual'

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='usage_periods'
    )
    usage = models.CharField(max_length=20, choices=Location.Usage.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.MANUAL,
        help_text="Whether this period was logged manually or set automatically "
                  "by a horse arrival/departure."
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']
        indexes = [
            models.Index(fields=['location', 'end_date'], name='usageperiod_loc_enddate'),
            models.Index(fields=['location', 'start_date'], name='usageperiod_loc_startdate'),
        ]
        constraints = [
            # Only one open-ended usage period per location at a time
            models.UniqueConstraint(
                fields=['location'],
                condition=models.Q(end_date__isnull=True),
                name='unique_open_usage_period_per_location',
            ),
        ]

    def __str__(self):
        status = "current" if self.is_current else f"ended {self.end_date}"
        return f"{self.location.name}: {self.get_usage_display()} ({status})"

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise DjangoValidationError(
                "Usage period end date cannot be before start date."
            )

    @property
    def is_current(self):
        return self.end_date is None

    def get_effective_dates_in_period(self, period_start, period_end):
        """Return (effective_start, effective_end) clipped to the given period."""
        effective_start = max(self.start_date, period_start)
        effective_end = min(self.end_date or period_end, period_end)
        return (effective_start, effective_end)

    def get_days_in_period(self, period_start, period_end):
        """Inclusive count of days this period overlaps the given period."""
        effective_start, effective_end = self.get_effective_dates_in_period(
            period_start, period_end
        )
        if effective_start > effective_end:
            return 0
        return (effective_end - effective_start).days + 1


class Horse(models.Model):
    """Individual horse record."""

    class Sex(models.TextChoices):
        MARE = 'mare', 'Mare'
        GELDING = 'gelding', 'Gelding'
        STALLION = 'stallion', 'Stallion'
        COLT = 'colt', 'Colt'
        FILLY = 'filly', 'Filly'

    class Color(models.TextChoices):
        BAY = 'bay', 'Bay'
        CHESTNUT = 'chestnut', 'Chestnut'
        GREY = 'grey', 'Grey'
        BLACK = 'black', 'Black'
        BROWN = 'brown', 'Brown'
        PALOMINO = 'palomino', 'Palomino'
        SKEWBALD = 'skewbald', 'Skewbald'
        PIEBALD = 'piebald', 'Piebald'
        ROAN = 'roan', 'Roan'
        DUN = 'dun', 'Dun'
        CREAM = 'cream', 'Cream'
        OTHER = 'other', 'Other'

    name = models.CharField(max_length=200)
    date_of_birth = models.DateField(null=True, blank=True, help_text="Date of birth")
    age = models.PositiveIntegerField(null=True, blank=True, help_text="Age in years (used if DOB unknown)")
    color = models.CharField(max_length=20, choices=Color.choices, blank=True)
    sex = models.CharField(max_length=20, choices=Sex.choices, blank=True)
    breeding = models.TextField(blank=True, help_text="Sire/dam information (free text)")
    dam = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='offspring_as_dam', help_text="Dam (mother) if she is in the system"
    )
    dam_name = models.CharField(max_length=200, blank=True, help_text="Dam (mother) name")
    sire_name = models.CharField(max_length=200, blank=True, help_text="Stallion name")
    photo = models.ImageField(
        upload_to='horses/', blank=True, null=True,
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif']),
            validate_file_size,
        ],
    )
    # Square rendition generated from photo on save — avatars render this
    # (~10-20 KB) instead of the full-resolution original (often several MB),
    # which matters on a 30-row horse list over yard 4G.
    photo_thumb = models.ImageField(
        upload_to='horses/thumbs/', blank=True, null=True, editable=False,
    )
    notes = models.TextField(blank=True, help_text="Special notes (e.g., first winter, lame, needs rug)")
    passport_number = models.CharField(max_length=100, blank=True)
    has_passport = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True, help_text="False if horse has left permanently")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        # Deactivating a horse that still has an open placement strands the
        # record: Departed in lists/search but still occupying a field.
        # Runs for every form built on this model — including the Django
        # admin, which bypasses the app's own forms. Departures must go
        # through the Depart flow, which closes the placement too.
        if self.pk and not self.is_active:
            was_active = Horse.objects.filter(
                pk=self.pk, is_active=True
            ).exists()
            has_open_placement = self.placements.filter(
                end_date__isnull=True
            ).exists()
            if was_active and has_open_placement:
                raise DjangoValidationError({
                    'is_active': (
                        "This horse still has an open placement. Use the "
                        "Depart button on the horse's page to log the "
                        "departure instead of unticking Active."
                    ),
                })

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        if update_fields is None or 'photo' in update_fields:
            if self._sync_photo_thumb() and update_fields is not None:
                kwargs['update_fields'] = list(update_fields) + ['photo_thumb']
        super().save(*args, **kwargs)

    def _sync_photo_thumb(self):
        """Keep photo_thumb in step with photo.

        Regenerates when the photo is new/changed (or a thumb is missing),
        clears it when the photo is removed. Generation failures degrade to
        "no thumbnail" — the avatar partial falls back to the original photo,
        so a bad image never blocks saving the horse. Returns True when
        photo_thumb was modified.
        """
        from .images import make_avatar_thumbnail

        if not self.photo:
            if self.photo_thumb:
                self.photo_thumb.delete(save=False)
                self.photo_thumb = None
                return True
            return False

        old_photo_name = None
        if self.pk:
            old_photo_name = (
                Horse.objects.filter(pk=self.pk)
                .values_list('photo', flat=True)
                .first()
            )
        if self.photo.name == old_photo_name and self.photo_thumb:
            return False  # unchanged photo, thumb present

        thumb = make_avatar_thumbnail(self.photo)
        if thumb is None:
            return False
        if self.photo_thumb:
            self.photo_thumb.delete(save=False)
        base = Path(self.photo.name).stem if self.photo.name else 'horse'
        self.photo_thumb.save(f"{base}-thumb.jpg", thumb, save=False)
        return True

    @property
    def calculated_age(self):
        """Return age from DOB if set, else fall back to age field."""
        if self.date_of_birth:
            today = timezone.localdate()
            return today.year - self.date_of_birth.year - (
                (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
            )
        return self.age

    @property
    def is_mare(self):
        return self.sex == self.Sex.MARE

    @cached_property
    def foals(self):
        """Return offspring where this horse is the dam."""
        return Horse.objects.filter(dam=self)

    @cached_property
    def current_placement(self):
        """Get the current active placement."""
        return self.placements.filter(end_date__isnull=True).first()

    @cached_property
    def current_location(self):
        """Get the current location."""
        placement = self.current_placement
        return placement.location if placement else None

    @cached_property
    def current_owner(self):
        """Get the current owner -- prefer OwnershipShare, fall back to Placement."""
        primary = self.primary_owner
        if primary:
            return primary
        placement = self.current_placement
        return placement.owner if placement else None

    @cached_property
    def current_owners(self):
        """Get all current fractional owners with their share percentages.

        Returns a list of (owner, share_percentage) tuples.
        Falls back to placement owner at 100% if no ownership records exist.
        """
        shares = list(
            self.ownership_shares.select_related('owner').all()
        )
        if shares:
            return [(s.owner, s.share_percentage) for s in shares]
        # Fallback to placement owner
        if self.current_owner:
            return [(self.current_owner, Decimal('100.00'))]
        return []

    @cached_property
    def has_fractional_ownership(self):
        """Check if this horse has explicit ownership share records."""
        return self.ownership_shares.exists()

    @cached_property
    def primary_owner(self):
        """Get the primary contact owner, falling back to largest shareholder."""
        share = self.ownership_shares.filter(is_primary_contact=True).first()
        if not share:
            share = self.ownership_shares.order_by('-share_percentage').first()
        return share.owner if share else None

    @cached_property
    def owners(self):
        """Get all current owners via OwnershipShare."""
        return Owner.objects.filter(
            ownership_shares__horse=self
        ).distinct()

    @cached_property
    def has_multiple_owners(self):
        return self.ownership_shares.count() > 1


class RateType(models.Model):
    """Rate configuration for different livery types."""

    name = models.CharField(max_length=100)
    daily_rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['daily_rate']

    def __str__(self):
        return f"{self.name} (£{self.daily_rate}/day)"


class Placement(models.Model):
    """Tracks where a horse is located and who owns it."""

    horse = models.ForeignKey(
        Horse,
        on_delete=models.CASCADE,
        related_name='placements'
    )
    owner = models.ForeignKey(
        Owner,
        on_delete=models.PROTECT,
        related_name='placements'
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='placements'
    )
    rate_type = models.ForeignKey(
        RateType,
        on_delete=models.PROTECT,
        related_name='placements'
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    expected_departure = models.DateField(
        null=True, blank=True,
        help_text="Anticipated departure date (for forecasting)"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date']
        indexes = [
            models.Index(fields=['horse', 'end_date'], name='placement_horse_enddate'),
            models.Index(fields=['horse', 'location', 'end_date'], name='placement_horse_loc_end'),
            models.Index(fields=['horse', 'owner', 'end_date'], name='placement_horse_owner_end'),
            models.Index(fields=['end_date'], name='placement_enddate_solo'),
            # Location detail / capacity / emptiness checks all filter
            # location + open placement; the horse-led indexes can't serve them.
            models.Index(fields=['location', 'end_date'], name='placement_loc_enddate'),
        ]
        constraints = [
            # Prevent a horse from having more than one open-ended placement
            models.UniqueConstraint(
                fields=['horse'],
                condition=models.Q(end_date__isnull=True),
                name='unique_active_placement_per_horse',
            ),
        ]

    def __str__(self):
        status = "current" if self.is_current else f"ended {self.end_date}"
        return f"{self.horse.name} at {self.location.name} ({status})"

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if not self.horse_id or not self.start_date:
            return

        if self.end_date and self.end_date < self.start_date:
            raise ValidationError(
                "Placement end date cannot be before start date."
            )

        # Find overlapping placements for the same horse
        overlapping = Placement.objects.filter(horse=self.horse)
        if self.pk:
            overlapping = overlapping.exclude(pk=self.pk)

        # A placement overlaps if it starts before this one ends
        # and ends after this one starts (or is still open)
        if self.end_date:
            # This placement has a defined range
            overlapping = overlapping.filter(
                start_date__lte=self.end_date
            ).exclude(
                end_date__isnull=False, end_date__lt=self.start_date
            )
        else:
            # This placement is open-ended — overlaps with anything
            # that hasn't ended before this one starts
            overlapping = overlapping.exclude(
                end_date__isnull=False, end_date__lt=self.start_date
            )

        if overlapping.exists():
            conflict = overlapping.first()
            end = conflict.end_date or "present"
            raise ValidationError(
                f"{self.horse.name} already has a placement from "
                f"{conflict.start_date} to {end} that overlaps with these dates."
            )

    def save(self, *args, **kwargs):
        # Always validate overlaps, even when clean() isn't called
        self.full_clean()

        # Lifecycle choke point. Horse.is_active and the field-usage history
        # must stay in step with placements no matter which path writes them
        # (services, placement forms, Django admin, future code) — patching
        # each caller individually is how horses ended up stranded as
        # "departed but placed" in production. Detect the transition here.
        was_open = None
        creating = self.pk is None
        if not creating:
            was_open = Placement.objects.filter(
                pk=self.pk
            ).values_list('end_date', flat=True).first() is None

        from .services import LocationUsageService
        newly_open = self.end_date is None and (creating or not was_open)
        opening_was_empty = (
            LocationUsageService._is_empty(
                self.location, exclude_horse_ids=[self.horse_id]
            )
            if newly_open else False
        )

        super().save(*args, **kwargs)

        if self.end_date is None:
            # An open placement means the horse is on site — never let it
            # sit flagged departed while occupying a field.
            if not self.horse.is_active:
                self.horse.is_active = True
                self.horse.save(update_fields=['is_active'])
            if not creating and was_open is False:
                # Re-opened (departure undone): remove the automatic rest
                # the departure created, if any.
                LocationUsageService.undo_auto_rest(self.location)
            elif newly_open:
                # An auto-rest starting on/after this occupancy is bogus —
                # e.g. the close half of a same-field move created it a
                # moment ago.
                LocationUsageService.clear_auto_rest_from(
                    self.location, self.start_date
                )
                LocationUsageService.horses_arrived(
                    self.location, self.start_date, was_empty=opening_was_empty
                )
        elif was_open and self.end_date is not None:
            # Closed: rest the field if this was its last occupant.
            LocationUsageService.rest_if_empty(self.location, self.end_date)

    def delete(self, *args, **kwargs):
        # Same choke point for deletion: removing a field's last open
        # placement must still rest the field. (Queryset bulk deletes bypass
        # this, as they bypass any model delete.)
        was_open = self.end_date is None
        location = self.location
        super().delete(*args, **kwargs)
        if was_open:
            from django.utils import timezone
            from .services import LocationUsageService
            LocationUsageService.rest_if_empty(location, timezone.localdate())

    @property
    def is_current(self):
        return self.end_date is None

    @property
    def daily_rate(self):
        return self.rate_type.daily_rate

    def get_effective_dates_in_period(self, period_start, period_end):
        """Return (effective_start, effective_end) tuple for a billing period."""
        effective_start = max(self.start_date, period_start)
        effective_end = min(self.end_date or period_end, period_end)
        return (effective_start, effective_end)

    def get_days_in_period(self, period_start, period_end):
        """Calculate billable days within a billing period."""
        effective_start, effective_end = self.get_effective_dates_in_period(period_start, period_end)

        if effective_start > effective_end:
            return 0

        return (effective_end - effective_start).days + 1

    def calculate_charge(self, period_start, period_end):
        """Calculate the charge for this placement in a billing period."""
        days = self.get_days_in_period(period_start, period_end)
        return days * self.daily_rate


class HorseOwnership(models.Model):
    """Tracks fractional ownership of a horse by multiple owners.

    This allows horses to be owned by multiple owners with different
    percentage shares. Invoice charges are split according to these
    ownership percentages.
    """

    horse = models.ForeignKey(
        Horse,
        on_delete=models.CASCADE,
        related_name='ownerships'
    )
    owner = models.ForeignKey(
        Owner,
        on_delete=models.PROTECT,
        related_name='horse_ownerships'
    )
    share_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal('0.01')),
            MaxValueValidator(Decimal('100.00'))
        ],
        help_text="Ownership percentage (0.01 to 100.00)"
    )
    effective_from = models.DateField()
    effective_to = models.DateField(
        null=True,
        blank=True,
        help_text="Leave blank if ownership is ongoing"
    )
    is_billing_contact = models.BooleanField(
        default=False,
        help_text="Primary contact for billing communications about this horse"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-effective_from', 'owner__name']
        unique_together = [('horse', 'owner', 'effective_from')]
        verbose_name = "Horse Ownership"
        verbose_name_plural = "Horse Ownerships"

    def __str__(self):
        return f"{self.horse.name} - {self.owner.name} ({self.share_percentage}%)"

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()

        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValidationError("Effective end date cannot be before start date.")

    @property
    def is_current(self):
        """Check if this ownership is currently active."""
        today = timezone.localdate()
        if self.effective_from > today:
            return False
        if self.effective_to and self.effective_to < today:
            return False
        return True

    @classmethod
    def get_ownership_shares(cls, horse, as_of_date=None):
        """Get all active ownership shares for a horse on a given date.

        Returns a list of (owner, share_percentage) tuples.
        If no ownership records exist, returns empty list.
        """
        if as_of_date is None:
            as_of_date = timezone.localdate()

        ownerships = cls.objects.filter(
            horse=horse,
            effective_from__lte=as_of_date,
        ).filter(
            models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=as_of_date)
        ).select_related('owner')

        return [(o.owner, o.share_percentage) for o in ownerships]

    @classmethod
    def get_ownership_for_period(cls, horse, period_start, period_end):
        """Get ownership shares that overlap with a billing period.

        Returns a list of dicts with owner, percentage, and effective dates.
        """
        ownerships = cls.objects.filter(
            horse=horse,
            effective_from__lte=period_end,
        ).filter(
            models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=period_start)
        ).select_related('owner')

        result = []
        for ownership in ownerships:
            eff_start = max(ownership.effective_from, period_start)
            eff_end = min(ownership.effective_to or period_end, period_end)
            result.append({
                'owner': ownership.owner,
                'percentage': ownership.share_percentage,
                'effective_start': eff_start,
                'effective_end': eff_end,
            })
        return result


class OwnershipShare(models.Model):
    """Fractional ownership of a horse. Shares for a horse must total <= 100%."""

    horse = models.ForeignKey(
        Horse,
        on_delete=models.CASCADE,
        related_name='ownership_shares'
    )
    owner = models.ForeignKey(
        Owner,
        on_delete=models.PROTECT,
        related_name='ownership_shares'
    )
    share_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal('0.01')),
            MaxValueValidator(Decimal('100.00')),
        ],
        help_text="Ownership percentage (e.g. 50.00 for 50%)"
    )
    is_primary_contact = models.BooleanField(
        default=False,
        help_text="Primary contact for this horse"
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-share_percentage']
        unique_together = [('horse', 'owner')]
        indexes = [
            models.Index(fields=['horse', 'owner'], name='ownership_horse_owner'),
        ]

    def __str__(self):
        return f"{self.owner.name} owns {self.share_percentage}% of {self.horse.name}"

    @property
    def share_fraction(self):
        """Return share as a decimal fraction (e.g. 0.50 for 50%)."""
        return self.share_percentage / Decimal('100')

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if not self.horse_id:
            return
        existing = OwnershipShare.objects.filter(horse=self.horse)
        if self.pk:
            existing = existing.exclude(pk=self.pk)
        total = sum(s.share_percentage for s in existing) + (self.share_percentage or Decimal('0'))
        if total > Decimal('100.00'):
            raise ValidationError(
                f"Total ownership for {self.horse.name} would be {total}%, "
                f"which exceeds 100%."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class BusinessSettings(models.Model):
    """Singleton model for business configuration."""

    business_name = models.CharField(max_length=200, default="Horse Livery")
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    vat_registration = models.CharField(
        max_length=50,
        blank=True,
        default="N/A",
        help_text="VAT registration number, or N/A if not registered"
    )
    vat_rate = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text=(
            "VAT percentage added to invoices: 0 if not VAT-registered, "
            "20 for the UK standard rate. Applies to newly created invoices."
        ),
    )
    logo = models.ImageField(
        upload_to='business/', blank=True, null=True,
        validators=[
            FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'heic', 'heif']),
            validate_file_size,
        ],
    )
    bank_details = models.TextField(blank=True, help_text="Bank details for payment")
    card_payment_url = models.URLField(
        blank=True,
        help_text="URL for online card payment (e.g. SumUp link)"
    )
    default_payment_terms = models.PositiveIntegerField(
        default=30,
        help_text="Default payment terms in days"
    )
    invoice_prefix = models.CharField(max_length=10, default="INV")
    next_invoice_number = models.PositiveIntegerField(default=1)
    auto_generate_invoices = models.BooleanField(
        default=True,
        help_text=(
            "Automatically create draft invoices for the previous month "
            "on the 1st (drafts still need reviewing and sending)"
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Business Settings"
        verbose_name_plural = "Business Settings"

    def __str__(self):
        return self.business_name

    def save(self, *args, **kwargs):
        # Ensure only one instance exists
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        """Get or create the singleton settings instance."""
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

    def get_next_invoice_number(self):
        """Get and atomically increment the next invoice number.

        Uses select_for_update inside a transaction so that two concurrent
        invoice creations cannot read the same value and emit duplicate
        invoice numbers.
        """
        with transaction.atomic():
            locked = BusinessSettings.objects.select_for_update().get(pk=self.pk)
            number = locked.next_invoice_number
            locked.next_invoice_number = number + 1
            locked.save(update_fields=['next_invoice_number'])
        self.next_invoice_number = number + 1
        return f"{self.invoice_prefix}{number:05d}"


class DashboardPreference(models.Model):
    """Per-user home dashboard layout (widget visibility + order)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dashboard_preference',
    )
    # {"attention": {"visible": True, "order": 0}, ...}
    layout = models.JSONField(default=dict, blank=True)
    # The site the dashboard's site switch was last set to; blank = all sites.
    site = models.CharField(max_length=100, blank=True, default='')
    # The location the Near you card highlights when GPS has no answer.
    pinned_location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='pinned_by',
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"DashboardPreference for {self.user}"

    @classmethod
    def get_for(cls, user):
        """Get-or-create the user's preference row (mirrors BusinessSettings.get_settings)."""
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    def resolved_layout(self):
        """Merge stored layout over DEFAULT_LAYOUT.

        New widgets added to the registry appear visible at the end of their
        group's order. Stored keys no longer in the registry are ignored.
        """
        from .dashboard_widgets import DEFAULT_LAYOUT, WIDGETS_BY_KEY
        resolved = {}
        for key, default in DEFAULT_LAYOUT.items():
            stored = self.layout.get(key) if isinstance(self.layout, dict) else None
            if isinstance(stored, dict):
                resolved[key] = {
                    "visible": bool(stored.get("visible", default["visible"])),
                    "order": int(stored.get("order", default["order"])),
                }
            else:
                resolved[key] = dict(default)
        # Drop any stale keys (defensive — DEFAULT_LAYOUT is already the filter).
        return {k: v for k, v in resolved.items() if k in WIDGETS_BY_KEY}

    def visible_keys(self):
        """The set of widget keys this user sees (visible, and allowed)."""
        return {key for keys in self.visible_ordered_keys_by_group().values() for key in keys}

    def visible_ordered_keys_by_group(self):
        """Return {group: [key, ...]} filtered to visible keys, sorted by order.

        Widgets tied to a feature area the user's role can't view are
        dropped regardless of stored preference.
        """
        from .dashboard_widgets import GROUPS, WIDGETS_BY_KEY
        from .permissions import access_map
        levels = access_map(self.user)
        layout = self.resolved_layout()
        grouped = {g: [] for g in GROUPS}
        ordered = sorted(layout.items(), key=lambda kv: kv[1]["order"])
        for key, meta in ordered:
            if not meta["visible"]:
                continue
            widget = WIDGETS_BY_KEY[key]
            if levels[widget["feature"]] == "hidden":
                continue
            grouped[widget["group"]].append(key)
        return grouped


class Document(models.Model):
    """A file attached to a horse or an owner: passport scan, insurance
    certificate, registration papers, loan agreement, etc.

    Documents with an expiry date are chased by the daily reminder task
    (notifications.tasks.send_document_expiry_reminders) so an insurance
    certificate can't lapse silently.
    """

    class DocType(models.TextChoices):
        PASSPORT = 'passport', 'Passport'
        INSURANCE = 'insurance', 'Insurance Certificate'
        REGISTRATION = 'registration', 'Registration Papers'
        LOAN_AGREEMENT = 'loan_agreement', 'Loan Agreement'
        VET_REPORT = 'vet_report', 'Vet Report'
        OTHER = 'other', 'Other'

    horse = models.ForeignKey(
        Horse,
        on_delete=models.CASCADE,
        related_name='documents',
        null=True,
        blank=True,
    )
    owner = models.ForeignKey(
        Owner,
        on_delete=models.CASCADE,
        related_name='documents',
        null=True,
        blank=True,
    )
    doc_type = models.CharField(
        max_length=20,
        choices=DocType.choices,
        default=DocType.OTHER,
    )
    title = models.CharField(max_length=200)
    file = models.FileField(
        upload_to='documents/%Y/%m/',
        validators=[
            FileExtensionValidator(allowed_extensions=[
                'pdf', 'jpg', 'jpeg', 'png', 'webp', 'heic', 'heif',
                'doc', 'docx',
            ]),
            validate_file_size,
        ],
    )
    expiry_date = models.DateField(
        null=True,
        blank=True,
        help_text="Leave blank if the document doesn't expire",
    )
    expiry_reminder_sent = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_documents',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        attached_to = self.horse or self.owner
        return f"{self.get_doc_type_display()}: {self.title} ({attached_to})"

    def clean(self):
        if not self.horse and not self.owner:
            raise DjangoValidationError(
                "A document must be attached to a horse or an owner."
            )

    def save(self, *args, **kwargs):
        # A changed expiry date re-arms the reminder.
        if self.pk:
            old = Document.objects.filter(pk=self.pk).values_list(
                'expiry_date', flat=True
            ).first()
            if old != self.expiry_date:
                self.expiry_reminder_sent = False
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        from django.utils import timezone
        return bool(self.expiry_date) and self.expiry_date < timezone.localdate()


class HorsePhoto(models.Model):
    """A quick-add photo on a horse's record: condition shots, markings,
    injuries, arrival/check-in snaps.

    Passport photos are deliberately not a category here — the quick-add
    flow routes those to Document (doc_type=passport) so the documents card
    and expiry reminders keep working.
    """

    class Category(models.TextChoices):
        CONDITION = 'condition', 'Condition'
        MARKINGS = 'markings', 'Markings'
        INJURY = 'injury', 'Injury'
        ARRIVAL = 'arrival', 'Arrival / check-in'
        OTHER = 'other', 'Other'

    horse = models.ForeignKey(
        Horse,
        on_delete=models.CASCADE,
        related_name='photos',
    )
    image = models.ImageField(
        upload_to='horse_photos/%Y/%m/',
        validators=[
            FileExtensionValidator(allowed_extensions=[
                'jpg', 'jpeg', 'png', 'webp', 'heic', 'heif',
            ]),
            validate_file_size,
        ],
    )
    # Square rendition generated from image on save — the grid renders this
    thumb = models.ImageField(
        upload_to='horse_photos/thumbs/%Y/%m/',
        null=True,
        blank=True,
        editable=False,
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.CONDITION,
    )
    caption = models.CharField(max_length=200, blank=True)
    taken_at = models.DateTimeField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_horse_photos',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.get_category_display()} photo of {self.horse}"

    def save(self, *args, **kwargs):
        # Photos are immutable once uploaded, so unlike Horse.photo_thumb
        # there is no change-diffing: generate the thumb once, on first save.
        # Generation failures degrade to "no thumbnail" — the grid falls
        # back to the original image.
        if self.image and not self.thumb:
            from .images import GRID_THUMB_SIZE, make_avatar_thumbnail
            thumb = make_avatar_thumbnail(self.image, size=GRID_THUMB_SIZE)
            if thumb is not None:
                base = Path(self.image.name).stem if self.image.name else 'photo'
                self.thumb.save(f"{base}-thumb.jpg", thumb, save=False)
        super().save(*args, **kwargs)


class Role(models.Model):
    """A named staff role with a per-feature access map (the Role Suite).

    ``access`` stores ``{"horses": "full", "invoices": "view", ...}`` keyed
    by ``core.features`` registry keys. Resolution merges it over registry
    defaults so features added later appear automatically (hidden) without a
    data migration — the same pattern as ``DashboardPreference.resolved_layout``.
    """

    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    # The seeded Administrator role: cannot be deleted and always resolves
    # to full access everywhere, so a yard can never lock itself out.
    is_system = models.BooleanField(default=False)
    access = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def resolved_access(self):
        """Feature→level map covering every registry feature."""
        from .features import ALL_FULL, DEFAULT_LEVELS, clamp_level
        if self.is_system:
            return dict(ALL_FULL)
        resolved = dict(DEFAULT_LEVELS)
        stored = self.access if isinstance(self.access, dict) else {}
        for key in resolved:
            if key in stored:
                resolved[key] = clamp_level(key, stored[key])
        return resolved


class UserRole(models.Model):
    """Assignment of a user to a role. Users without one see nothing."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='role_assignment',
    )
    # PROTECT: deleting a role with members requires reassigning them first.
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name='assignments')
    assigned_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} → {self.role}"


# Invoice and InvoiceLineItem have been moved to invoicing.models.
# Re-exported here for backward compatibility with existing imports.
from invoicing.models import Invoice, InvoiceLineItem  # noqa: F401
