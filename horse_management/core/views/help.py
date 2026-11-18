"""The help centre: a list of guides with a search, and one page per guide
(core/help.py, templates/help/)."""

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render

from .. import help as guides


@login_required
def help_index(request):
    query = request.GET.get('q', '').strip()[:100]
    return render(request, 'help/index.html', {
        'groups': guides.grouped_for(request.user),
        'query': query,
        'results': guides.search(request.user, query) if query else None,
        'support_email': guides.SUPPORT_EMAIL,
    })


@login_required
def help_article(request, slug):
    guide = guides.GUIDES_BY_SLUG.get(slug)
    if guide is None or not guide.visible_to(request.user):
        raise Http404('No such guide')
    return render(request, 'help/article.html', {
        'guide': guide,
        'groups': guides.grouped_for(request.user),
        'support_email': guides.SUPPORT_EMAIL,
    })
