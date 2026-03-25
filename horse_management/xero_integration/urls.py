from django.urls import path

from . import views

urlpatterns = [
    path('settings/', views.xero_settings, name='xero_settings'),
    path('connect/', views.xero_connect, name='xero_connect'),
    path('callback/', views.xero_callback, name='xero_callback'),
    path('disconnect/', views.xero_disconnect, name='xero_disconnect'),
    path('push/<int:pk>/', views.xero_push_invoice, name='xero_push_invoice'),
    path('status/<int:pk>/', views.xero_invoice_status, name='xero_invoice_status'),
]
