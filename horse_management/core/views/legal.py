"""Public legal pages. No sign-in is needed: app stores and Google's
sign-in review need a privacy policy link that anyone can open."""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from core.models import BusinessSettings

# Change this date each time the policy text changes.
PRIVACY_LAST_UPDATED = '11 November 2026'


@require_GET
def privacy_policy(request):
    return render(request, 'core/privacy.html', {
        'business': BusinessSettings.get_settings(),
        'last_updated': PRIVACY_LAST_UPDATED,
    })
