from django.urls import path
from . import views
from . import oauth_views

app_name = 'calendar_integrations'
urlpatterns = [
    path('google/', oauth_views.accounts, name='google_accounts'),
    path('google/connect/', oauth_views.connect, name='google_connect'),
    path('google/callback/', oauth_views.callback, name='google_callback'),
    path('google/<int:account_id>/select/', oauth_views.select_calendar, name='google_select'),
    path('google/<int:account_id>/refresh/', oauth_views.refresh_list, name='google_refresh'),
    path('google/<int:account_id>/disconnect/', oauth_views.disconnect, name='google_disconnect'),
    path('', views.sources, name='sources'),
    path('roles/', views.roles, name='roles'),
    path('<str:kind>/<int:source_id>/write-mode/', views.write_mode, name='write_mode'),
]
