from django.urls import path

from . import views
from . import wizard_views

urlpatterns = [
    path('', views.song_list, name='song_list'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('songs/upload/', views.song_upload, name='song_upload'),
    # New Song Wizard (must come before songs/<str:song_name>/ to avoid matching "new" as a song name)
    path('songs/new/', wizard_views.wizard_start, name='wizard_start'),
    path('songs/new/<int:wizard_id>/step/<str:step_name>/', wizard_views.wizard_step, name='wizard_step'),
    path('songs/new/<int:wizard_id>/cancel/', wizard_views.wizard_cancel, name='wizard_cancel'),
    path('songs/<str:song_name>/delete/', views.song_delete, name='song_delete'),
    path('songs/<str:song_name>/download/', views.song_download_zip, name='song_download_zip'),
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
    # Download Tracks
    path('download-tracks/', views.download_tracks, name='download_tracks'),
    path('api/download-tracks/search/', views.download_tracks_search, name='download_tracks_search'),
    path('api/download-tracks/preview/<str:video_id>/', views.download_tracks_preview, name='download_tracks_preview'),
    path('api/download-tracks/flac/<str:video_id>/', views.download_tracks_flac, name='download_tracks_flac'),
    # Attributions
    path('attributions/', views.attributions, name='attributions'),
    # Admin console
    path('admin-console/', views.admin_users, name='admin_users'),
    path('admin-console/settings/', views.admin_settings, name='admin_settings'),
    path('admin-console/add/', views.admin_user_add, name='admin_user_add'),
    path('admin-console/<int:user_id>/edit/', views.admin_user_edit, name='admin_user_edit'),
    path('admin-console/<int:user_id>/delete/', views.admin_user_delete, name='admin_user_delete'),
    # New Song Wizard API endpoints
    path('api/songs/new/<int:wizard_id>/task-status/', wizard_views.wizard_task_status, name='wizard_task_status'),
    path('api/songs/new/<int:wizard_id>/youtube-search/', wizard_views.wizard_youtube_search, name='wizard_youtube_search'),
    path('api/songs/new/<int:wizard_id>/download/', wizard_views.wizard_start_download, name='wizard_start_download'),
    path('api/songs/new/<int:wizard_id>/upload/', wizard_views.wizard_upload_files, name='wizard_upload_files'),
    path('api/songs/new/<int:wizard_id>/detect-beats/', wizard_views.wizard_detect_beats, name='wizard_detect_beats'),
    path('api/songs/new/<int:wizard_id>/beats/', wizard_views.wizard_get_beats, name='wizard_get_beats'),
    path('api/songs/new/<int:wizard_id>/save-beats/', wizard_views.wizard_save_beats, name='wizard_save_beats'),
    path('api/songs/new/<int:wizard_id>/waveform/', wizard_views.wizard_waveform, name='wizard_waveform'),
    path('api/songs/new/<int:wizard_id>/quantize/', wizard_views.wizard_start_quantize, name='wizard_start_quantize'),
    path('api/songs/new/<int:wizard_id>/demucs/', wizard_views.wizard_start_demucs, name='wizard_start_demucs'),
    path('api/songs/new/<int:wizard_id>/stem-audio/<str:stem_name>/', wizard_views.wizard_stem_audio, name='wizard_stem_audio'),
]
