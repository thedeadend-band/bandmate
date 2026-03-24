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
    # Playlists
    path('playlists/', views.playlist_list, name='playlist_list'),
    path('playlists/new/', views.playlist_create, name='playlist_create'),
    path('playlists/<int:playlist_id>/edit/', views.playlist_edit, name='playlist_edit'),
    path('playlists/<int:playlist_id>/delete/', views.playlist_delete, name='playlist_delete'),
    path('playlists/<int:playlist_id>/play/', views.playlist_player, name='playlist_player'),
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
