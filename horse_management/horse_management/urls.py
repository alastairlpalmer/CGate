"""
URL configuration for horse_management project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.views import account as account_views
from core.views import audit_log as audit_log_views
from core.views import background_tasks as background_tasks_views
from core.views import well_known as well_known_views
from core.views import recently_deleted as recently_deleted_views
from notifications import views as notification_views
from core.views import (
    app_settings,
    posthog_proxy,
    dashboard_toggle,
    health_check,
    rate_type_create,
    rate_type_update,
    service_item_create,
    service_item_update,
    role_create,
    role_delete,
    role_update,
    user_create,
    user_update,
)

urlpatterns = [
    path('_health/', health_check, name='health_check'),
    path('.well-known/security.txt', well_known_views.security_txt, name='security_txt'),
    # Same-origin PostHog proxy, so ad blockers cannot see the tracker.
    # Path must match POSTHOG_PROXY_PATH in settings.
    path('ingest/', posthog_proxy, name='posthog_proxy_root'),
    path('ingest/<path:upstream_path>', posthog_proxy, name='posthog_proxy'),
    path('settings/', app_settings, name='app_settings'),
    path('settings/rates/add/', rate_type_create, name='rate_type_create'),
    path('settings/rates/<int:pk>/edit/', rate_type_update, name='rate_type_update'),
    path('settings/services/add/', service_item_create, name='service_item_create'),
    path('settings/services/<int:pk>/edit/', service_item_update, name='service_item_update'),
    path('settings/users/add/', user_create, name='user_create'),
    path('settings/users/<int:pk>/edit/', user_update, name='user_update'),
    path('settings/roles/add/', role_create, name='role_create'),
    path('settings/roles/<int:pk>/edit/', role_update, name='role_update'),
    path('settings/roles/<int:pk>/delete/', role_delete, name='role_delete'),
    path('settings/dashboard/toggle/', dashboard_toggle, name='dashboard_toggle'),
    path('settings/audit/', audit_log_views.audit_log, name='audit_log'),
    path('settings/tasks/', background_tasks_views.background_tasks, name='background_tasks'),
    path('settings/tasks/<int:pk>/resolve/', background_tasks_views.failed_task_resolve, name='failed_task_resolve'),
    path('settings/deleted/', recently_deleted_views.recently_deleted, name='recently_deleted'),
    # Public: the unsubscribe link in reminder emails, and the email
    # provider's bounce and complaint webhooks (notifications/views.py).
    path('email/unsubscribe/<str:token>/', notification_views.unsubscribe, name='email_unsubscribe'),
    path('email/events/postmark/<str:token>/', notification_views.postmark_events, name='email_events_postmark'),
    path('email/events/resend/', notification_views.resend_events, name='email_events_resend'),
    path(settings.ADMIN_URL, admin.site.urls),
    # Ahead of django.contrib.auth.urls, so these replace the stock views of
    # the same names (core/views/account.py says why).
    path('accounts/password_change/', account_views.AuditedPasswordChangeView.as_view(), name='password_change'),
    path('accounts/password_reset/', account_views.RateLimitedPasswordResetView.as_view(), name='password_reset'),
    path('accounts/reset/<uidb64>/<token>/', account_views.UnlockingPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('accounts/invite/<uidb64>/<token>/', account_views.InviteConfirmView.as_view(), name='invite_confirm'),
    path('accounts/security/', account_views.account_security, name='account_security'),
    path('accounts/security/sign-out-others/', account_views.sign_out_other_devices, name='sign_out_other_devices'),
    path('accounts/two-step/', account_views.mfa_verify, name='mfa_verify'),
    path('accounts/two-step/setup/', account_views.mfa_setup, name='mfa_setup'),
    path('accounts/two-step/recovery-codes/', account_views.mfa_new_recovery_codes, name='mfa_new_recovery_codes'),
    path('accounts/two-step/off/', account_views.mfa_disable, name='mfa_disable'),
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
        response = media_serve(request, path, document_root=document_root)
        # Open PDFs and photos in the browser; download everything else,
        # so an uploaded file is never rendered as a page of this site.
        from core.uploads import content_disposition
        response['Content-Disposition'] = content_disposition(path)
        response['X-Content-Type-Options'] = 'nosniff'
        return response

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
