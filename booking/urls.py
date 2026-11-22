from django.urls import path
from . import views

app_name = 'booking'
urlpatterns = [
    path('', views.overview, name='overview'),
    path('settings/', views.settings_view, name='settings'),
    path('roles/', views.roles, name='roles'),
]
