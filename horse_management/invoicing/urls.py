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
    path('bulk-action/', views.invoice_bulk_action, name='invoice_bulk_action'),
    path('<int:pk>/payments/add/', views.payment_create, name='payment_create'),
    path('payments/<int:pk>/delete/', views.payment_delete, name='payment_delete'),
    path('debtors/', views.aged_debtors, name='aged_debtors'),
    path('statements/<int:owner_pk>/', views.owner_statement, name='owner_statement'),
    path('statements/<int:owner_pk>/pdf/', views.owner_statement_pdf, name='owner_statement_pdf'),
    path('statements/<int:owner_pk>/email/', views.owner_statement_email, name='owner_statement_email'),
]
