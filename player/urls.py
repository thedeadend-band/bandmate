from django.urls import path

from . import views

urlpatterns = [
    path('', views.song_list, name='song_list'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('songs/upload/', views.song_upload, name='song_upload'),
    path('songs/<str:song_name>/delete/', views.song_delete, name='song_delete'),
    path('songs/<str:song_name>/', views.song_player, name='song_player'),
    path(
        'api/songs/<str:song_name>/tracks/<str:track_filename>/audio/',
        views.track_audio,
        name='track_audio',
    ),
    path(
        'api/songs/<str:song_name>/tracks/<str:track_filename>/waveform/',
        views.track_waveform,
        name='track_waveform',
    ),
    # Setlists
    path('setlists/', views.setlist_list, name='setlist_list'),
    path('setlists/new/', views.setlist_create, name='setlist_create'),
    path('setlists/<int:setlist_id>/edit/', views.setlist_edit, name='setlist_edit'),
    path('setlists/<int:setlist_id>/delete/', views.setlist_delete, name='setlist_delete'),
    path('setlists/<int:setlist_id>/play/', views.setlist_player, name='setlist_player'),
    path('setlists/<int:setlist_id>/export/', views.setlist_export, name='setlist_export'),
    path('setlists/<int:setlist_id>/export-midi/', views.setlist_export_midi, name='setlist_export_midi'),
    path('api/songs/<str:song_name>/master/audio/', views.master_audio, name='master_audio'),
    path('api/songs/<str:song_name>/master/waveform/', views.master_waveform, name='master_waveform'),
    path('api/songs/<str:song_name>/info/', views.song_info_api, name='song_info_api'),
    # Calendar
    path('calendar/', views.calendar_view, name='calendar'),
    # Admin console
    path('admin-console/', views.admin_users, name='admin_users'),
    path('admin-console/settings/', views.admin_settings, name='admin_settings'),
    path('admin-console/add/', views.admin_user_add, name='admin_user_add'),
    path('admin-console/<int:user_id>/edit/', views.admin_user_edit, name='admin_user_edit'),
    path('admin-console/<int:user_id>/delete/', views.admin_user_delete, name='admin_user_delete'),
]
