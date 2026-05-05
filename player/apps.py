from django.apps import AppConfig


class PlayerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'player'

    def ready(self):
        from .models import StemSeparationJob
        try:
            StemSeparationJob.objects.filter(status='processing').update(
                status='paused', message='Paused (service restarted)')
        except Exception:
            pass
