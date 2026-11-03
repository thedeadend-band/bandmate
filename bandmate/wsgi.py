import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bandmate.settings')

application = get_wsgi_application()

from player.job_queue import start_audio_queue_worker

start_audio_queue_worker()
