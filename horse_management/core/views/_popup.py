"""Helpers for views that can render inside the shared pop-up sheet
(templates/includes/_popup_sheet.html, static/js/popup.js)."""

from django.http import HttpResponse


def is_popup_request(request):
    """True when the pop-up sheet asked for this view.

    Boosted navigations also send ``HX-Request: true``, so the target is
    the discriminator: the sheet always loads into ``#popup-body``.
    """
    htmx = getattr(request, 'htmx', None)
    return bool(htmx) and htmx.target == 'popup-body'


def popup_saved_response():
    """The answer to a successful save from the sheet: nothing to swap, and
    a trigger that closes the sheet and refreshes the page beneath it.
    Django messages queued before this show on that refresh."""
    return HttpResponse(status=204, headers={'HX-Trigger': 'popup:saved'})


class PopupFormMixin:
    """Let a CreateView/UpdateView also serve the pop-up sheet.

    Put it first in the bases. Outside the sheet nothing changes. Inside
    it (HX-Target: popup-body) the form renders through the generic
    ``includes/popup_form.html`` partial, invalid posts re-render that
    partial, and a valid save answers 204 + ``popup:saved`` instead of
    redirecting — the sheet closes and the page beneath refreshes, so
    any messages the view queued show as toasts.

    A ``?horse=<id>`` in the URL (the Quick Action deep links) turns the
    form's horse select into a hidden field: the sheet's title already
    names the horse.
    """

    popup_template_name = 'includes/popup_form.html'
    # Offer "Save & add another" on create forms: the record saves and a
    # fresh form re-renders in the sheet (a farrier day is six in a row).
    popup_add_another = True
    # A template included below the generic field grid, and the names of
    # the fields it renders itself (skipped by the grid).
    popup_extra_template = ''
    popup_deferred_fields = ()
    # One line at the top of the sheet: what the form is for, when the
    # button that opens it is too small to say (UX review C8).
    popup_intro = ''
    # The fields the sheet shows first. When it is set, a new record's
    # other fields wait under "More details", with "Save & add another":
    # the usual record needs two or three answers, and a sheet of ten
    # fields hid them (docs/MOBILE_APP_PLAN.md, C8). A field that must be
    # filled in is never put there. Empty: every field shows, as before.
    popup_main_fields = ()

    @property
    def in_popup(self):
        return is_popup_request(self.request)

    def get_template_names(self):
        if self.in_popup:
            return [self.popup_template_name]
        return super().get_template_names()

    def get_popup_submit_label(self):
        obj = getattr(self, 'object', None)
        return 'Save changes' if obj is not None and obj.pk else 'Save'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        horse_id = self.request.GET.get('horse', '')
        context['in_popup'] = self.in_popup
        context['popup_horse_id'] = horse_id if horse_id.isdigit() else ''
        context['popup_submit_label'] = self.get_popup_submit_label()
        context['popup_add_another'] = bool(
            self.in_popup and self.popup_add_another and self._is_create()
        )
        context['popup_extra_template'] = self.popup_extra_template
        context['popup_deferred_fields'] = list(self.popup_deferred_fields)
        context['popup_intro'] = self.popup_intro if self.in_popup else ''
        context.update(self._popup_more(context.get('form')))
        return context

    def _popup_more(self, form):
        """Which fields wait under "More details", and is it open."""
        nothing = {'popup_more_fields': [], 'popup_more_open': False, 'popup_more_hint': ''}
        if not (self.in_popup and self.popup_main_fields and form is not None):
            return nothing
        more = [
            field for field in form.visible_fields()
            if field.name not in self.popup_main_fields
            and field.name not in self.popup_deferred_fields
            and not field.field.required
        ]
        if not more:
            return nothing
        labels = [str(field.label).lower() for field in more]
        return {
            'popup_more_fields': [field.name for field in more],
            # Open when someone came to change a saved record, and when a
            # field in it has an error to show.
            'popup_more_open': not self._is_create() or any(field.errors for field in more),
            'popup_more_hint': ', '.join(labels[:4]) + (' and more' if len(labels) > 4 else ''),
        }

    def _is_create(self):
        obj = getattr(self, 'object', None)
        return obj is None or not obj.pk

    def form_valid(self, form):
        response = super().form_valid(form)
        if not self.in_popup:
            return response
        if self.popup_add_another and 'save_and_add' in self.request.POST:
            # Saved: hand back a clean form in the same sheet. The messages
            # the view queued show together on the final refresh.
            self.object = None
            kwargs = self.get_form_kwargs()
            kwargs.pop('data', None)
            kwargs.pop('files', None)
            fresh = self.get_form_class()(**kwargs)
            return self.render_to_response(
                self.get_context_data(form=fresh, popup_saved_note='Saved. Add another below.')
            )
        return popup_saved_response()
