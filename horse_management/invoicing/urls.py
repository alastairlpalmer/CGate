"""
URL configuration for invoicing app.
"""

from django.urls import path

from . import views

urlpatterns = [
    path('', views.InvoiceListView.as_view(), name='invoice_list'),
    path('create/', views.invoice_create, name='invoice_create'),
    path('generate/', views.invoice_generate_monthly, name='invoice_generate'),
    path('preview/', views.invoice_preview, name='invoice_preview'),
    path('<int:pk>/', views.InvoiceDetailView.as_view(), name='invoice_detail'),
    path('<int:pk>/edit/', views.InvoiceUpdateView.as_view(), name='invoice_update'),
    path('<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
    path('<int:pk>/csv/', views.invoice_csv, name='invoice_csv'),
    path('<int:pk>/send/', views.invoice_send, name='invoice_send'),
    path('<int:pk>/mark-paid/', views.invoice_mark_paid, name='invoice_mark_paid'),
    path('export-csv/', views.invoice_export_csv, name='invoice_export_csv'),
    path('runs/<int:run_id>/', views.invoice_run_detail, name='invoice_run_detail'),
    path(
        'runs/<int:run_id>/bundle/<int:owner_id>.pdf',
        views.invoice_run_bundle_pdf,
        name='invoice_run_bundle_pdf',
    ),
]
