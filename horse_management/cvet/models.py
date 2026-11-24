"""Models for C-vet (docs/C_VET_PLAN.md).

The practice side: ``Practice``, ``PracticeMember``, ``PracticeYardLink``.
The practice's clients: ``Client`` and, for a client that is not in
C-gate, its horses, ``ClientHorse``.
The clinical records: ``Visit``, ``ClinicalNote``, ``ClinicalFile``, and
the practice's money: ``PriceItem``, ``NoteLine``, ``Bill``, ``BillLine``.
They belong to a practice, not to the yard. Read them only through
``cvet.access``; no code outside this app may import them
(cvet/tests_ownership.py checks).

``SyncReceipt`` is how each note arrived from the phone. It holds no
clinical text, so the yard's administrator may read it.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords


class Practice(models.Model):
    """A veterinary practice: the owner of the clinical records in C-vet.

    The practice is a separate organisation from the yard. The yard's
    staff, administrators included, are not members and see none of its
    clinical records. Today there is one practice and one yard in one
    database; the rule is the same when there are many.
    """

    name = models.CharField(max_length=200, unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    vat_number = models.CharField('VAT number', max_length=30, blank=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Untick to close the practice's access. Its records are kept.",
    )
    # The practice's bills (cvet.billing). Numbers run 1, 2, 3 with no
    # gaps, as an invoice sequence must; test bills count apart.
    bill_prefix = models.CharField(
        max_length=10, blank=True,
        help_text='Letters before a bill number, for example "HV" for HV-0001. Empty gives "CV".',
    )
    next_bill_number = models.PositiveIntegerField(default=1, editable=False)
    next_test_bill_number = models.PositiveIntegerField(default=1, editable=False)
    bank_details = models.TextField(blank=True, help_text="How to pay. Printed at the foot of each bill.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords(
        excluded_fields=['updated_at', 'next_bill_number', 'next_test_bill_number'],
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class PracticeMember(models.Model):
    """A login that works for a practice.

    One login belongs to one practice at most. The login also needs a
    role with Full on the ``cvet`` feature (the seeded Vet role); this
    row says whose records it then sees.
    """

    class Kind(models.TextChoices):
        VET = 'vet', 'Vet'
        OFFICE = 'office', 'Practice office'

    practice = models.ForeignKey(Practice, on_delete=models.CASCADE, related_name='members')
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='practice_membership',
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.VET)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Who could read a practice's clinical records, and when.
    history = HistoricalRecords()

    class Meta:
        ordering = ['practice', 'user']

    def __str__(self):
        return f'{self.user} at {self.practice}'


class PracticeYardLink(models.Model):
    """The agreement between a practice and this yard.

    While the link is open, the practice sees the yard's horse list
    (identity, owner, who pays, field name) and the yard receives the
    dates it needs from the practice. Close the link (``ended_on``) and
    both stop. The clinical notes never cross it in either state.

    One per practice for now: the yard is the whole database. When C-gate
    holds more than one yard, this gets a ``yard`` key and a practice has
    one link for each yard it visits.
    """

    class BillTo(models.TextChoices):
        YARD = 'yard', 'The yard (the yard invoices its owners)'
        OWNERS = 'owners', 'Each horse owner directly'

    class BillWhen(models.TextChoices):
        VISIT = 'visit', 'After each visit'
        MONTH = 'month', 'Once a month'

    practice = models.OneToOneField(Practice, on_delete=models.CASCADE, related_name='yard_link')
    # Who the practice's client is at this yard. Normally the yard: the
    # vet deals with the yard's owner, bills the yard for the visit, and
    # the yard puts each horse's part on its owner's invoice. Some yards
    # have the vet bill each owner; then co-owners share a horse's part.
    bill_to = models.CharField(max_length=10, choices=BillTo.choices, default=BillTo.YARD)
    # When the practice makes its bill. The vet or the practice office
    # chooses, on the Bills page of C-vet.
    bill_when = models.CharField(max_length=10, choices=BillWhen.choices, default=BillWhen.VISIT)
    # The practice as the yard already knows it: the entry in the yard's
    # own list of providers, which its vet visit log and charges use.
    service_provider = models.OneToOneField(
        'billing.ServiceProvider', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='practice_link', limit_choices_to={'provider_type': 'vet'},
        help_text="This practice in the yard's list of service providers, if it is there.",
    )
    started_on = models.DateField(default=timezone.localdate)
    ended_on = models.DateField(
        null=True, blank=True,
        help_text="Set a date to end the link. The practice then sees no horses of this yard.",
    )
    # What the practice sends back to the yard (phase 6 reads these).
    sends_vaccination_dates = models.BooleanField(
        default=True, help_text="The yard receives vaccination dates and next due dates.",
    )
    sends_follow_up_dates = models.BooleanField(
        default=True, help_text="The yard receives follow-up dates, with no reason.",
    )
    sends_charges = models.BooleanField(
        default=True,
        help_text="When the yard is billed: each horse's part of the bill goes into C-gate "
                  "as a charge, so the yard's invoices bill each owner.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    def __str__(self):
        return f'{self.practice} and the yard'

    @property
    def is_open(self):
        today = timezone.localdate()
        return self.started_on <= today and (self.ended_on is None or self.ended_on > today)


class PracticeScopedQuerySet(models.QuerySet):
    def for_practice(self, practice):
        """The rows one practice may read. ``None`` gives no rows."""
        if practice is None:
            return self.none()
        return self.filter(practice=practice)


class Client(models.Model):
    """One client of a practice: a livery yard, a stud, a racing yard, an owner.

    The practice's own list. The vet makes it in C-vet, and no yard screen
    shows it. One client can be the C-gate yard (``yard_link``): its
    horses come from C-gate, and its bills and dates go back there. Every
    other client holds its own horses (``ClientHorse``) and nothing of it
    reaches C-gate.

    The phone makes ``client_id``, so a client that is saved twice is
    stored once, and a client can be made at a yard with no signal.
    """

    class Kind(models.TextChoices):
        YARD = 'yard', 'Livery yard'
        STUD = 'stud', 'Stud'
        RACING = 'racing', 'Racing yard'
        COMPETITION = 'competition', 'Competition yard'
        PRIVATE = 'private', 'Private owner'
        OTHER = 'other', 'Other'

    practice = models.ForeignKey(Practice, on_delete=models.PROTECT, related_name='clients')
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.YARD)
    contact_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    # Set for the one client that is the C-gate yard.
    yard_link = models.OneToOneField(
        PracticeYardLink, on_delete=models.SET_NULL, null=True, blank=True, related_name='client',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'Client {self.pk}'

    @property
    def is_yard(self):
        return self.yard_link_id is not None


class ClientHorse(models.Model):
    """A horse of a client that is not in C-gate. The practice's own record."""

    practice = models.ForeignKey(Practice, on_delete=models.PROTECT, related_name='+')
    practice_client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='horses')
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    microchip = models.CharField(max_length=50, blank=True)
    passport = models.CharField(max_length=100, blank=True)
    # What the horse looks like, in the vet's words: "Bay mare, 12yo".
    about = models.CharField(max_length=100, blank=True)
    # Who owns the horse, as the client says. Text only: not a C-gate owner.
    owner_name = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'Client horse {self.pk}'


class Visit(models.Model):
    """One day's work by one vet for one client: the notes made that day.

    It belongs to the practice. It is not ``health.ProviderVisit``, which
    is the yard's record of a farrier's day and of what the yard owes the
    farrier. A vet bills in the practice's name, so this visit never
    enters the yard's books.

    The phone makes ``client_id`` when the first note of the day is
    saved, and sends it with every note of that visit.
    """

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        FINISHED = 'finished', 'Finished'

    practice = models.ForeignKey(Practice, on_delete=models.PROTECT, related_name='visits')
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    author_name = models.CharField(max_length=200, blank=True)
    date = models.DateField(default=timezone.localdate)
    # The client the visit was for. Empty on a visit made before C-vet
    # had a client list: that visit was at the C-gate yard.
    practice_client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True, related_name='visits',
    )
    # The client's name and who its bill goes to, as they were when the
    # visit started. Copies: the bill for work done does not change when
    # the client is renamed or the arrangement changes later.
    yard_name = models.CharField(max_length=200, blank=True)
    bill_to = models.CharField(
        max_length=10, choices=PracticeYardLink.BillTo.choices, default=PracticeYardLink.BillTo.YARD,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    finished_at = models.DateTimeField(null=True, blank=True)
    is_test = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'Visit {self.pk} ({self.date})'

    @property
    def at_cgate_yard(self):
        """True when the visit was at the C-gate yard, so its dates and costs go there."""
        return self.practice_client is None or self.practice_client.is_yard


class ClinicalNote(models.Model):
    """What a vet found and did for one horse on one day.

    It belongs to the practice. The yard does not see it: no yard page,
    export, search or admin screen reads this table. The horse is linked
    with ``related_name='+'``, so a ``Horse`` has no way back to its
    notes, and the yard's code cannot reach them by accident.

    The horse's name and numbers are copied onto the note. The record
    must stay complete when the yard renames the horse, or deletes it.
    """

    practice = models.ForeignKey(Practice, on_delete=models.PROTECT, related_name='clinical_notes')
    visit = models.ForeignKey(
        Visit, on_delete=models.SET_NULL, null=True, blank=True, related_name='notes',
    )
    horse = models.ForeignKey(
        'core.Horse', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    # The horse, when it is a client's horse that C-gate does not hold.
    client_horse = models.ForeignKey(
        ClientHorse, on_delete=models.SET_NULL, null=True, blank=True, related_name='notes',
    )
    horse_name = models.CharField(max_length=200)
    horse_microchip = models.CharField(max_length=50, blank=True)
    horse_passport = models.CharField(max_length=100, blank=True)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    author_name = models.CharField(max_length=200, blank=True)

    # Made on the phone when the note is saved. A note that is sent twice
    # is stored once.
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)

    seen_on = models.DateField(default=timezone.localdate)
    # Empty when the note is a photo or a recording with no text.
    findings = models.TextField(blank=True)
    treatment = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)

    # Who pays, and each one's share, as the yard's records said when the
    # note arrived: [{"owner_id": 3, "name": "Jo Smith", "share": "50.00"}].
    # A copy, so that a later change of owner does not change who is
    # billed for work already done. The practice's invoices read it.
    payers = models.JSONField(default=list, blank=True)

    # When this note's dates and charges went into the yard's records, and
    # which yard records they made ({"vet_visit": 12, "vaccinations": [7]}).
    # Empty until the visit is finished; never set for a test note.
    yard_sent_at = models.DateTimeField(null=True, blank=True)
    yard_records = models.JSONField(default=dict, blank=True)

    # The phone's clock when the vet tapped Save.
    written_at = models.DateTimeField(default=timezone.now)
    # Made while CVET_TEST_MODE was on: not a clinical record, and the
    # yard's administrator may delete it (without reading it).
    is_test = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # A clinical record is corrected, never silently replaced: every
    # change keeps the text before it.
    history = HistoricalRecords(excluded_fields=['updated_at'])

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['-seen_on', '-written_at']
        indexes = [
            models.Index(fields=['practice', 'horse', '-seen_on'], name='cvet_note_practice_horse'),
        ]

    def __str__(self):
        # No clinical text here: this string reaches logs and error pages.
        return f'Clinical note {self.pk} ({self.seen_on})'


class PriceItem(models.Model):
    """One line of a practice's price list, for example "Flu/Tet booster".

    The practice's own data: the vet sets it in C-vet, and no yard screen
    shows it. The price is the one the client pays, VAT included; the VAT
    rate says how much of it is VAT.
    """

    practice = models.ForeignKey(Practice, on_delete=models.CASCADE, related_name='price_items')
    # Made on the phone, so that a price saved twice is stored once.
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    # A heading the practice chooses, for example "Vaccination": the item
    # list on the phone is grouped by it. Empty: under "Other".
    category = models.CharField(max_length=50, blank=True)
    price_inc_vat = models.DecimalField(max_digits=8, decimal_places=2)
    vat_rate = models.DecimalField('VAT rate (%)', max_digits=4, decimal_places=2, default=Decimal('20.00'))
    # Set when this item is a vaccination: giving it also records the
    # dose in the yard's vaccination records, so the yard's due dates and
    # reminders stay correct. The types are the yard's own list.
    vaccination_type = models.ForeignKey(
        'health.VaccinationType', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class NoteLine(models.Model):
    """One item done for a horse on a visit, with its price: a line of the bill.

    It belongs to a clinical note. The description, the price and the VAT
    rate are copied from the price list when the vet taps the item, so a
    later change of the price list does not change work already done.
    """

    practice = models.ForeignKey(Practice, on_delete=models.PROTECT, related_name='+')
    note = models.ForeignKey(ClinicalNote, on_delete=models.CASCADE, related_name='lines')
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    price_item = models.ForeignKey(
        PriceItem, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('1'))
    unit_price_inc_vat = models.DecimalField(max_digits=8, decimal_places=2)
    vat_rate = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('20.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['created_at', 'pk']

    def __str__(self):
        return f'Line {self.pk}'

    @property
    def total_inc_vat(self):
        return (self.quantity * self.unit_price_inc_vat).quantize(Decimal('0.01'), ROUND_HALF_UP)


class Bill(models.Model):
    """The practice's bill: to a client, or to one owner of a horse at the C-gate yard.

    For one visit, made when the vet taps Finish visit; or for a month of
    visits (cvet.billing). It is a copy of what was done and what it cost
    at that moment, and it never changes: work that arrives later goes on
    a further bill.
    """

    practice = models.ForeignKey(Practice, on_delete=models.PROTECT, related_name='bills')
    # The visit, for a bill made after a visit. Empty for a monthly bill,
    # which covers the visits from period_start to period_end.
    visit = models.ForeignKey(Visit, on_delete=models.PROTECT, null=True, blank=True, related_name='bills')
    # The client it is for. Empty on a bill made before C-vet had a client
    # list: that bill was for the C-gate yard.
    practice_client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True, related_name='bills',
    )
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    number = models.CharField(max_length=30)
    bill_to = models.CharField(max_length=10, choices=PracticeYardLink.BillTo.choices)
    # Who it is addressed to: the yard's name, or the owner's.
    to_name = models.CharField(max_length=200)
    to_owner = models.ForeignKey(
        'core.Owner', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    date = models.DateField()
    issued_at = models.DateTimeField(auto_now_add=True)
    total_inc_vat = models.DecimalField(max_digits=10, decimal_places=2)
    total_vat = models.DecimalField(max_digits=10, decimal_places=2)
    is_test = models.BooleanField(default=False)
    # The cost this bill made in the yard's books, when the yard is billed.
    yard_cost = models.ForeignKey(
        'billing.YardCost', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    emailed_at = models.DateTimeField(null=True, blank=True)
    # Where it was sent, when the practice sent it to a client of its own.
    emailed_to = models.CharField(max_length=254, blank=True)
    # The day the practice received the money. Empty: not paid yet. The
    # practice office sets it on the Bills page; nothing else reads it.
    paid_on = models.DateField(null=True, blank=True)

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['-issued_at', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['practice', 'number'], name='cvet_bill_number_once'),
        ]

    def __str__(self):
        return f'Bill {self.number}'

    @property
    def total_ex_vat(self):
        return self.total_inc_vat - self.total_vat


class BillLine(models.Model):
    """One line of a bill: a copy of an item done, or of an owner's part of it."""

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name='lines')
    note_line = models.ForeignKey(
        NoteLine, on_delete=models.SET_NULL, null=True, blank=True, related_name='bill_lines',
    )
    # The day of the visit the item was done on.
    date = models.DateField(default=timezone.localdate)
    horse_name = models.CharField(max_length=200)
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=6, decimal_places=2)
    unit_price_inc_vat = models.DecimalField(max_digits=8, decimal_places=2)
    vat_rate = models.DecimalField(max_digits=4, decimal_places=2)
    # The part of the item this bill carries: 100, or a co-owner's part.
    share = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('100.00'))
    amount_inc_vat = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ['pk']

    def __str__(self):
        return f'Bill line {self.pk}'


def clinical_file_path(instance, filename):
    # The name the phone sent is never used: only an extension, which the
    # view has already replaced with one from its own list. Files under
    # cvet/ are never served by the media view (horse_management/urls.py).
    extension = filename.rsplit('.', 1)[-1].lower()
    return f'cvet/{instance.kind}/{uuid.uuid4().hex}.{extension}'


class ClinicalFile(models.Model):
    """A photo or a voice recording that belongs to a clinical note.

    Served only by ``cvet.views.api_file``, to a member of the practice.
    ``practice`` repeats the note's practice so that the file can be
    scoped without a join, and stays scoped if the note is ever moved.
    """

    class Kind(models.TextChoices):
        PHOTO = 'photo', 'Photo'
        AUDIO = 'audio', 'Voice recording'

    practice = models.ForeignKey(Practice, on_delete=models.PROTECT, related_name='+')
    note = models.ForeignKey(ClinicalNote, on_delete=models.CASCADE, related_name='files')
    # Made on the phone. The address of the file is built from it.
    client_id = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    file = models.FileField(upload_to=clinical_file_path)
    content_type = models.CharField(max_length=50)
    bytes = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PracticeScopedQuerySet.as_manager()

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.get_kind_display()} {self.pk}'


class SyncReceipt(models.Model):
    """How one note arrived from the phone. No clinical text.

    The measures of the phone test (docs/C_VET_PLAN.md, section 8): how
    long the note waited on the phone, how many tries it took, and if
    its files arrived complete. The yard's administrator reads these in
    the Django admin. The note itself is not shown there.
    """

    practice = models.ForeignKey(Practice, on_delete=models.CASCADE, related_name='+')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    # Kept when the note is deleted, so the test results outlive the
    # test notes.
    note = models.OneToOneField(
        ClinicalNote, on_delete=models.SET_NULL, null=True, blank=True, related_name='receipt',
    )
    note_client_id = models.UUIDField(unique=True)
    is_test = models.BooleanField(default=False)

    # The phone's clock at Save, and the server's clock at arrival.
    device_created_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    # How many times the phone tried to send this note.
    attempts = models.PositiveIntegerField(default=1)
    text_length = models.PositiveIntegerField(default=0)
    photo_bytes = models.PositiveIntegerField(default=0)
    # True when the file that arrived has the SHA-256 the phone worked
    # out before it sent it. None: no file, or the phone sent no hash.
    photo_intact = models.BooleanField(null=True, blank=True)
    audio_bytes = models.PositiveIntegerField(default=0)
    audio_intact = models.BooleanField(null=True, blank=True)
    # What the phone reported about itself: browser, installed or not,
    # storage used, time to open. See static/cvet/app.js, clientInfo().
    client_info = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-received_at']
        verbose_name = 'sync receipt'

    def __str__(self):
        return f'Receipt {self.pk} ({self.received_at:%d %b %Y %H:%M})'

    @property
    def waited(self):
        """Time from Save on the phone to arrival on the server."""
        return self.received_at - self.device_created_at
