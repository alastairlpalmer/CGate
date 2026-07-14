"""
User management views (Settings → Users & Roles).

Users with Full access on the "Users & Roles" feature create accounts,
assign roles, reset passwords, and deactivate accounts — no Django admin
or CLI needed.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from ..forms import AdminSetPasswordForm, UserCreateForm, UserUpdateForm
from ..permissions import LEVEL_FULL, LEVEL_ORDER, feature_required

User = get_user_model()


def _grants_user_management(role):
    return LEVEL_ORDER[role.resolved_access()['users']] >= LEVEL_ORDER[LEVEL_FULL]


def _other_role_managers(user):
    """Active users other than ``user`` who can manage users & roles.

    Guards the last-manager lockout. Superusers count; so does anyone whose
    role resolves to Full on the ``users`` feature. The roles table is tiny,
    so resolving each role in Python is fine.
    """
    from ..models import Role

    manager_role_ids = [r.pk for r in Role.objects.all() if _grants_user_management(r)]
    return User.objects.filter(is_active=True).exclude(pk=user.pk).filter(
        Q(is_superuser=True) | Q(role_assignment__role_id__in=manager_role_ids)
    )


def _is_manager(user):
    return user.is_superuser or (
        hasattr(user, 'role_assignment') and _grants_user_management(user.role_assignment.role)
    )


@feature_required('users')
def user_create(request):
    """Create a login account. The email address is the sign-in identifier."""
    if request.method == 'POST':
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
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
                messages.success(request, f"Password updated for {target.email or target.username}.")
                return redirect('app_settings')

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
    })
