"""
URL configuration for health app.
"""

from django.urls import path

from . import views

urlpatterns = [
    # Health Dashboard
    path('', views.health_dashboard, name='health_dashboard'),

    # Bulk Actions
    path('bulk/form/', views.bulk_health_form, name='bulk_health_form'),
    path('bulk/apply/', views.bulk_health_apply, name='bulk_health_apply'),

    # Vaccinations
    path('vaccinations/', views.VaccinationListView.as_view(), name='vaccination_list'),
    path('vaccinations/add/', views.VaccinationCreateView.as_view(), name='vaccination_create'),
    path('vaccinations/<int:pk>/edit/', views.VaccinationUpdateView.as_view(), name='vaccination_update'),

    # Vaccination Types
    path('vaccination-types/', views.VaccinationTypeListView.as_view(), name='vaccination_type_list'),
    path('vaccination-types/add/', views.VaccinationTypeCreateView.as_view(), name='vaccination_type_create'),
    path('vaccination-types/<int:pk>/edit/', views.VaccinationTypeUpdateView.as_view(), name='vaccination_type_update'),

    # Farrier
    path('farrier/', views.FarrierListView.as_view(), name='farrier_list'),
    path('farrier/add/', views.FarrierCreateView.as_view(), name='farrier_create'),
    path('farrier/<int:pk>/edit/', views.FarrierUpdateView.as_view(), name='farrier_update'),

    # Worming
    path('worming/', views.WormingListView.as_view(), name='worming_list'),
    path('worming/add/', views.WormingCreateView.as_view(), name='worming_create'),
    path('worming/<int:pk>/edit/', views.WormingUpdateView.as_view(), name='worming_update'),

    # Egg Counts
    path('egg-counts/', views.WormEggCountListView.as_view(), name='egg_count_list'),
    path('egg-counts/add/', views.WormEggCountCreateView.as_view(), name='egg_count_create'),
    path('egg-counts/<int:pk>/edit/', views.WormEggCountUpdateView.as_view(), name='egg_count_update'),

    # Medical Conditions
    path('conditions/', views.MedicalConditionListView.as_view(), name='condition_list'),
    path('conditions/add/', views.MedicalConditionCreateView.as_view(), name='condition_create'),
    path('conditions/<int:pk>/edit/', views.MedicalConditionUpdateView.as_view(), name='condition_update'),

    # Vet Visits
    path('vet-visits/', views.VetVisitListView.as_view(), name='vet_visit_list'),
    path('vet-visits/add/', views.VetVisitCreateView.as_view(), name='vet_visit_create'),
    path('vet-visits/<int:pk>/edit/', views.VetVisitUpdateView.as_view(), name='vet_visit_update'),

    # Breeding
    path('breeding/', views.BreedingRecordListView.as_view(), name='breeding_list'),
    path('breeding/add/', views.BreedingRecordCreateView.as_view(), name='breeding_create'),
    path('breeding/<int:pk>/edit/', views.BreedingRecordUpdateView.as_view(), name='breeding_update'),

    # Quick-add helpers
    path('quick-add-vet/', views.quick_add_vet, name='quick_add_vet'),
]
