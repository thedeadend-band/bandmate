from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('player', '0014_spotify_connection'),
    ]

    operations = [
        migrations.AddField(
            model_name='songwizard',
            name='spotify_track_uri',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='stemseparationjob',
            name='spotify_track_uri',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='stemseparationjob',
            name='youtube_selection',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
