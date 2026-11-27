"""The public marketing pages, for visitors who are not signed in.

``MARKETING_SITE_ENABLED`` turns the site on. While it is off, ``/``
works as it did before (the dashboard, or the sign-in page for a
visitor) and the other marketing pages are "not found". This lets the
site be built in steps and turned on when the words are ready.
"""

import logging

from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.html import escape
from django.views.decorators.http import require_GET, require_http_methods

from . import content
from .forms import ContactForm
from .models import ContactMessage

logger = logging.getLogger(__name__)


def site_enabled():
    return bool(getattr(settings, 'MARKETING_SITE_ENABLED', False))


def _wants_the_app(request):
    """True when ``/`` must be the dashboard (or its sign-in redirect).

    Only a plain page load by a visitor gets the Home page. A request
    from inside the app (htmx, a background poll), a form post, or the
    installed phone app (its start address is ``/?source=app``) keeps
    the old behaviour: a signed-out user goes to the sign-in page.
    """
    return (
        request.user.is_authenticated
        or not site_enabled()
        or request.method not in ('GET', 'HEAD')
        or request.headers.get('HX-Request')
        or request.headers.get('X-Background')
        or request.GET.get('source') == 'app'
    )


def root(request):
    """``/``: the dashboard for a signed-in user, Home for a visitor."""
    if _wants_the_app(request):
        from core.views import dashboard

        response = dashboard(request)
    else:
        response = render(request, 'marketing/home.html', {
            'page': 'home', 'features': content.FEATURES[:6],
        })
    # The same address gives two pages, chosen by the session cookie.
    patch_vary_headers(response, ('Cookie',))
    return response


def _page(template, name, extra=None):
    @require_GET
    def view(request):
        if not site_enabled():
            raise Http404
        return render(request, template, {'page': name, **(extra or {})})
    view.__name__ = name
    return view


features = _page('marketing/features.html', 'features', {'features': content.FEATURES})
pricing = _page('marketing/pricing.html', 'pricing', {
    'plans': content.PLANS, 'example_horses': content.EXAMPLE_HORSES, 'faq': content.PRICING_FAQ, 'large_yard': content.LARGE_YARD_HORSES,
})
about = _page('marketing/about.html', 'about')


@require_http_methods(['GET', 'HEAD', 'POST'])
def contact(request):
    """The Contact page and its form.

    A message is saved first, then emailed to MARKETING_CONTACT_EMAIL. A
    bot (the hidden field filled) is thanked and nothing is saved. Too many
    sends from one address in an hour are refused.
    """
    from core import ratelimit
    from core.audit import client_ip

    if not site_enabled():
        raise Http404
    sent = request.GET.get('sent') == '1'
    initial = {}
    if request.GET.get('topic') in ContactMessage.Topic.values:
        initial['topic'] = request.GET['topic']
    form = ContactForm(initial=initial)
    status = 200
    if request.method == 'POST':
        form = ContactForm(request.POST)
        if form.is_bot():
            return redirect(f"{request.path}?sent=1")
        ip = client_ip(request) or ''
        if not ratelimit.allow('marketing-contact', ip, settings.MARKETING_CONTACT_LIMIT_PER_HOUR, 3600):
            form.add_error(None, 'Too many messages from this address. Try again in an hour, or email us.')
            status = 429
        elif form.is_valid():
            message = form.save()
            _email_team(request, message)
            return redirect(f"{request.path}?sent=1")
        else:
            status = 400
    return render(request, 'marketing/contact.html', {
        'page': 'contact', 'form': form, 'sent': sent,
    }, status=status)


def _email_team(request, message):
    """Send the message to the team. A failure is logged; the message stays saved."""
    from notifications import mail

    to = [address for address in settings.MARKETING_CONTACT_EMAIL.split(',') if address.strip()]
    if not to:
        return
    rows = [
        ('Topic', message.get_topic_display()), ('Name', message.name), ('Email', message.email),
        ('Phone', message.phone), ('Yard', message.yard_name),
        ('Horses', '' if message.horses is None else str(message.horses)),
    ]
    body = ''.join(f'<p><strong>{label}:</strong> {escape(value)}</p>' for label, value in rows if value)
    if message.message:
        body += '<p><strong>Message:</strong></p><p>' + escape(message.message).replace('\n', '<br>') + '</p>'
    try:
        email = mail.message(f'Cgate website: {message.get_topic_display()} from {message.name}'[:200],
                             body, [address.strip() for address in to])
        email.reply_to = [message.email]
        email.send()
    except Exception:
        logger.exception('Contact message %s was saved but not emailed', message.pk)
        return
    message.emailed_at = timezone.now()
    message.save(update_fields=['emailed_at'])
