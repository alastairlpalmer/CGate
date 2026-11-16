"""Who may open C-vet, and the only way to read a practice's records.

docs/C_VET_PLAN.md, sections 3 and 4. Three rules live here so the views
cannot get them wrong:

* **The gate.** A view runs only for a signed-in user with Full on the
  ``cvet`` feature who is an active member of an active practice. The
  practice is put on ``request.practice``.
* **The scope.** Every read of a clinical record goes through
  ``notes_for`` with that practice. No view takes a practice id from a
  URL or a form, so one practice can never reach another's notes.
* **The link.** The practice sees the yard's horses only while its link
  to the yard is open (``horses_for``).

A yard login, an administrator included, is not a member of a practice.
It passes none of these, by any route.
"""

from functools import wraps

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from core.permissions import LEVEL_FULL, LEVEL_HIDDEN, access_map, feature_required, has_feature_access

from .models import ClinicalNote, PracticeMember

FEATURE = 'cvet'


def membership_for(user):
    """The user's active membership of an active practice, or None."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    return (
        PracticeMember.objects.select_related('practice')
        .filter(user=user, is_active=True, practice__is_active=True)
        .first()
    )


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
    levels = access_map(user)
    return levels.get(FEATURE) == LEVEL_FULL and levels.get('dashboard') == LEVEL_HIDDEN


def open_link(practice):
    """The practice's open link to the yard, or None."""
    link = getattr(practice, 'yard_link', None) if practice is not None else None
    return link if link is not None and link.is_open else None


def notes_for(practice):
    """The clinical notes this practice may read. The only door to them."""
    return ClinicalNote.objects.for_practice(practice)


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
        if not settings.CVET_PROTOTYPE_ENABLED:
            raise Http404
        return view_func(request, *args, **kwargs)
    return _wrapped


def practice_page(view_func):
    """Gate for a C-vet page. Puts the practice on ``request.practice``."""
    @wraps(view_func)
    @cvet_enabled
    @feature_required(FEATURE, LEVEL_FULL)
    def _wrapped(request, *args, **kwargs):
        practice = practice_for(request.user)
        if practice is None:
            return render(request, 'cvet/no_practice.html', status=403)
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
        practice = practice_for(request.user)
        if practice is None:
            return JsonResponse({'error': 'no_access'}, status=403)
        request.practice = practice
        return view_func(request, *args, **kwargs)
    return _wrapped
