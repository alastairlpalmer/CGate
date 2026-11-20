from django.urls import path

from . import editor, import_views, views

urlpatterns = [
    path('horses/<int:pk>/edit/', views.pedigree_edit, name='pedigree_edit'),
    path('records/<int:record_pk>/tree/', views.pedigree_tree, name='pedigree_tree'),
    # Fill in records (plan §7.5)
    path('fill-in/', editor.records_horses, name='records_horses'),
    path('fill-in/undo/', editor.records_undo, name='records_undo'),
    path('fill-in/download/', editor.records_export, name='records_export'),
    path('fill-in/records/', editor.records_pedigree, name='records_pedigree'),
    path('fill-in/merge/', editor.records_merge, name='records_merge'),
    path('fill-in/merges/<int:pk>/undo/', editor.records_merge_undo, name='records_merge_undo'),
    # Import a file and the back-fill (plan §7.6, §7.7)
    path('fill-in/imports/', import_views.records_imports, name='records_imports'),
    path('fill-in/imports/template/', import_views.records_import_template, name='records_import_template'),
    path('fill-in/imports/backfill/', import_views.records_backfill, name='records_backfill'),
    path('fill-in/imports/<int:pk>/', import_views.records_import_review, name='records_import_review'),
]
