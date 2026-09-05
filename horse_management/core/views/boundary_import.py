"""Land App boundary import: upload, match, commit (plan phase 4b).

Three steps on two URLs. The upload page validates the file with
``core.boundary_import`` (never the browser) and keeps the normalised
shapes in the session; the matching page shows every shape beside a
picker of the site's locations with spatial and name suggestions, and a
preview drawn by the phase 3 map partial; a confirmed post writes the
boundaries in one transaction.
"""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from ..boundary_import import (
    BoundaryImportError, ImportedShape, apply_boundary, parse_geojson,
    preview_payload, suggest_matches,
)
from ..dashboard import board
from ..forms import BoundaryUploadForm
from ..models import BoundarySource, Location
from ..permissions import feature_required

SESSION_KEY = 'boundary_import'
NEW, SKIP = 'new', 'skip'


def _maps_on():
    return settings.LOCATION_MAPS_ENABLED


@feature_required('locations')
@require_http_methods(['GET', 'POST'])
def boundary_import_upload(request):
    """Step one: pick the site, upload the export."""
    if not _maps_on():
        return redirect('location_list')
    sites = board.site_names()
    if not sites:
        messages.error(request, 'Add a location first, so the shapes have a site to join.')
        return redirect('location_list')

    initial = {'site': request.GET.get('site') if request.GET.get('site') in sites else sites[0]}
    if request.method == 'POST':
        form = BoundaryUploadForm(request.POST, request.FILES, sites=sites)
        if form.is_valid():
            upload = form.cleaned_data['file']
            try:
                report = parse_geojson(upload.read())
            except BoundaryImportError as exc:
                form.add_error('file', str(exc))
            else:
                request.session[SESSION_KEY] = {
                    'site': form.cleaned_data['site'],
                    'filename': upload.name,
                    'shapes': [shape.as_dict() for shape in report.shapes],
                    'notes': report.notes,
                    'converted': report.converted_from_bng,
                }
                return redirect('boundary_import_match')
    else:
        form = BoundaryUploadForm(sites=sites, initial=initial)
    return render(request, 'locations/boundary_import_upload.html', {'form': form})


def _load(request):
    data = request.session.get(SESSION_KEY)
    if not data or not data.get('shapes'):
        return None
    shapes = [ImportedShape.from_dict(item) for item in data['shapes']]
    return data, shapes


def _parse_choices(post, shapes, by_pk):
    """``{index: ('location', Location) | ('new', name) | ('skip', None)}``
    from the posted picker rows, plus the errors that stop a commit."""
    choices, errors, used = {}, [], {}
    for shp in shapes:
        if not shp.importable:
            choices[shp.index] = (SKIP, None)
            continue
        raw = (post.get(f'shape_{shp.index}') or SKIP).strip()
        if raw == SKIP:
            choices[shp.index] = (SKIP, None)
        elif raw == NEW:
            name = (post.get(f'new_name_{shp.index}') or shp.name).strip()[:200]
            if not name:
                errors.append(f'Shape {shp.index} needs a name for its new location.')
            choices[shp.index] = (NEW, name)
        elif raw.isdigit() and int(raw) in by_pk:
            loc = by_pk[int(raw)]
            if loc.pk in used:
                errors.append(
                    f'{loc.name} is chosen for shapes {used[loc.pk]} and {shp.index}. '
                    'A location can take one boundary.'
                )
            used[loc.pk] = shp.index
            choices[shp.index] = ('location', loc)
        else:
            errors.append(f'Shape {shp.index}: that location is not on this site any more.')
    return choices, errors


@feature_required('locations')
@require_http_methods(['GET', 'POST'])
def boundary_import_match(request):
    """Step two and three: match each shape to a location, then commit."""
    if not _maps_on():
        return redirect('location_list')
    loaded = _load(request)
    if loaded is None:
        messages.info(request, 'Upload a Land App export to start an import.')
        return redirect('boundary_import_upload')
    data, shapes = loaded
    site = data['site']
    locations = list(Location.objects.active().filter(site=site).order_by('name'))
    by_pk = {loc.pk: loc for loc in locations}
    suggestions = suggest_matches(shapes, locations)

    if request.method == 'POST':
        if 'cancel' in request.POST:
            request.session.pop(SESSION_KEY, None)
            messages.info(request, 'Import cancelled. Nothing was saved.')
            return redirect(f"{reverse('location_list')}?tab=map&site={site}")
        choices, errors = _parse_choices(request.POST, shapes, by_pk)
        overwriting = [
            loc for kind, loc in choices.values()
            if kind == 'location' and loc.boundary is not None
        ]
        if overwriting and not request.POST.get('confirm_overwrite'):
            errors.append(
                'These locations already have a boundary: '
                + ', '.join(loc.name for loc in overwriting)
                + '. Tick "Replace existing boundaries" to overwrite them; the old '
                'boundary is kept in the history.'
            )
        if not any(kind != SKIP for kind, _ in choices.values()):
            errors.append('Nothing is matched. Choose a location, or Create new, for at least one shape.')
        if errors:
            return _render_match(request, data, shapes, locations, suggestions, choices, errors)
        try:
            written, created = _commit(request, shapes, choices, site)
        except ValidationError as exc:
            return _render_match(
                request, data, shapes, locations, suggestions, choices,
                ['Nothing was saved: ' + '; '.join(exc.messages)],
            )
        request.session.pop(SESSION_KEY, None)
        messages.success(
            request,
            f"{written} boundar{'ies' if written != 1 else 'y'} imported"
            + (f", {created} new location{'s' if created != 1 else ''} created" if created else '')
            + f" on {site}."
            + (f" {len(overwriting)} previous boundar{'ies' if len(overwriting) != 1 else 'y'} kept in the history." if overwriting else ''),
        )
        return redirect(f"{reverse('location_list')}?tab=map&site={site}")

    # GET: pre-select the suggestions.
    choices = {}
    for shp in shapes:
        s = suggestions.get(shp.index)
        choices[shp.index] = ('location', s['location']) if s else (SKIP, None)
    return _render_match(request, data, shapes, locations, suggestions, choices, [])


def _render_match(request, data, shapes, locations, suggestions, choices, errors):
    rows = []
    for shp in shapes:
        kind, value = choices.get(shp.index, (SKIP, None))
        suggestion = suggestions.get(shp.index)
        rows.append({
            'shape': shp,
            'selected': str(value.pk) if kind == 'location' else kind,
            'new_name': value if kind == NEW else shp.name,
            'suggested_pk': suggestion['location'].pk if suggestion else None,
            'strength': suggestion['strength'] if suggestion else None,
        })
    return render(request, 'locations/boundary_import_match.html', {
        'site': data['site'],
        'filename': data.get('filename', ''),
        'notes': data.get('notes', []),
        'rows': rows,
        'locations': locations,
        'errors': errors,
        'importable': sum(1 for shp in shapes if shp.importable),
        'invalid': [shp for shp in shapes if not shp.importable],
        'map_payload': preview_payload(data['site'], shapes),
    })


def _commit(request, shapes, choices, site):
    """Write every chosen shape, or nothing."""
    now = timezone.now()
    written = created = 0
    with transaction.atomic():
        for shp in shapes:
            kind, value = choices[shp.index]
            if kind == SKIP:
                continue
            if kind == NEW:
                location = Location(name=value, site=site, usage=Location.Usage.OTHER)
                location.full_clean()
                location.save()
                created += 1
            else:
                location = value
            apply_boundary(location, shp, source=BoundarySource.LANDAPP, now=now, user=request.user)
            written += 1
    return written, created
