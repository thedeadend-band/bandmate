from django.urls import path
from . import event_views as views
app_name = 'calendar_events'
urlpatterns = [
    path('new/', views.create, name='create'),
    path('operations/<int:operation_id>/', views.operation, name='operation'),
    path('<str:kind>/<int:event_id>/', views.detail, name='detail'),
    path('<str:kind>/<int:event_id>/edit/', views.edit, name='edit'),
    path('<str:kind>/<int:event_id>/delete/', views.delete, name='delete'),
]
