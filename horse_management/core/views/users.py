"""
User management views (Settings → Users & Roles).

Users with Full access on the "Users & Roles" feature create accounts,
assign roles, reset passwords, and deactivate accounts — no Django admin
or CLI needed.
"""

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .. import mfa
from ..account_security import clear_lockout, is_locked_out, sign_out_everywhere
from ..forms import AdminSetPasswordForm, UserCreateForm, UserUpdateForm
from ..permissions import (
    LEVEL_FULL,
    LEVEL_ORDER,
    feature_required,
    has_feature_access,
)

User = get_user_model()
logger = logging.getLogger(__name__)


def _grants_user_management(role):
    return LEVEL_ORDER[role.resolved_access()['users']] >= LEVEL_ORDER[LEVEL_FULL]


def _managers_excluding(user_pks):
    """Active users outside ``user_pks`` who can manage users & roles.

    The single definition of "manager" behind every last-manager lockout
    guard (user demotion/deactivation here, role demotion/deletion in
    roles.py). Superusers count; so does anyone whose role resolves to Full
    on the ``users`` feature. The roles table is tiny, so resolving each
    role in Python is fine.
    """
    from ..models import Role

    manager_role_ids = [r.pk for r in Role.objects.all() if _grants_user_management(r)]
    return User.objects.filter(is_active=True).exclude(pk__in=user_pks).filter(
        Q(is_superuser=True) | Q(role_assignment__role_id__in=manager_role_ids)
    )


def _other_role_managers(user):
    return _managers_excluding([user.pk])


def _is_manager(user):
    # Same resolution the permission layer uses everywhere else.
    return has_feature_access(user, 'users', LEVEL_FULL)


@feature_required('users')
def user_create(request):
    """Create a login account. The email address is the sign-in identifier."""
    if request.method == 'POST':
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            if form.sends_invite:
                from .account import send_invite
                try:
                    send_invite(request, user)
                except Exception:
                    logger.exception('Invitation email to new user %s failed.', user.pk)
                    messages.warning(
                        request,
                        f"User {user.email} created, but the invitation email could not be sent. "
                        "Check the email settings, then set a password for them below.",
                    )
                    return redirect('user_update', pk=user.pk)
                messages.success(request, f"User {user.email} created. An invitation to choose a password is on its way.")
            else:
                messages.success(request, f"User {user.email} created.")
            return redirect('app_settings')
    else:
        form = UserCreateForm()
    return render(request, 'settings/user_form.html', {'form': form})


@feature_required('users')
def user_update(request, pk):
    """Edit name/email/role, reset the password, or (de)activate an account.

    The three forms on the page are distinguished by a hidden POST key,
    mirroring the business-settings pattern on the settings page.
    """
    target = get_object_or_404(
        User.objects.select_related('role_assignment__role'), pk=pk
    )
    is_self = target.pk == request.user.pk
    current_role = getattr(getattr(target, 'role_assignment', None), 'role', None)

    form = UserUpdateForm(instance=target, initial={
        'first_name': target.first_name,
        'last_name': target.last_name,
        'email': target.email,
        'role': current_role,
    })
    password_form = AdminSetPasswordForm(user=target)

    if request.method == 'POST':
        if 'save_details' in request.POST:
            form = UserUpdateForm(request.POST, instance=target)
            if form.is_valid():
                new_role = form.cleaned_data['role']
                demoting = (
                    _is_manager(target)
                    and not target.is_superuser
                    and not _grants_user_management(new_role)
                )
                if demoting and is_self:
                    messages.error(request, "You can't remove your own user-management access. Ask another administrator to change your role.")
                elif demoting and not _other_role_managers(target).exists():
                    messages.error(request, "You can't demote the only user with access to Users & Roles. Give someone else a role with that access first.")
                else:
                    form.save()
                    messages.success(request, "User details saved.")
                    return redirect('app_settings')

        elif 'set_password' in request.POST:
            password_form = AdminSetPasswordForm(request.POST, user=target)
            if password_form.is_valid():
                password_form.save()
                if is_self:
                    # Changing one's own password invalidates the session
                    # auth hash — refresh it or the redirect bounces the
                    # admin to the login screen mid-save.
                    from django.contrib.auth import update_session_auth_hash
                    update_session_auth_hash(request, target)
                messages.success(request, f"Password updated for {target.email or target.username}.")
                return redirect('app_settings')

        elif 'unlock' in request.POST:
            cleared = clear_lockout(target)
            logger.info('Sign-in lockout cleared for user %s by user %s.', target.pk, request.user.pk)
            if cleared:
                messages.success(request, f"{target.email or target.username} can sign in again.")
            else:
                messages.info(request, f"{target.email or target.username} was not locked out.")
            return redirect('user_update', pk=target.pk)

        elif 'sign_out_everywhere' in request.POST:
            ended = sign_out_everywhere(
                target, keep_session_key=request.session.session_key if is_self else None,
            )
            logger.info('User %s signed out of %d sessions by user %s.', target.pk, ended, request.user.pk)
            messages.success(request, f"Ended {ended} session{'s' if ended != 1 else ''} for {target.email or target.username}.")
            return redirect('user_update', pk=target.pk)

        elif 'mfa_reset' in request.POST:
            if is_self:
                messages.error(request, "Change your own two-step sign-in on your Account Security page.")
            elif not mfa.is_administrator(request.user):
                # Together with "Set Password" this would hand over the
                # account completely, so it is kept to Administrators.
                messages.error(request, "Only an Administrator can turn off two-step sign-in for someone else.")
            else:
                mfa.disable(target)
                # Whoever had the lost phone may also hold a signed-in session.
                sign_out_everywhere(target)
                logger.warning('Two-step sign-in turned off for user %s by user %s.', target.pk, request.user.pk)
                messages.success(
                    request,
                    f"Two-step sign-in is off for {target.email or target.username}. "
                    "They have been signed out everywhere."
                    + (" Their role needs it, so they must set it up again when they next sign in." if mfa.mfa_required(target) else ""),
                )
            return redirect('user_update', pk=target.pk)

        elif 'toggle_active' in request.POST:
            if is_self:
                messages.error(request, "You can't deactivate your own account.")
            elif target.is_active and _is_manager(target) and not _other_role_managers(target).exists():
                messages.error(request, "You can't deactivate the only user with access to Users & Roles.")
            else:
                target.is_active = not target.is_active
                target.save(update_fields=['is_active'])
                verb = "reactivated" if target.is_active else "deactivated"
                messages.success(request, f"User {target.email or target.username} {verb}.")
                return redirect('app_settings')

    return render(request, 'settings/user_form.html', {
        'form': form,
        'password_form': password_form,
        'object': target,
        'is_self': is_self,
        'locked_out': is_locked_out(target),
        'mfa': mfa.mfa_status(target),
        'can_reset_mfa': mfa.is_administrator(request.user) and not is_self,
    })
