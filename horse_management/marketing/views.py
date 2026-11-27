"""The public marketing pages, for visitors who are not signed in.

``MARKETING_SITE_ENABLED`` turns the site on. While it is off, ``/``
works as it did before (the dashboard, or the sign-in page for a
visitor) and the other marketing pages are "not found". This lets the
site be built in steps and turned on when the words are ready.
"""

import logging
from urllib.parse import urlencode

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
            'page': 'home', 'hero_horses': content.HERO_HORSES, 'showcase_tabs': content.SHOWCASE_TABS,
            'showcase_fields': content.SHOWCASE_FIELDS, 'showcase_invoices': content.SHOWCASE_INVOICES,
            'showcase_nav': content.SHOWCASE_NAV, 'showcase_stats': content.SHOWCASE_STATS,
            'showcase_week': content.SHOWCASE_WEEK, 'showcase_timeline': content.SHOWCASE_TIMELINE, 'plans': content.PLANS, 'addons': content.ADDONS, 'steps': content.STEPS,
            'beliefs': content.BELIEFS, 'partner_percent': content.PARTNER_PERCENT, 'offer_months': content.OFFER_MONTHS,
        })
    # The same address gives two pages, chosen by the session cookie.
    patch_vary_headers(response, ('Cookie',))
    return response


def _home_section(anchor):
    """The old Features and About pages are sections of Home now."""
    @require_GET
    def view(request):
        if not site_enabled():
            raise Http404
        return redirect(f'/#{anchor}', permanent=True)
    return view


features = _home_section('features')


@require_GET
def pricing(request):
    """The Pricing page and its calculator. ``?plan=pro&horses=22&addon=training`` opens it with that quote."""
    if not site_enabled():
        raise Http404
    addons = request.GET.getlist('addon') or [key for key in request.GET.get('addons', '').split(',') if key]
    chosen = content.quote(request.GET.get('horses') or content.EXAMPLE_HORSES,
                           request.GET.get('plan') or content.DEFAULT_PLAN, addons)
    return render(request, 'marketing/pricing.html', {
        'page': 'pricing', 'plans': content.PLANS, 'addons': content.ADDONS, 'quote': chosen,
        'faq': content.PRICING_FAQ, 'slider_max': content.SLIDER_MAX, 'max_horses': content.MAX_HORSES,
        'offer_months': content.OFFER_MONTHS, 'offer_percent': content.OFFER_PERCENT,
        'partner_percent': content.PARTNER_PERCENT,
    })


about = _home_section('about')


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
    if request.GET.get('horses'):
        # From the Pricing page form (plan=…&addon=…&addon=…) or a link (addons=a,b).
        addons = ','.join(request.GET.getlist('addon')) or request.GET.get('addons', '')
        initial['quote_spec'] = urlencode({
            'horses': request.GET['horses'], 'plan': request.GET.get('plan', content.DEFAULT_PLAN), 'addons': addons,
        })
        chosen = content.quote_from_spec(initial['quote_spec'])
        if chosen:
            initial['horses'] = chosen['horses']
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
            message = form.save(commit=False)
            chosen = content.quote_from_spec(form.cleaned_data.get('quote_spec'))
            if chosen:
                message.quote = content.quote_summary(chosen)[:500]
            message.save()
            _email_team(request, message)
            return redirect(f"{request.path}?sent=1")
        else:
            status = 400
    spec = form['quote_spec'].value()
    return render(request, 'marketing/contact.html', {
        'quote': content.quote_from_spec(spec), 'offer_months': content.OFFER_MONTHS,
        'page': 'contact', 'form': form, 'sent': sent, 'next_steps': content.CONTACT_STEPS,
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
        ('Quote', message.quote),
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
