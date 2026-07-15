"""
Role Suite views (Settings → Users & Roles): create, edit and delete roles.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..forms import RoleForm
from ..models import Role, UserRole
from ..permissions import feature_required
from .users import _grants_user_management, _other_role_managers


def _demotion_locks_out(role, new_access_grants_users):
    """True if taking users-access away from ``role`` leaves nobody in charge.

    Only bites when the role currently grants Users & Roles access, the new
    matrix doesn't, the role has active members, and no active manager
    exists outside those members (superusers always count as managers).
    """
    if not _grants_user_management(role) or new_access_grants_users:
        return False
    members = list(role.assignments.filter(user__is_active=True).values_list('user_id', flat=True))
    if not members:
        return False
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    other_manager_role_ids = [
        r.pk for r in Role.objects.exclude(pk=role.pk) if _grants_user_management(r)
    ]
    return not get_user_model().objects.filter(is_active=True).exclude(pk__in=members).filter(
        Q(is_superuser=True) | Q(role_assignment__role_id__in=other_manager_role_ids)
    ).exists()


@feature_required('users')
def role_create(request):
    if request.method == 'POST':
        form = RoleForm(request.POST)
        if form.is_valid():
            role = form.save()
            messages.success(request, f"Role “{role.name}” created. Assign it to users from their edit page.")
            return redirect('app_settings')
    else:
        form = RoleForm()
    return render(request, 'settings/role_form.html', {'form': form})


@feature_required('users')
def role_update(request, pk):
    role = get_object_or_404(Role, pk=pk)
    if request.method == 'POST':
        form = RoleForm(request.POST, instance=role)
        if form.is_valid():
            from ..features import LEVEL_FULL
            new_grants_users = (
                role.is_system
                or form.access_value().get('users') == LEVEL_FULL
            )
            if _demotion_locks_out(role, new_grants_users):
                messages.error(
                    request,
                    "This change would leave nobody able to manage users and roles. "
                    "Give another user a role with Users & Roles access first.",
                )
            else:
                form.save()
                messages.success(request, "Role saved.")
                return redirect('app_settings')
    else:
        form = RoleForm(instance=role)

    member_count = role.assignments.count()
    return render(request, 'settings/role_form.html', {
        'form': form,
        'object': role,
        'member_count': member_count,
        'reassign_targets': Role.objects.exclude(pk=role.pk).order_by('-is_system', 'name'),
    })


@feature_required('users')
@require_POST
def role_delete(request, pk):
    role = get_object_or_404(Role, pk=pk)
    if role.is_system:
        messages.error(request, "The Administrator role can't be deleted.")
        return redirect('role_update', pk=pk)

    members = role.assignments.count()
    target = None
    if members:
        try:
            reassign_pk = int(request.POST.get('reassign_to') or 0)
        except (TypeError, ValueError):
            reassign_pk = 0
        target = Role.objects.exclude(pk=role.pk).filter(pk=reassign_pk).first()
        if target is None:
            messages.error(request, "Choose a role to move this role's members to.")
            return redirect('role_update', pk=pk)
        # Reassignment must not strand user management with nobody.
        if _demotion_locks_out(role, _grants_user_management(target)):
            messages.error(
                request,
                "Moving these members to that role would leave nobody able to "
                "manage users and roles. Pick a role with Users & Roles access.",
            )
            return redirect('role_update', pk=pk)
        UserRole.objects.filter(role=role).update(role=target)

    name = role.name
    role.delete()
    if members and target is not None:
        messages.success(request, f"Role “{name}” deleted; {members} member{'s' if members != 1 else ''} moved to {target.name}.")
    else:
        messages.success(request, f"Role “{name}” deleted.")
    return redirect('app_settings')
