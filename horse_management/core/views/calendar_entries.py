"""General items on the dashboard calendar (core.models.CalendarEntry).

Add, change and delete happen in the shared pop-up sheet, opened from the
calendar's chosen day. A save answers 204 + ``popup:saved``: the sheet
closes and the dashboard refreshes, so the item shows on its day at once.
Outside the sheet these views send the reader to the dashboard.
"""

from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..forms import CalendarEntryForm
from ..models import CalendarEntry
from ..permissions import feature_required
from ._popup import is_popup_request, popup_saved_response


def _day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _render(request, form, entry=None, on=None):
    if entry is None:
        action_url = reverse('calendar_entry_add')
    else:
        action_url = reverse('calendar_entry_edit', args=[entry.pk])
        if on is not None:
            action_url += f'?on={on:%Y-%m-%d}'
    return render(request, 'partials/dashboard/calendar_entry_form.html', {
        'form': form,
        'entry': entry,
        'on': on,
        'in_popup': True,
        'action_url': action_url,
    })


@feature_required('dashboard')
def calendar_entry_add(request):
    """Add a general item. ``?date=YYYY-MM-DD`` fills in the chosen day."""
    if not is_popup_request(request):
        return redirect('dashboard')
    if request.method == 'POST':
        form = CalendarEntryForm(request.POST)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.created_by = request.user
            entry.save()
            messages.success(request, f'"{entry.title}" is on the calendar for {entry.date:%-d %B}.')
            return popup_saved_response()
    else:
        day = _day(request.GET.get('date'))
        form = CalendarEntryForm(initial={'date': day} if day else None)
    return _render(request, form)


@feature_required('dashboard')
def calendar_entry_edit(request, pk):
    """Change a general item, or delete it.

    For a repeating item ``?on=YYYY-MM-DD`` names the day it was opened
    from. The form then offers "Delete this day only", which adds that
    day to ``skip_dates``; "Delete all" (or "Delete" for a single item)
    removes the row. A change applies to every day of the series.
    """
    entry = get_object_or_404(CalendarEntry, pk=pk)
    if not is_popup_request(request):
        return redirect('dashboard')
    on = _day(request.GET.get('on')) if entry.repeat else None
    if request.method == 'POST' and request.POST.get('delete') == 'day' and on is not None:
        skipped = set(entry.skip_dates or [])
        skipped.add(on.isoformat())
        entry.skip_dates = sorted(skipped)
        entry.save(update_fields=['skip_dates', 'updated_at'])
        messages.success(request, f'"{entry.title}" was taken off {on:%-d %B} only.')
        return popup_saved_response()
    if request.method == 'POST' and request.POST.get('delete'):
        title = entry.title
        entry.delete()
        messages.success(request, f'"{title}" was taken off the calendar.')
        return popup_saved_response()
    if request.method == 'POST':
        form = CalendarEntryForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, f'"{entry.title}" was saved.')
            return popup_saved_response()
    else:
        form = CalendarEntryForm(instance=entry)
    return _render(request, form, entry, on)
