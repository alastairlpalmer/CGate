"""Who may open C-vet, and the only way to read a practice's records.

docs/C_VET_PLAN.md, sections 3 and 4. Three rules live here so the views
cannot get them wrong:

* **The gate.** A view runs only for a signed-in user with Full on the
  ``cvet`` feature who is an active member of an active practice. The
  practice is put on ``request.practice``.
* **The scope.** Every read of a clinical record goes through
  ``notes_for``, ``visits_for`` or ``files_for`` with that practice. No view takes a practice id from a
  URL or a form, so one practice can never reach another's notes.
* **The link.** The practice sees the yard's horses only while its link
  to the yard is open (``horses_for``).

A yard login, an administrator included, is not a member of a practice.
It passes none of these, by any route.

A Vet login asks to join when the database has one practice: the first
time it opens C-vet, it becomes a waiting member (``join_the_only_practice``).
It reads nothing until a member of the practice approves it, on the
People page (``approve``). The first member of a practice with no
approved member is approved at once. The yard's administrator makes the
practice, and takes a login out of it, on the set-up page
(``cvet.setup``), but cannot let a login in.
"""

from functools import wraps

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from core.permissions import LEVEL_FULL, LEVEL_HIDDEN, access_map, feature_required, has_feature_access

from .models import (
    Bill, ClinicalFile, ClinicalNote, Client, ClientHorse, NoteLine, Practice, PracticeMember, PriceItem, Visit,
)

FEATURE = 'cvet'


def membership_for(user):
    """The user's approved, active membership of an active practice, or None."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    return (
        PracticeMember.objects.select_related('practice')
        .filter(user=user, is_active=True, practice__is_active=True, approved_at__isnull=False)
        .first()
    )


def waiting_membership(user):
    """The user's active membership that a member has not approved yet, or None."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    return (
        PracticeMember.objects.select_related('practice')
        .filter(user=user, is_active=True, practice__is_active=True, approved_at__isnull=True)
        .first()
    )


def new_member(practice, user, **fields):
    """Make a membership row: approved at once only as the practice's first member.

    Every other new member waits for a member's approval (``approve``).
    """
    from django.utils import timezone

    first = not PracticeMember.objects.filter(
        practice=practice, is_active=True, approved_at__isnull=False,
    ).exists()
    if first and fields.get('is_active', True):
        fields['approved_at'] = timezone.now()
    return PracticeMember.objects.create(practice=practice, user=user, **fields)


def waiting_for(practice):
    """The practice's members who wait for approval, oldest first."""
    return (
        PracticeMember.objects.filter(practice=practice, is_active=True, approved_at__isnull=True)
        .select_related('user').order_by('created_at')
    )


def approve(practice, member_pk, by):
    """Let a waiting member in. Returns the member, or None."""
    from django.utils import timezone

    member = waiting_for(practice).filter(pk=member_pk).first()
    if member is None:
        return None
    member.approved_at = timezone.now()
    member.approved_by = by
    member.save(update_fields=['approved_at', 'approved_by'])
    return member


def decline(practice, member_pk):
    """Refuse a waiting member. The row stays, inactive, so the login does
    not ask again by itself. Returns the member, or None."""
    member = waiting_for(practice).filter(pk=member_pk).first()
    if member is None:
        return None
    member.is_active = False
    member.save(update_fields=['is_active'])
    return member


def practice_for(user):
    """The practice whose records this user may read, or None.

    Both halves are needed: the role (Full on ``cvet``) and the
    membership. A role alone names no practice; a membership alone can be
    left behind when a role is taken away.
    """
    if not getattr(user, 'is_authenticated', False):
        return None
    if not has_feature_access(user, FEATURE, LEVEL_FULL):
        return None
    membership = membership_for(user)
    return membership.practice if membership else None


def is_vet_only(user):
    """True for a practice login (the Vet role): C-vet and nothing else.

    Full on ``cvet`` and no dashboard. A staff role that also has C-vet
    (an administrator who tests it) keeps the dashboard, so this stays
    False for it. ``cvet.middleware.VetPortalMiddleware`` fences these
    users in.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return _is_vet_only_map(access_map(user))


def _is_vet_only_map(levels):
    return levels.get(FEATURE) == LEVEL_FULL and levels.get('dashboard') == LEVEL_HIDDEN


def vet_logins():
    """The active logins that ``is_vet_only`` is true for, by name.

    The same rule, asked of the roles table: a superuser has every
    feature, the dashboard included, so it is never one of these.
    """
    from django.contrib.auth import get_user_model

    from core.models import Role

    roles = [role.pk for role in Role.objects.all() if _is_vet_only_map(role.resolved_access())]
    return (
        get_user_model().objects
        .filter(is_active=True, is_superuser=False, role_assignment__role_id__in=roles)
        .order_by('first_name', 'last_name', 'email', 'username')
    )


def only_practice():
    """The practice, when the database has exactly one active practice."""
    practices = list(Practice.objects.filter(is_active=True)[:2])
    return practices[0] if len(practices) == 1 else None


def join_the_only_practice(user):
    """Put a Vet login in the practice as a waiting member, when there is
    no doubt which one.

    With one practice in the database, the role names it, so the vet need
    not wait for the yard. But the yard's administrator gives the role,
    so the vet reads nothing until a member of the practice approves them
    (``approve``). Only the practice's first member is let in at once.

    Three limits keep the rule safe:

    * Only a Vet login (``is_vet_only``). An administrator has C-vet in
      its role too, and is never put in a practice by this.
    * Only a login that has no membership row. A login that was taken
      out of the practice (an inactive row) stays out.
    * Only when there is exactly one active practice. With two, a person
      must choose.

    Returns the practice the login may now read, or None.
    """
    if not is_vet_only(user):
        return None
    if PracticeMember.objects.filter(user=user).exists():
        return None
    practice = only_practice()
    if practice is None:
        return None
    from django.db import IntegrityError, transaction

    try:
        with transaction.atomic():
            new_member(practice, user)
    except IntegrityError:
        pass  # Two first requests at the same time: the other made the row.
    return practice_for(user)


def open_link(practice):
    """The practice's open link to the yard, or None."""
    link = getattr(practice, 'yard_link', None) if practice is not None else None
    return link if link is not None and link.is_open else None


def clients_for(practice):
    """The practice's clients. Its own list: no yard screen reads it."""
    return Client.objects.for_practice(practice)


def client_horses_for(practice):
    """The horses of the practice's clients that C-gate does not hold."""
    return ClientHorse.objects.for_practice(practice)


def notes_for(practice):
    """The clinical notes this practice may read. The only door to them."""
    return ClinicalNote.objects.for_practice(practice)


def current_notes_for(practice):
    """The notes the vet sees in the app.

    While CVET_TEST_MODE is on, the test notes; after it is turned off,
    the real ones. Test notes that were not deleted then stay out of the
    horse's history and out of what is due.
    """
    return notes_for(practice).filter(is_test=settings.CVET_TEST_MODE)


def prices_for(practice):
    """The practice's price list."""
    return PriceItem.objects.for_practice(practice)


def lines_for(practice):
    """The items done and their prices, for this practice."""
    return NoteLine.objects.for_practice(practice)


def bills_for(practice):
    """The practice's bills."""
    return Bill.objects.for_practice(practice)


def visits_for(practice):
    """The visits of this practice."""
    return Visit.objects.for_practice(practice)


def files_for(practice):
    """The clinical photos and recordings of this practice."""
    return ClinicalFile.objects.for_practice(practice)


def horses_for(practice):
    """The yard's active horses, while the practice's link to the yard is open."""
    from core.models import Horse

    if open_link(practice) is None:
        return Horse.objects.none()
    return Horse.objects.filter(is_active=True)


def cvet_enabled(view_func):
    """404 unless C-vet is turned on for this deployment."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not settings.CVET_ENABLED:
            raise Http404
        return view_func(request, *args, **kwargs)
    return _wrapped


def practice_page(view_func):
    """Gate for a C-vet page. Puts the practice on ``request.practice``."""
    @wraps(view_func)
    @cvet_enabled
    @feature_required(FEATURE, LEVEL_FULL)
    def _wrapped(request, *args, **kwargs):
        practice = practice_for(request.user) or join_the_only_practice(request.user)
        if practice is None:
            waiting = waiting_membership(request.user)
            if waiting is not None:
                return render(request, 'cvet/waiting.html', {'practice': waiting.practice}, status=403)
            return render(request, 'cvet/no_practice.html', {
                'can_set_up': has_feature_access(request.user, 'users', LEVEL_FULL),
            }, status=403)
        request.practice = practice
        return view_func(request, *args, **kwargs)
    return _wrapped


def practice_api(view_func):
    """Gate for a C-vet JSON endpoint: answers in JSON, never with a redirect.

    A phone that gets a sign-in page back cannot tell it from a failure.
    401 means "sign in again": the phone keeps every unsent record and
    shows a link. 403 means this login may not use C-vet.
    """
    @wraps(view_func)
    @cvet_enabled
    @never_cache
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'signed_out'}, status=401)
        practice = practice_for(request.user) or join_the_only_practice(request.user)
        if practice is None:
            if waiting_membership(request.user) is not None:
                return JsonResponse({'error': 'waiting_approval'}, status=403)
            return JsonResponse({'error': 'no_access'}, status=403)
        request.practice = practice
        return view_func(request, *args, **kwargs)
    return _wrapped
