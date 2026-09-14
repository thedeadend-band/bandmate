from django.db import migrations, models
import django.db.models.deletion


def migrate_calendar_url(apps, schema_editor):
    SiteSettings = apps.get_model('player', 'SiteSettings')
    CalendarSource = apps.get_model('player', 'CalendarSource')
    for site in SiteSettings.objects.all():
        if site.google_calendar_url:
            CalendarSource.objects.get_or_create(
                ics_url=site.google_calendar_url,
                defaults={
                    'name': 'Calendar',
                    'color': '#4a9eff',
                    'is_enabled': True,
                    'sync_interval_minutes': 60,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ('player', '0015_spotify_youtube_song_links'),
    ]

    operations = [
        migrations.CreateModel(
            name='CalendarSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('ics_url', models.URLField(max_length=2000)),
                ('color', models.CharField(default='#4a9eff', max_length=20)),
                ('is_enabled', models.BooleanField(default=True)),
                ('sync_interval_minutes', models.PositiveIntegerField(default=60)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='CalendarEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uid', models.CharField(max_length=500)),
                ('title', models.CharField(blank=True, default='', max_length=500)),
                ('starts_at', models.DateTimeField()),
                ('ends_at', models.DateTimeField()),
                ('is_all_day', models.BooleanField(default=False)),
                ('location', models.CharField(blank=True, default='', max_length=500)),
                ('description', models.TextField(blank=True, default='')),
                ('event_url', models.URLField(blank=True, default='', max_length=2000)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='player.calendarsource')),
            ],
            options={
                'ordering': ['starts_at', 'title'],
                'unique_together': {('source', 'uid', 'starts_at')},
            },
        ),
        migrations.RunPython(migrate_calendar_url, migrations.RunPython.noop),
    ]
