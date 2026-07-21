"""
URL configuration for horse_management project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.views import (
    app_settings,
    dashboard_toggle,
    health_check,
    rate_type_create,
    rate_type_update,
    role_create,
    role_delete,
    role_update,
    user_create,
    user_update,
)

urlpatterns = [
    path('_health/', health_check, name='health_check'),
    path('settings/', app_settings, name='app_settings'),
    path('settings/rates/add/', rate_type_create, name='rate_type_create'),
    path('settings/rates/<int:pk>/edit/', rate_type_update, name='rate_type_update'),
    path('settings/users/add/', user_create, name='user_create'),
    path('settings/users/<int:pk>/edit/', user_update, name='user_update'),
    path('settings/roles/add/', role_create, name='role_create'),
    path('settings/roles/<int:pk>/edit/', role_update, name='role_update'),
    path('settings/roles/<int:pk>/delete/', role_delete, name='role_delete'),
    path('settings/dashboard/toggle/', dashboard_toggle, name='dashboard_toggle'),
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('core.urls')),
    path('invoicing/', include('invoicing.urls')),
    path('health/', include('health.urls')),
    path('billing/', include('billing.urls')),
    path('xero/', include('xero_integration.urls')),
]

# Serve uploaded media through Django in production when SERVE_MEDIA=True
# (e.g. Railway volume). WhiteNoise only covers static files, and the
# static() helper below is a no-op unless DEBUG, so wire the view directly.
if getattr(settings, 'SERVE_MEDIA', False) and not settings.DEBUG:
    from django.contrib.auth.decorators import login_required
    from django.core.exceptions import PermissionDenied
    from django.urls import re_path
    from django.views.static import serve as media_serve

    from core.permissions import LEVEL_VIEW, has_feature_access

    # Login alone isn't authorization: passports, insurance documents and
    # supplier receipts live at guessable /media/ paths, and a role built
    # to hide Finance/Horses must not be able to fetch them directly.
    MEDIA_FEATURE_BY_PREFIX = (
        ('documents/', 'horses'),
        ('horses/', 'horses'),
        ('horse_photos/', 'horses'),
        ('receipts/yard/', 'costs'),
        ('receipts/', 'charges'),
        ('business/', None),  # logo — any signed-in user (renders app-wide)
    )

    @login_required
    def _gated_media_serve(request, path, document_root=None):
        for prefix, feature in MEDIA_FEATURE_BY_PREFIX:
            if path.startswith(prefix):
                if feature and not has_feature_access(
                    request.user, feature, LEVEL_VIEW
                ):
                    raise PermissionDenied
                break
        else:
            # Unknown prefix: superusers only, fail closed.
            if not request.user.is_superuser:
                raise PermissionDenied
        return media_serve(request, path, document_root=document_root)

    urlpatterns += [
        re_path(
            r'^media/(?P<path>.*)$',
            _gated_media_serve,
            {'document_root': settings.MEDIA_ROOT},
        ),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

    # Debug toolbar (skipped when it isn't installed, e.g. QA runs)
    if 'debug_toolbar' in settings.INSTALLED_APPS:
        import debug_toolbar
        urlpatterns = [
            path('__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns
