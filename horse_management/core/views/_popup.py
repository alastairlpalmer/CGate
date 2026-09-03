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

    @property
    def in_popup(self):
        return is_popup_request(self.request)

    def get_template_names(self):
        if self.in_popup:
            return [self.popup_template_name]
        return super().get_template_names()

    def get_popup_submit_label(self):
        obj = getattr(self, 'object', None)
        return 'Save Changes' if obj is not None and obj.pk else 'Save'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        horse_id = self.request.GET.get('horse', '')
        context['in_popup'] = self.in_popup
        context['popup_horse_id'] = horse_id if horse_id.isdigit() else ''
        context['popup_submit_label'] = self.get_popup_submit_label()
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.in_popup:
            return popup_saved_response()
        return response
