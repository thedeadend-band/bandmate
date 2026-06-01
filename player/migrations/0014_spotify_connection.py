from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('player', '0013_stemseparationjob_gpu_used'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SpotifyConnection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('spotify_user_id', models.CharField(blank=True, default='', max_length=128)),
                ('access_token', models.TextField(blank=True, default='')),
                ('refresh_token', models.TextField(blank=True, default='')),
                ('token_expires_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='spotify_connection', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
