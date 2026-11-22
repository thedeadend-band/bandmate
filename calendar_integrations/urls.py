from django.urls import path
from . import views

app_name = 'calendar_integrations'
urlpatterns = [
    path('', views.sources, name='sources'),
    path('roles/', views.roles, name='roles'),
    path('<str:kind>/<int:source_id>/write-mode/', views.write_mode, name='write_mode'),
]
