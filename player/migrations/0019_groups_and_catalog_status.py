import os

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def migrate_default_group(apps, schema_editor):
    Group = apps.get_model('player', 'Group')
    GroupMembership = apps.get_model('player', 'GroupMembership')
    GroupSong = apps.get_model('player', 'GroupSong')
    SongReadiness = apps.get_model('player', 'SongReadiness')
    SongCelebrationDismissal = apps.get_model('player', 'SongCelebrationDismissal')
    Setlist = apps.get_model('player', 'Setlist')
    SetlistEntry = apps.get_model('player', 'SetlistEntry')
    CalendarSource = apps.get_model('player', 'CalendarSource')
    PlaybackPreference = apps.get_model('player', 'PlaybackPreference')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))

    default_group, _ = Group.objects.get_or_create(
        name='Default', defaults={'color_scheme': 'blue'})
    members = list(User.objects.filter(is_staff=False))
    for user in members:
        GroupMembership.objects.get_or_create(group=default_group, user=user)

    Setlist.objects.filter(group__isnull=True).update(group=default_group)
    CalendarSource.objects.filter(group__isnull=True).update(group=default_group)
    PlaybackPreference.objects.filter(group__isnull=True).update(group=default_group)

    song_names = set()
    songs_dir = getattr(settings, 'SONGS_DIR', '')
    if songs_dir and os.path.isdir(songs_dir):
        song_names.update(
            name for name in os.listdir(songs_dir)
            if not name.startswith('.') and os.path.isdir(os.path.join(songs_dir, name))
        )
    song_names.update(
        SetlistEntry.objects.filter(is_break=False)
        .exclude(song_name='').values_list('song_name', flat=True)
    )

    for song_name in song_names:
        entries = SetlistEntry.objects.filter(
            setlist__group=default_group, song_name=song_name, is_break=False,
        ).select_related('setlist')
        dates = []
        for entry in entries:
            dates.append(entry.setlist.date or entry.setlist.created_at.date())
        ready_date = min(dates) if dates else None
        group_song, _ = GroupSong.objects.get_or_create(
            group=default_group,
            song_name=song_name,
            defaults={'status': 'active', 'ready_date': ready_date},
        )
        for user in members:
            SongReadiness.objects.get_or_create(
                group_song=group_song, user=user, defaults={'is_ready': True})
            SongCelebrationDismissal.objects.get_or_create(
                group_song=group_song, user=user)


class Migration(migrations.Migration):

    dependencies = [
        ('player', '0018_playbackpreference'),
    ]

    operations = [
        migrations.CreateModel(
            name='Group',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, unique=True)),
                ('logo', models.FileField(blank=True, default='', upload_to='group-logos/')),
                ('color_scheme', models.CharField(choices=[('blue', 'Blue'), ('green', 'Green'), ('red', 'Red'), ('gold', 'Gold'), ('teal', 'Teal'), ('violet', 'Violet'), ('pink', 'Pink'), ('cyan', 'Cyan'), ('lime', 'Lime'), ('coral', 'Coral'), ('indigo', 'Indigo'), ('mono', 'Monochrome')], default='blue', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='GroupMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='player.group')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='group_memberships', to=settings.AUTH_USER_MODEL)),
            ],
            options={'unique_together': {('group', 'user')}},
        ),
        migrations.AddField(
            model_name='setlist', name='group',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='setlists', to='player.group'),
        ),
        migrations.AddField(
            model_name='calendarsource', name='group',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='calendar_sources', to='player.group'),
        ),
        migrations.AlterField(
            model_name='playbackpreference', name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='playback_preferences', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='playbackpreference', name='group',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='playback_preferences', to='player.group'),
        ),
        migrations.CreateModel(
            name='GroupSong',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('song_name', models.CharField(max_length=500)),
                ('status', models.CharField(choices=[('on_deck', 'On-Deck'), ('active', 'Ready to Play'), ('retired', 'Retired')], default='on_deck', max_length=20)),
                ('ready_date', models.DateField(blank=True, null=True)),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='songs', to='player.group')),
            ],
            options={'ordering': ['song_name'], 'unique_together': {('group', 'song_name')}},
        ),
        migrations.CreateModel(
            name='SongReadiness',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_ready', models.BooleanField(default=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('group_song', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='readiness', to='player.groupsong')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='song_readiness', to=settings.AUTH_USER_MODEL)),
            ],
            options={'unique_together': {('group_song', 'user')}},
        ),
        migrations.CreateModel(
            name='SongCelebrationDismissal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('dismissed_at', models.DateTimeField(auto_now_add=True)),
                ('group_song', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='celebration_dismissals', to='player.groupsong')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='song_celebration_dismissals', to=settings.AUTH_USER_MODEL)),
            ],
            options={'unique_together': {('group_song', 'user')}},
        ),
        migrations.RunPython(migrate_default_group, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='setlist', name='group',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='setlists', to='player.group'),
        ),
        migrations.AlterField(
            model_name='calendarsource', name='group',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='calendar_sources', to='player.group'),
        ),
        migrations.AlterField(
            model_name='playbackpreference', name='group',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='playback_preferences', to='player.group'),
        ),
        migrations.AlterUniqueTogether(
            name='playbackpreference', unique_together={('user', 'group')},
        ),
    ]
