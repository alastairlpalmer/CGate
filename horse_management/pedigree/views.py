"""Pedigree pop-up and diagram views (plan §7.2, §8.3)."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from core.models import Horse
from core.permissions import LEVEL_VIEW, feature_required
from core.views._popup import is_popup_request, popup_saved_response

from .display import diagram_context, record_for_page
from .forms import PedigreeForm, validation_messages
from .models import PedigreeHorse
from .services import set_parents
from .tree import MAX_GENERATIONS

SUGGESTION_LIMIT = 500


def _suggestions(sex):
    rows = (PedigreeHorse.objects.exclude(sex=PedigreeHorse.Sex.FEMALE if sex == 'M' else PedigreeHorse.Sex.MALE)
            .order_by('name', 'country', 'year_of_birth')
            .only('name', 'country', 'year_of_birth', 'horse')[:SUGGESTION_LIMIT])
    return [(r.label, r.horse_id is not None) for r in rows]


@feature_required('horses')
def pedigree_edit(request, pk):
    """Edit a horse's parents and grandparents (pop-up, or a full page)."""
    horse = get_object_or_404(Horse, pk=pk)
    record = record_for_page(horse)
    form = PedigreeForm(request.POST or None, horse=horse, record=record)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                set_parents(horse, form.resolutions)
        except ValidationError as error:
            for message in validation_messages(error):
                form.add_error(None, message)
        else:
            if form.resolutions:
                messages.success(request, f"Pedigree for {horse.name} saved.")
            if is_popup_request(request):
                return popup_saved_response()
            return redirect('horse_detail', pk=horse.pk)

    template = 'pedigree/partials/edit_form.html' if is_popup_request(request) else 'pedigree/edit_page.html'
    return render(request, template, {
        'horse': horse, 'record': record, 'form': form,
        'males': _suggestions('M'), 'females': _suggestions('F'),
    })


@feature_required('horses', LEVEL_VIEW)
def pedigree_tree(request, record_pk):
    """The diagram for any record, as a partial: an ancestor's own tree,
    or the page horse's tree with more generations."""
    root = get_object_or_404(PedigreeHorse.objects.select_related('horse'), pk=record_pk)
    horse_pk = request.GET.get('horse', '')
    page_horse = get_object_or_404(Horse, pk=horse_pk) if horse_pk.isdigit() else root.horse
    if page_horse is None:
        raise Http404('No horse to show this pedigree for.')
    if not getattr(request, 'htmx', None):
        return redirect('horse_detail', pk=page_horse.pk)
    generations = request.GET.get('generations', '2')
    generations = min(max(int(generations), 1), MAX_GENERATIONS) if generations.isdigit() else 2
    back_pk = request.GET.get('back', '')
    back = PedigreeHorse.objects.filter(pk=back_pk).first() if back_pk.isdigit() else None
    context = diagram_context(root, page_horse=page_horse, generations=generations, back=back)
    return render(request, 'pedigree/_diagram.html', context)
