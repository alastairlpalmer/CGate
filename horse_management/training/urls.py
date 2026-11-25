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

urlpatterns += [
    path('lots/', views.training_lots, name='training_lots'),
    path('lots/add/', views.LotCreateView.as_view(), name='training_lot_create'),
    path('lots/<int:pk>/edit/', views.LotUpdateView.as_view(), name='training_lot_update'),
    path('lots/<int:pk>/archive/', views.lot_archive, name='training_lot_archive'),
    path('lots/<int:pk>/horses/', views.lot_add_horses, name='training_lot_add_horses'),
    path('lots/members/<int:pk>/', views.lot_member_action, name='training_lot_member'),
    path('horses/<int:horse_pk>/status/', views.horse_training_edit, name='training_horse_edit'),
]

urlpatterns += [
    path('board/', views.training_board, name='training_board'),
    path('board/<str:day>/', views.training_board, name='training_board_day'),
    path('board/<str:day>/start/', views.board_start, name='training_board_start'),
    path('board/<str:day>/add-lot/<int:lot_pk>/', views.board_add_lot, name='training_board_add_lot'),
    path('lot-days/<int:pk>/change/', views.lot_day_change, name='training_lot_day_change'),
    path('lot-days/<int:pk>/horses/', views.lot_day_horses, name='training_lot_day_horses'),
    path('lot-days/<int:pk>/<str:action>/', views.lot_day_action, name='training_lot_day_action'),
    path('activities/<int:pk>/move/', views.activity_move, name='training_activity_move'),
]

urlpatterns += [
    path('venues/add/', views.VenueCreateView.as_view(), name='training_venue_create'),
    path('venues/<int:pk>/edit/', views.VenueUpdateView.as_view(), name='training_venue_update'),
    path('riders/add/', views.RiderCreateView.as_view(), name='training_rider_create'),
    path('riders/<int:pk>/edit/', views.RiderUpdateView.as_view(), name='training_rider_update'),
    path('setup/<str:kind>/<int:pk>/archive/', views.setup_archive, name='training_setup_archive'),
    path('lots/<int:pk>/week/', views.lot_week_plan, name='training_lot_week_plan'),
]

# Board redesign (docs/TRAINING_BOARD_REDESIGN_PLAN.md). The day note comes
# before the sheet, or ``note`` would be read as a sheet mode.
urlpatterns += [
    path('plan/', views.training_plan, name='training_plan'),
    path('plan/<str:day>/', views.training_plan, name='training_plan_week'),
    path('board/<str:day>/note/', views.day_note, name='training_day_note'),
    # "horse", not "horses": the sidebar marks Horses for any address with
    # /horses/ in it, and this page belongs to Training.
    path('horse/<int:horse_pk>/', views.horse_profile, name='training_horse_profile'),
    path('horse/<int:horse_pk>/notes/add/', views.TrainingNoteCreateView.as_view(), name='training_note_create'),
    path('notes/<int:pk>/edit/', views.TrainingNoteUpdateView.as_view(), name='training_note_update'),
    path('notes/<int:pk>/delete/', views.training_note_delete, name='training_note_delete'),
]

urlpatterns += [
    path('board/<str:day>/<str:mode>/', views.board_sheet, name='training_board_sheet'),
]
