"""
User management views (Settings → Users & Access).

Admins (is_staff) create accounts, assign the Admin/Viewer role, reset
passwords, and deactivate accounts — no Django admin or CLI needed.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render

from ..forms import AdminSetPasswordForm, UserCreateForm, UserUpdateForm
from ..mixins import staff_required

User = get_user_model()


def _other_active_admins(user):
    """Active admins other than ``user`` — guards the last-admin lockout."""
    return User.objects.filter(is_staff=True, is_active=True).exclude(pk=user.pk)


@staff_required
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


@staff_required
def user_update(request, pk):
    """Edit name/email/role, reset the password, or (de)activate an account.

    The three forms on the page are distinguished by a hidden POST key,
    mirroring the business-settings pattern on the settings page.
    """
    target = get_object_or_404(User, pk=pk)
    is_self = target.pk == request.user.pk

    form = UserUpdateForm(instance=target, initial={
        'first_name': target.first_name,
        'last_name': target.last_name,
        'email': target.email,
        'role': 'admin' if target.is_staff else 'viewer',
    })
    password_form = AdminSetPasswordForm(user=target)

    if request.method == 'POST':
        if 'save_details' in request.POST:
            form = UserUpdateForm(request.POST, instance=target)
            if form.is_valid():
                demoting = target.is_staff and form.cleaned_data['role'] != 'admin'
                if demoting and is_self:
                    messages.error(request, "You can't remove your own admin access. Ask another admin to change your role.")
                elif demoting and not _other_active_admins(target).exists():
                    messages.error(request, "You can't demote the only admin. Promote someone else first.")
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
            elif target.is_active and target.is_staff and not _other_active_admins(target).exists():
                messages.error(request, "You can't deactivate the only admin.")
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
