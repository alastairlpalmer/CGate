from django.urls import path

from . import views

urlpatterns = [
    path('', views.training_home, name='training_home'),
    path('log/', views.ActivityLogView.as_view(), name='training_log'),
    path('log/mark/', views.log_mark, name='training_log_mark'),
    path('activities/add/', views.ActivityCreateView.as_view(), name='training_activity_create'),
    path('activities/<int:pk>/edit/', views.ActivityUpdateView.as_view(), name='training_activity_update'),
    path('activities/<int:pk>/delete/', views.activity_delete, name='training_activity_delete'),
    path('activities/<int:pk>/mark/', views.activity_mark, name='training_activity_mark'),
    path('setup/', views.training_setup, name='training_setup'),
    path('work-types/add/', views.WorkTypeCreateView.as_view(), name='training_work_type_create'),
    path('work-types/<int:pk>/edit/', views.WorkTypeUpdateView.as_view(), name='training_work_type_update'),
    path('work-types/<int:pk>/archive/', views.work_type_archive, name='training_work_type_archive'),
]
