"""The signed-in person's own profile: the pop-up from the sidebar's
profile card and the phone's More sheet.

docs/SIDEBAR_AND_NOTIFICATIONS_PLAN.md, WP2. Tabs:

* Details: name, email, phone, photo.
* Security: change password, the state of two-step sign-in and Google
  sign-in, with links to the existing pages for the steps that need a
  full page (setting up an authenticator app shows a QR code).
* Notifications: which kinds of notification to get, in the app and by
  email (added with the notification centre, WP3).

Inside the pop-up sheet (HX-Target: popup-body) a valid save answers
204 + popup:saved: the sheet closes, the page beneath refreshes and the
message shows as a toast. Outside it, the same view is a normal page.
"""

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render

from .. import audit
from ..forms import ProfileDetailsForm
from ..models import AuditEvent, UserProfile
from ._popup import is_popup_request, popup_saved_response

TABS = ('details', 'security', 'notifications')


def _initial_details(user, profile):
    return {
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'phone': profile.phone,
        'display_size': profile.display_size,
        'phone_text_size': profile.phone_text_size,
    }


def _save_details(request, form, profile):
    user = request.user
    data = form.cleaned_data
    old_email = user.email
    user.first_name = data['first_name'].strip()
    user.last_name = data['last_name'].strip()
    user.email = data['email']
    user.save(update_fields=['first_name', 'last_name', 'email'])

    profile.phone = data['phone']
    profile.display_size = data['display_size']
    profile.phone_text_size = data.get('phone_text_size') or profile.phone_text_size
    upload = data.get('photo')
    if upload or data.get('remove_photo'):
        if profile.photo:
            profile.photo.delete(save=False)
            profile.photo = None
    if upload:
        from ..images import make_avatar_thumbnail
        thumb = make_avatar_thumbnail(upload)
        if thumb is not None:
            profile.photo.save(f'user-{user.pk}.jpg', thumb, save=False)
    profile.save()

    if old_email.lower() != user.email.lower():
        audit.record(
            AuditEvent.Action.USER_UPDATED, request=request, target=user,
            detail='Changed their own email address',
        )


@login_required
def profile(request):
    user = request.user
    profile_obj = UserProfile.get_for(user)
    tab = request.GET.get('tab', 'details')
    if tab not in TABS:
        tab = 'details'

    details_form = ProfileDetailsForm(initial=_initial_details(user, profile_obj), user=user)
    password_form = PasswordChangeForm(user) if user.has_usable_password() else None

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'details':
            tab = 'details'
            from ..reauth import recently_confirmed
            details_form = ProfileDetailsForm(
                request.POST, request.FILES, user=user,
                recently_confirmed=recently_confirmed(request),
            )
            if details_form.is_valid():
                _save_details(request, details_form, profile_obj)
                from ..user_photos import remember
                remember(request, user)
                messages.success(request, 'Your details are saved.')
                return _saved(request, reload_page=True)
        elif action == 'notifications':
            from notifications import centre
            centre.save_preferences(user, request.POST)
            centre.resync_soon(user)
            messages.success(request, 'Your notification choices are saved.')
            return _saved(request, reload_page=True)
        elif action == 'password' and password_form is not None:
            tab = 'security'
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                password_form.save()
                # Keeps this session; every other session ends, because
                # it holds a hash of the old password.
                update_session_auth_hash(request, password_form.user)
                audit.record(AuditEvent.Action.PASSWORD_CHANGED, request=request, target=user)
                messages.success(request, 'Your password is changed. Your other devices are signed out.')
                return _saved(request)

    from ..mfa import mfa_status
    from ..models import GoogleIdentity

    context = {
        'tab': tab,
        'profile': profile_obj,
        'details_form': details_form,
        'password_form': password_form,
        'mfa': mfa_status(user),
        'google_identity': GoogleIdentity.objects.filter(user=user).first(),
        'in_popup': is_popup_request(request),
    }
    from notifications.centre import preference_rows
    context['notification_rows'] = preference_rows(user)
    template = 'account/_profile_body.html' if context['in_popup'] else 'account/profile.html'
    return render(request, template, context)


def _saved(request, reload_page=False):
    """The answer to a valid save. In the sheet: close it and refresh the
    page beneath. A new name or photo shows in the sidebar and the More
    sheet, which sit outside the part of the page that refreshes, so a
    details save reloads the whole page instead (the message survives in
    the session and shows after the reload)."""
    if is_popup_request(request):
        response = popup_saved_response()
        if reload_page:
            response['HX-Refresh'] = 'true'
        return response
    return redirect('profile')
