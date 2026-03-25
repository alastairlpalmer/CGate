from django.contrib import admin

from .models import XeroConnection, XeroContactMapping, XeroInvoiceSync


@admin.register(XeroConnection)
class XeroConnectionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'is_active', 'xero_tenant_name', 'connected_at', 'updated_at')
    readonly_fields = (
        'access_token', 'refresh_token', 'token_expires_at',
        'xero_tenant_id', 'xero_tenant_name', 'connected_at',
        'last_refreshed_at', 'oauth_state', 'created_at', 'updated_at',
    )

    def has_add_permission(self, request):
        # Singleton — only one instance allowed
        return not XeroConnection.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(XeroContactMapping)
class XeroContactMappingAdmin(admin.ModelAdmin):
    list_display = ('owner', 'xero_contact_name', 'xero_contact_id', 'last_synced_at')
    search_fields = ('owner__first_name', 'owner__last_name', 'xero_contact_name')
    readonly_fields = ('last_synced_at',)


@admin.register(XeroInvoiceSync)
class XeroInvoiceSyncAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'sync_status', 'xero_invoice_id', 'last_pushed_at')
    list_filter = ('sync_status',)
    readonly_fields = ('created_at', 'updated_at')
