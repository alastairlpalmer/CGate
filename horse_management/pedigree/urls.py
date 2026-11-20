from django.urls import path

from . import views

urlpatterns = [
    path('horses/<int:pk>/edit/', views.pedigree_edit, name='pedigree_edit'),
    path('records/<int:record_pk>/tree/', views.pedigree_tree, name='pedigree_tree'),
]
