"""
URL configuration for billing app.
"""

from django.urls import path

from . import views

urlpatterns = [
    # Unified costs view
    path('costs/', views.CostsListView.as_view(), name='costs_list'),

    # Yard costs CRUD
    path('costs/yard/add/', views.YardCostCreateView.as_view(), name='yard_cost_create'),
    path('costs/yard/<int:pk>/edit/', views.YardCostUpdateView.as_view(), name='yard_cost_update'),
    path('costs/yard/<int:pk>/delete/', views.YardCostDeleteView.as_view(), name='yard_cost_delete'),
    path('costs/yard/<int:pk>/duplicate/', views.yard_cost_duplicate, name='yard_cost_duplicate'),

    # Feed stock & feed out
    path('feed-stock/add/', views.FeedStockCreateView.as_view(), name='feed_stock_create'),
    path('feed-out/<int:location_pk>/add/', views.feed_out_create, name='feed_out_create'),

    # Supplier autocomplete
    path('api/suppliers/', views.supplier_autocomplete, name='supplier_autocomplete'),

    # Extra charges
    path('charges/', views.ExtraChargeListView.as_view(), name='charge_list'),
    path('charges/add/', views.ExtraChargeCreateView.as_view(), name='charge_create'),
    path('charges/<int:pk>/edit/', views.ExtraChargeUpdateView.as_view(), name='charge_update'),
    path('charges/<int:pk>/delete/', views.ExtraChargeDeleteView.as_view(), name='charge_delete'),

    # Service providers
    path('providers/', views.ServiceProviderListView.as_view(), name='provider_list'),
    path('providers/add/', views.ServiceProviderCreateView.as_view(), name='provider_create'),
    path('providers/<int:pk>/edit/', views.ServiceProviderUpdateView.as_view(), name='provider_update'),
]
