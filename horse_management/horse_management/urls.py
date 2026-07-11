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
)

urlpatterns = [
    path('_health/', health_check, name='health_check'),
    path('settings/', app_settings, name='app_settings'),
    path('settings/rates/add/', rate_type_create, name='rate_type_create'),
    path('settings/rates/<int:pk>/edit/', rate_type_update, name='rate_type_update'),
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
    from django.urls import re_path
    from django.views.static import serve as media_serve

    urlpatterns += [
        re_path(
            r'^media/(?P<path>.*)$',
            # Uploaded media includes receipts and other financial documents
            # at predictable paths — never serve it unauthenticated.
            login_required(media_serve),
            {'document_root': settings.MEDIA_ROOT},
        ),
    ]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

    # Debug toolbar
    import debug_toolbar
    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
    ] + urlpatterns
