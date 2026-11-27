from django.contrib import admin

from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'name', 'email', 'topic', 'yard_name', 'horses', 'emailed_at', 'handled')
    list_filter = ('topic', 'handled')
    list_editable = ('handled',)
    search_fields = ('name', 'email', 'yard_name', 'message')
    readonly_fields = ('topic', 'name', 'email', 'phone', 'yard_name', 'horses', 'quote', 'message', 'created_at', 'emailed_at')
