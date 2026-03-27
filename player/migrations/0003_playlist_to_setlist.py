from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('player', '0002_sitesettings'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='Playlist',
            new_name='Setlist',
        ),
        migrations.RenameModel(
            old_name='PlaylistEntry',
            new_name='SetlistEntry',
        ),
        migrations.RenameField(
            model_name='setlistentry',
            old_name='playlist',
            new_name='setlist',
        ),
        migrations.AlterField(
            model_name='setlist',
            name='owner',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='setlists',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='setlistentry',
            name='song_name',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AlterUniqueTogether(
            name='setlistentry',
            unique_together={('setlist', 'position')},
        ),
        migrations.AddField(
            model_name='setlist',
            name='date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='setlistentry',
            name='is_break',
            field=models.BooleanField(default=False),
        ),
    ]
