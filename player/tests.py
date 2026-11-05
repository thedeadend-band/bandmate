import json
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from .context_processors import group_context
from .models import (
    CalendarSource, Group, GroupMembership, GroupSong, PitchShiftJob, Setlist,
    SetlistEntry, SongCelebrationDismissal, SongReadiness, SongWizard,
    StemSeparationJob, UserProfile,
)
from .user_display import user_display_name


class WizardNavigationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='wizard-admin', password='test', is_staff=True)
        self.group = Group.objects.create(name='Wizard Band')
        self.client.force_login(self.admin)
        session = self.client.session
        session['active_group_id'] = self.group.pk
        session.save()

    def test_group_logo_request_does_not_delete_active_wizard(self):
        wizard = SongWizard.objects.create(created_by=self.admin)
        song_info_url = reverse(
            'wizard_step', args=[wizard.pk, 'song_info'])

        self.assertEqual(self.client.get(song_info_url).status_code, 200)
        self.client.get(reverse('group_logo', args=[self.group.pk]))

        self.assertTrue(SongWizard.objects.filter(pk=wizard.pk).exists())
        response = self.client.post(song_info_url, {
            'title': 'Route Test',
            'artist': 'Test Artist',
        })
        self.assertRedirects(
            response,
            reverse('wizard_step', args=[wizard.pk, 'tempo_key']),
            fetch_redirect_response=False,
        )

    def test_metadata_step_saves_click_multiplier_separately(self):
        wizard = SongWizard.objects.create(
            created_by=self.admin, current_step='tempo_key',
            title='Would?', artist='Alice In Chains')

        response = self.client.post(
            reverse('wizard_step', args=[wizard.pk, 'tempo_key']),
            {
                'action': 'next', 'tempo': '99',
                'click_track_multiplier': '2',
                'key': 'F# minor', 'time_signature': '4/4',
            },
        )

        wizard.refresh_from_db()
        self.assertRedirects(
            response,
            reverse('wizard_step', args=[wizard.pk, 'lyrics']),
            fetch_redirect_response=False,
        )
        self.assertEqual(wizard.tempo, 99)
        self.assertEqual(wizard.click_track_multiplier, 2)


class QuantizationTimingTests(TestCase):
    def test_beat_detection_uses_song_tempo_as_reference(self):
        from .wizard_services import detect_beats

        librosa = Mock()
        librosa.load.return_value = ([0.0, 0.0], 22050)
        librosa.beat.beat_track.return_value = (99.0, [1, 2, 3])
        beat_times = Mock()
        beat_times.tolist.return_value = [0.5, 1.0, 1.5]
        librosa.frames_to_time.return_value = beat_times
        wizard = Mock(tempo=99)

        with patch.dict('sys.modules', {'librosa': librosa}):
            detected = detect_beats('Master.wav', wizard)

        self.assertEqual(detected, [0.5, 1.0, 1.5])
        self.assertEqual(librosa.beat.beat_track.call_args.kwargs['bpm'], 99.0)
        self.assertEqual(
            wizard.bg_task_message,
            'Detected 3 beats at 99 BPM (metadata reference)',
        )

    def test_double_tempo_preserves_audio_beat_interval(self):
        from .wizard_services import get_quantization_timing

        timing = get_quantization_timing([0.5, 1.0, 1.5, 2.0], 240)

        self.assertEqual(timing['interval_multiplier'], 2.0)
        self.assertAlmostEqual(timing['audio_interval'], 0.5)
        self.assertAlmostEqual(timing['audio_bpm'], 120)
        self.assertAlmostEqual(timing['target_interval'], 0.25)

    def test_half_tempo_preserves_audio_beat_interval(self):
        from .wizard_services import get_quantization_timing

        timing = get_quantization_timing([0.5, 1.0, 1.5, 2.0], 60)

        self.assertEqual(timing['interval_multiplier'], 0.5)
        self.assertAlmostEqual(timing['audio_interval'], 0.5)
        self.assertAlmostEqual(timing['audio_bpm'], 120)
        self.assertAlmostEqual(timing['target_interval'], 1.0)

    def test_normal_tempo_adjustment_is_not_overridden(self):
        from .wizard_services import get_quantization_timing

        timing = get_quantization_timing([0.0, 0.6, 1.2, 1.8], 120)

        self.assertEqual(timing['interval_multiplier'], 1.0)
        self.assertAlmostEqual(timing['audio_interval'], 0.5)
        self.assertAlmostEqual(timing['audio_bpm'], 120)

    def test_click_multiplier_does_not_change_song_tempo(self):
        from .tempo import click_bpm

        self.assertEqual(click_bpm(99, 2), 198)

    def test_midi_export_applies_click_multiplier(self):
        import mido
        from types import SimpleNamespace
        from .export import render_setlist_midi

        midi = render_setlist_midi(
            SimpleNamespace(name='Tempo Test'),
            [{
                'song_name': 'Would?',
                'info': {'tempo': 99, 'click_track_multiplier': 2},
            }],
        )
        parsed = mido.MidiFile(file=midi)
        tempo_message = next(
            message for message in parsed.tracks[0]
            if message.type == 'set_tempo'
        )

        self.assertAlmostEqual(mido.tempo2bpm(tempo_message.tempo), 198, places=2)


class PlaybackVariantTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(SONGS_DIR=self.temp_dir.name)
        self.settings_override.enable()
        self.user = User.objects.create_user(
            username='variant-user', password='test')
        self.group = Group.objects.create(name='Variant Band')
        GroupMembership.objects.create(group=self.group, user=self.user)
        self.song_name = 'Test Artist - Tempo Song'
        self.song_dir = Path(self.temp_dir.name) / self.song_name
        self.song_dir.mkdir()
        (self.song_dir / 'Master.wav').write_bytes(b'audio')
        (self.song_dir / 'info.json').write_text(json.dumps({
            'artist': 'Test Artist',
            'title': 'Tempo Song',
            'tempo': 100,
            'key': 'A',
        }))
        GroupSong.objects.create(group=self.group, song_name=self.song_name)
        self.client.force_login(self.user)
        session = self.client.session
        session['active_group_id'] = self.group.pk
        session.save()

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()

    @patch('player.pitch_services.start_pitch_worker')
    def test_tempo_only_change_is_enqueued(self, start_worker):
        response = self.client.post(
            reverse('song_pitch_update', args=[self.song_name]),
            {'semitones': '0', 'tempo_bpm': '112'},
        )

        job = PitchShiftJob.objects.get(song_name=self.song_name)
        self.assertEqual(job.semitones, 0)
        self.assertEqual(job.tempo_bpm, 112)
        self.assertEqual(job.status, 'queued')
        start_worker.assert_called_once()
        self.assertRedirects(
            response, reverse('song_list'), fetch_redirect_response=False)

    def test_song_page_shows_combined_pitch_and_tempo_controls(self):
        response = self.client.get(
            reverse('song_player', args=[self.song_name]))

        self.assertContains(response, 'Playback pitch &amp; tempo')
        self.assertContains(response, 'name="tempo_bpm"')
        self.assertContains(response, 'value="100"')

    def test_songs_page_uses_add_to_catalog_label(self):
        available = Path(self.temp_dir.name) / 'Test Artist - Available Song'
        available.mkdir()
        (available / 'Master.wav').write_bytes(b'audio')
        (available / 'info.json').write_text(json.dumps({
            'artist': 'Test Artist', 'title': 'Available Song', 'tempo': 100,
        }))

        response = self.client.get(reverse('group_songs'))

        self.assertContains(response, 'Add to Catalog')

    def test_returning_to_original_removes_combined_variant(self):
        info_path = self.song_dir / 'info.json'
        info = json.loads(info_path.read_text())
        info.update({'pitch_shift_semitones': -1, 'tempo_shift_bpm': 112})
        info_path.write_text(json.dumps(info))
        variant_dir = self.song_dir / 'pitch-variants' / 'active'
        variant_dir.mkdir(parents=True)
        PitchShiftJob.objects.create(
            song_name=self.song_name, semitones=-1, tempo_bpm=112,
            created_by=self.user, status='done')

        response = self.client.post(
            reverse('song_pitch_update', args=[self.song_name]),
            {'semitones': '0', 'tempo_bpm': '100'},
        )

        saved_info = json.loads(info_path.read_text())
        self.assertNotIn('pitch_shift_semitones', saved_info)
        self.assertNotIn('tempo_shift_bpm', saved_info)
        self.assertFalse((self.song_dir / 'pitch-variants').exists())
        self.assertFalse(PitchShiftJob.objects.filter(song_name=self.song_name).exists())
        self.assertRedirects(
            response,
            reverse('song_player', args=[self.song_name]),
            fetch_redirect_response=False,
        )

    def test_combined_processing_uses_one_rubberband_pass(self):
        import numpy as np
        from .pitch_services import _write_variant_track

        source = Path('Guitar.wav')
        destination = Path('Guitar-variant.flac')
        audio = np.zeros((1000, 2), dtype=np.float32)
        processed = np.zeros((800, 2), dtype=np.float32)

        with (
            patch('player.pitch_services.sf.read', return_value=(audio, 48000)),
            patch('player.pitch_services.sf.write') as write_audio,
            patch(
                'player.pitch_services.sf.info',
                return_value=SimpleNamespace(frames=800, samplerate=48000),
            ),
            patch('pyrubberband.time_stretch', return_value=processed) as stretch,
        ):
            _write_variant_track(source, destination, 2, 1.25)

        stretch.assert_called_once()
        self.assertEqual(stretch.call_args.args[2], 1.25)
        self.assertEqual(
            stretch.call_args.kwargs['rbargs'],
            {'--pitch': 2, '--formant': ''},
        )
        self.assertEqual(write_audio.call_args.args[1].shape, (800, 2))

    def test_tempo_change_does_not_pitch_shift_drums(self):
        import numpy as np
        from .pitch_services import _write_variant_track

        audio = np.zeros((1000, 2), dtype=np.float32)
        with (
            patch('player.pitch_services.sf.read', return_value=(audio, 48000)),
            patch('player.pitch_services.sf.write'),
            patch(
                'player.pitch_services.sf.info',
                return_value=SimpleNamespace(frames=800, samplerate=48000),
            ),
            patch(
                'pyrubberband.time_stretch',
                return_value=np.zeros((800, 2), dtype=np.float32),
            ) as stretch,
        ):
            _write_variant_track(
                Path('Drums.wav'), Path('Drums-variant.flac'), 3, 1.25)

        self.assertEqual(stretch.call_args.kwargs['rbargs'], {})

    def test_tempo_variant_scales_lyrics_and_offset(self):
        from .views import _load_lyrics, _load_song_info

        info_path = self.song_dir / 'info.json'
        info = json.loads(info_path.read_text())
        info.update({'tempo_shift_bpm': 125, 'lyric_offset': 2.5})
        info_path.write_text(json.dumps(info))
        (self.song_dir / 'lyrics.lrc').write_text(
            '[00:10.00]First line\n[00:20.00]Second line\n')

        loaded_info = _load_song_info(self.song_dir)
        lyrics = _load_lyrics(self.song_dir)

        self.assertEqual(loaded_info['original_tempo'], 100)
        self.assertEqual(loaded_info['tempo'], 125)
        self.assertEqual(loaded_info['lyric_offset_secs'], 2.0)
        self.assertEqual([line['time'] for line in lyrics], [8.0, 16.0])

    def test_processing_replaces_the_single_active_variant(self):
        from .pitch_services import _process_job

        for filename in ('Master.wav', 'Guitar.wav', 'Drums.wav'):
            (self.song_dir / filename).write_bytes(b'audio')
        old_variant = self.song_dir / 'pitch-variants' / '+1'
        old_variant.mkdir(parents=True)
        (old_variant / 'old.flac').write_bytes(b'old')
        job = PitchShiftJob.objects.create(
            song_name=self.song_name, semitones=2, tempo_bpm=125,
            created_by=self.user)

        def write_track(source, destination, semitones, tempo_rate):
            destination.write_bytes(b'variant')

        def build_master(track_paths, original_master, destination, expected_frames):
            destination.write_bytes(b'master')

        with (
            patch('player.audio_tools.require_rubberband'),
            patch(
                'player.pitch_services.sf.info',
                return_value=SimpleNamespace(frames=1000, samplerate=48000),
            ),
            patch(
                'player.pitch_services._write_variant_track',
                side_effect=write_track,
            ) as write_variant,
            patch(
                'player.pitch_services._build_master',
                side_effect=build_master,
            ),
        ):
            _process_job(job)

        active_dir = self.song_dir / 'pitch-variants' / 'active'
        manifest = json.loads((active_dir / 'manifest.json').read_text())
        saved_info = json.loads((self.song_dir / 'info.json').read_text())
        self.assertTrue(active_dir.is_dir())
        self.assertFalse(old_variant.exists())
        self.assertEqual(manifest['semitones'], 2)
        self.assertEqual(manifest['source_tempo_bpm'], 100)
        self.assertEqual(manifest['tempo_bpm'], 125)
        self.assertEqual(manifest['tempo_rate'], 1.25)
        self.assertEqual(saved_info['tempo'], 100)
        self.assertEqual(saved_info['pitch_shift_semitones'], 2)
        self.assertEqual(saved_info['tempo_shift_bpm'], 125)
        self.assertTrue(all(
            call.args[3] == 1.25 for call in write_variant.call_args_list))
        job.refresh_from_db()
        self.assertEqual(job.status, 'done')


class AudioJobQueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='queue-user', password='test')

    def stem_job(self, title, **values):
        return StemSeparationJob.objects.create(
            title=title,
            artist='Test Artist',
            created_by=self.user,
            staging_dir=f'/tmp/{title}',
            **values,
        )

    def test_processing_job_blocks_newer_queued_job(self):
        from .job_queue import _next_job

        active = self.stem_job('Active', status='processing')
        queued = PitchShiftJob.objects.create(
            song_name='Queued Pitch', semitones=-1,
            created_by=self.user,
        )

        self.assertEqual(_next_job(), ('busy', None))
        active.refresh_from_db()
        queued.refresh_from_db()
        self.assertEqual(active.status, 'processing')
        self.assertEqual(queued.status, 'queued')

    def test_recovered_job_keeps_fifo_precedence(self):
        from .job_queue import _next_job, _recover_interrupted_jobs

        interrupted = self.stem_job('Interrupted', status='processing')
        newer = self.stem_job('Newer')

        _recover_interrupted_jobs()
        kind, selected = _next_job()

        self.assertEqual(kind, 'stem')
        self.assertEqual(selected.pk, interrupted.pk)
        newer.refresh_from_db()
        self.assertEqual(newer.status, 'queued')

    def test_recovery_preserves_user_paused_job(self):
        from .job_queue import _recover_interrupted_jobs

        user_paused = self.stem_job(
            'User Paused', status='paused', message='Paused by user')
        legacy_restart = self.stem_job(
            'Restart Paused', status='paused',
            message='Paused')

        _recover_interrupted_jobs()

        user_paused.refresh_from_db()
        legacy_restart.refresh_from_db()
        self.assertEqual(user_paused.status, 'paused')
        self.assertEqual(legacy_restart.status, 'queued')

    def test_recovered_pitch_job_keeps_fifo_precedence_over_new_stem(self):
        from .job_queue import _next_job, _recover_interrupted_jobs

        interrupted = PitchShiftJob.objects.create(
            song_name='Interrupted Pitch', semitones=-2,
            created_by=self.user, status='failed',
            message='Interrupted (service restarted)',
        )
        newer = self.stem_job('Newer Stem')

        _recover_interrupted_jobs()
        kind, selected = _next_job()

        self.assertEqual(kind, 'pitch')
        self.assertEqual(selected.pk, interrupted.pk)
        newer.refresh_from_db()
        self.assertEqual(newer.status, 'queued')


class GroupCatalogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='test', is_staff=True)
        self.alice = User.objects.create_user(username='alice', password='test')
        self.bob = User.objects.create_user(username='bob', password='test')
        self.primary = Group.objects.create(name='Primary')
        self.secondary = Group.objects.create(name='Secondary')
        GroupMembership.objects.create(group=self.primary, user=self.alice)
        GroupMembership.objects.create(group=self.primary, user=self.bob)
        GroupMembership.objects.create(group=self.secondary, user=self.alice)

    def select_group(self, client, group):
        session = client.session
        session['active_group_id'] = group.pk
        session.save()

    def test_multiple_group_members_must_select_a_group(self):
        self.client.force_login(self.alice)

        response = self.client.get(reverse('song_list'))

        self.assertRedirects(
            response, reverse('group_select'), fetch_redirect_response=False)

    def test_setlists_are_scoped_to_the_active_group(self):
        Setlist.objects.create(
            group=self.primary, owner=self.alice, name='Primary Set')
        Setlist.objects.create(
            group=self.secondary, owner=self.alice, name='Secondary Set')
        self.client.force_login(self.alice)
        self.select_group(self.client, self.primary)

        response = self.client.get(reverse('setlist_list'))

        self.assertContains(response, 'Primary Set')
        self.assertNotContains(response, 'Secondary Set')

    def test_catalog_remove_only_removes_song_from_active_group(self):
        primary_song = GroupSong.objects.create(
            group=self.primary, song_name='Shared Song', status='active')
        primary_song_id = primary_song.pk
        GroupSong.objects.create(
            group=self.secondary, song_name='Shared Song', status='active')
        SongReadiness.objects.create(
            group_song=primary_song, user=self.alice, is_ready=True)
        self.client.force_login(self.admin)
        self.select_group(self.client, self.primary)

        response = self.client.post(reverse(
            'catalog_song_remove', kwargs={'song_name': 'Shared Song'}))

        self.assertRedirects(response, reverse('song_list'))
        self.assertFalse(GroupSong.objects.filter(
            group=self.primary, song_name='Shared Song').exists())
        self.assertTrue(GroupSong.objects.filter(
            group=self.secondary, song_name='Shared Song').exists())
        self.assertFalse(SongReadiness.objects.filter(
            group_song_id=primary_song_id).exists())

    def test_catalog_remove_rejects_song_used_by_group_setlist(self):
        GroupSong.objects.create(
            group=self.primary, song_name='Setlist Song', status='active')
        setlist = Setlist.objects.create(
            group=self.primary, owner=self.alice, name='Current Set')
        SetlistEntry.objects.create(
            setlist=setlist, song_name='Setlist Song', position=0)
        self.client.force_login(self.admin)
        self.select_group(self.client, self.primary)

        response = self.client.post(reverse(
            'catalog_song_remove', kwargs={'song_name': 'Setlist Song'}))

        self.assertEqual(response.status_code, 400)
        self.assertTrue(GroupSong.objects.filter(
            group=self.primary, song_name='Setlist Song').exists())

    @patch('player.views._get_available_songs')
    def test_permanent_delete_is_only_visible_to_admins(self, available_songs_mock):
        available_songs_mock.return_value = [{
            'name': 'Available Song', 'artist': 'Artist',
            'title': 'Available Song', 'track_count': 4,
        }]
        self.client.force_login(self.alice)
        self.select_group(self.client, self.primary)

        response = self.client.get(reverse('group_songs'))
        self.assertNotContains(response, 'Delete permanently')
        response = self.client.post(reverse(
            'song_delete', kwargs={'song_name': 'Available Song'}))
        self.assertEqual(response.status_code, 404)

        self.client.force_login(self.admin)
        self.select_group(self.client, self.primary)
        response = self.client.get(reverse('group_songs'))
        self.assertContains(response, 'Delete permanently')

    def test_setlists_are_sorted_by_date_descending_with_undated_last(self):
        Setlist.objects.create(
            group=self.primary, owner=self.alice, name='Undated')
        Setlist.objects.create(
            group=self.primary, owner=self.alice, name='Older',
            date=date(2026, 10, 1))
        Setlist.objects.create(
            group=self.primary, owner=self.alice, name='Newest',
            date=date(2026, 12, 1))
        self.client.force_login(self.alice)
        self.select_group(self.client, self.primary)

        response = self.client.get(reverse('setlist_list'))

        self.assertEqual(
            [setlist.name for setlist in response.context['setlists']],
            ['Newest', 'Older', 'Undated'],
        )

    @patch('player.views._get_available_songs')
    def test_catalog_can_filter_by_song_status(self, available_songs_mock):
        available_songs_mock.return_value = [
            {'name': 'Ready Song', 'artist': 'Alpha', 'title': 'Ready Song', 'track_count': 4,
             'spotify_track_url': '', 'youtube_video_url': ''},
            {'name': 'Deck Song', 'artist': 'Beta', 'title': 'Deck Song', 'track_count': 4,
             'spotify_track_url': '', 'youtube_video_url': ''},
            {'name': 'Retired Song', 'artist': 'Gamma', 'title': 'Retired Song', 'track_count': 4,
             'spotify_track_url': '', 'youtube_video_url': ''},
        ]
        GroupSong.objects.create(
            group=self.primary, song_name='Ready Song', status='active')
        GroupSong.objects.create(
            group=self.primary, song_name='Deck Song', status='on_deck')
        GroupSong.objects.create(
            group=self.primary, song_name='Retired Song', status='retired')
        self.client.force_login(self.alice)
        self.select_group(self.client, self.primary)

        response = self.client.get(
            reverse('song_list'), {'status': 'on_deck', 'sort': 'title'})

        self.assertEqual(
            [song['name'] for song in response.context['songs']],
            ['Deck Song'],
        )
        self.assertEqual(response.context['status_filter'], 'on_deck')

    @patch('player.views._get_available_songs')
    def test_on_deck_catalog_card_hides_historical_ready_date(
        self, available_songs_mock,
    ):
        available_songs_mock.return_value = [{
            'name': 'Learning Song', 'artist': 'Artist',
            'title': 'Learning Song', 'track_count': 4,
            'spotify_track_url': '', 'youtube_video_url': '',
        }]
        historical_date = date(2026, 10, 15)
        group_song = GroupSong.objects.create(
            group=self.primary,
            song_name='Learning Song',
            status='on_deck',
            ready_date=historical_date,
        )
        self.client.force_login(self.alice)
        self.select_group(self.client, self.primary)

        response = self.client.get(reverse('song_list'))

        self.assertIsNone(response.context['songs'][0]['ready_date'])
        self.assertNotContains(response, 'Ready Oct 15, 2026')
        group_song.refresh_from_db()
        self.assertEqual(group_song.ready_date, historical_date)

    @patch('player.views._get_available_songs')
    def test_catalog_displays_and_sorts_by_readiness(self, available_songs_mock):
        charlie = User.objects.create_user(username='charlie', password='test')
        dana = User.objects.create_user(username='dana', password='test')
        GroupMembership.objects.create(group=self.primary, user=charlie)
        GroupMembership.objects.create(group=self.primary, user=dana)
        song_data = [
            ('Quarter Ready', 'Delta'),
            ('Three Quarters Ready', 'Charlie'),
            ('Ready Song', 'Alpha'),
            ('Retired Song', 'Bravo'),
        ]
        available_songs_mock.side_effect = lambda: [{
            'name': name, 'artist': artist, 'title': name, 'track_count': 4,
            'spotify_track_url': '', 'youtube_video_url': '',
        } for name, artist in song_data]
        quarter = GroupSong.objects.create(
            group=self.primary, song_name='Quarter Ready', status='on_deck')
        three_quarters = GroupSong.objects.create(
            group=self.primary,
            song_name='Three Quarters Ready',
            status='on_deck',
        )
        GroupSong.objects.create(
            group=self.primary, song_name='Ready Song', status='active')
        GroupSong.objects.create(
            group=self.primary, song_name='Retired Song', status='retired')
        SongReadiness.objects.create(
            group_song=quarter, user=self.alice, is_ready=True)
        for user in (self.alice, self.bob, charlie):
            SongReadiness.objects.create(
                group_song=three_quarters, user=user, is_ready=True)
        self.client.force_login(self.alice)
        self.select_group(self.client, self.primary)

        ascending = self.client.get(
            reverse('song_list'), {'sort': 'readiness', 'direction': 'asc'})
        descending = self.client.get(
            reverse('song_list'), {'sort': 'readiness', 'direction': 'desc'})

        self.assertEqual(
            [song['name'] for song in ascending.context['songs']],
            ['Quarter Ready', 'Three Quarters Ready', 'Ready Song', 'Retired Song'],
        )
        self.assertEqual(
            [song['name'] for song in descending.context['songs']],
            ['Ready Song', 'Retired Song', 'Three Quarters Ready', 'Quarter Ready'],
        )
        readiness = {
            song['name']: song['readiness_percent']
            for song in ascending.context['songs']
        }
        self.assertEqual(readiness, {
            'Quarter Ready': 25,
            'Three Quarters Ready': 75,
            'Ready Song': 100,
            'Retired Song': 100,
        })
        self.assertContains(ascending, '75% Ready')
        self.assertContains(ascending, 'On-Deck')
        self.assertContains(ascending, '4 tracks')

    @patch('player.views._song_duration_seconds')
    def test_setlist_card_shows_total_song_duration(self, duration_mock):
        duration_mock.side_effect = {'First Song': 185, 'Second Song': 265}.get
        setlist = Setlist.objects.create(
            group=self.primary, owner=self.alice, name='Timed Set')
        SetlistEntry.objects.create(
            setlist=setlist, song_name='First Song', position=0)
        SetlistEntry.objects.create(
            setlist=setlist, song_name='', position=1, is_break=True)
        SetlistEntry.objects.create(
            setlist=setlist, song_name='Second Song', position=2)
        self.client.force_login(self.alice)
        self.select_group(self.client, self.primary)

        response = self.client.get(reverse('setlist_list'))

        self.assertContains(response, '7:30 total')
        self.assertEqual(duration_mock.call_count, 2)

    def test_audio_duration_format_includes_hours_when_needed(self):
        from .views import _format_audio_duration

        self.assertEqual(_format_audio_duration(185), '3:05')
        self.assertEqual(_format_audio_duration(3725), '1:02:05')
        self.assertEqual(_format_audio_duration(None), '--:--')

    def test_setlist_owner_can_update_title_and_date_from_player(self):
        setlist = Setlist.objects.create(
            group=self.primary, owner=self.alice, name='Old Title')
        self.client.force_login(self.alice)
        self.select_group(self.client, self.primary)

        response = self.client.post(
            reverse('setlist_details_update', args=[setlist.pk]),
            {'name': 'New Title', 'date': '2026-11-15'},
        )

        self.assertRedirects(
            response, reverse('setlist_player', args=[setlist.pk]),
            fetch_redirect_response=False,
        )
        setlist.refresh_from_db()
        self.assertEqual(setlist.name, 'New Title')
        self.assertEqual(setlist.date.isoformat(), '2026-11-15')

    def test_admin_can_update_another_users_setlist_details(self):
        setlist = Setlist.objects.create(
            group=self.primary, owner=self.alice, name='Old Title')
        self.client.force_login(self.admin)
        self.select_group(self.client, self.primary)

        response = self.client.post(
            reverse('setlist_details_update', args=[setlist.pk]),
            {'name': 'Admin Title', 'date': ''},
        )

        self.assertEqual(response.status_code, 302)
        setlist.refresh_from_db()
        self.assertEqual(setlist.name, 'Admin Title')
        self.assertIsNone(setlist.date)

    def test_non_owner_cannot_update_setlist_details(self):
        setlist = Setlist.objects.create(
            group=self.primary, owner=self.alice, name='Alice Set')
        self.client.force_login(self.bob)
        self.select_group(self.client, self.primary)

        response = self.client.post(
            reverse('setlist_details_update', args=[setlist.pk]),
            {'name': 'Changed', 'date': '2026-11-15'},
        )

        self.assertEqual(response.status_code, 404)
        setlist.refresh_from_db()
        self.assertEqual(setlist.name, 'Alice Set')

    def test_all_members_activate_song_and_dismiss_celebration(self):
        group_song = GroupSong.objects.create(
            group=self.primary, song_name='New Song')
        alice_client = Client()
        alice_client.force_login(self.alice)
        self.select_group(alice_client, self.primary)
        bob_client = Client()
        bob_client.force_login(self.bob)
        self.select_group(bob_client, self.primary)

        alice_client.post(
            reverse('song_readiness_update', args=[group_song.song_name]),
            {'is_ready': '1'},
        )
        group_song.refresh_from_db()
        self.assertEqual(group_song.status, 'on_deck')

        bob_client.post(
            reverse('song_readiness_update', args=[group_song.song_name]),
            {'is_ready': '1'},
        )
        group_song.refresh_from_db()
        self.assertEqual(group_song.status, 'active')
        self.assertIsNotNone(group_song.ready_date)

        request = RequestFactory().get('/')
        request.user = self.alice
        request.active_group = self.primary
        self.assertEqual(group_context(request)['ready_celebrations'], [group_song])

        response = alice_client.post(
            reverse('celebration_dismiss', args=[group_song.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(SongCelebrationDismissal.objects.filter(
            group_song=group_song, user=self.alice).exists())
        self.assertEqual(group_context(request)['ready_celebrations'], [])

    def test_admin_can_set_each_members_readiness(self):
        group_song = GroupSong.objects.create(
            group=self.primary, song_name='Override Song')
        self.client.force_login(self.admin)
        self.select_group(self.client, self.primary)

        readiness_url = reverse(
            'song_readiness_update', args=[group_song.song_name])
        alice_response = self.client.post(
            readiness_url,
            {'user_id': self.alice.pk, 'is_ready': '1'},
        )

        self.assertEqual(alice_response.status_code, 302)
        self.assertTrue(SongReadiness.objects.get(
            group_song=group_song, user=self.alice).is_ready)
        group_song.refresh_from_db()
        self.assertEqual(group_song.status, 'on_deck')

        self.client.post(
            readiness_url,
            {'user_id': self.bob.pk, 'is_ready': '1'},
        )
        group_song.refresh_from_db()
        self.assertEqual(group_song.status, 'active')
        self.assertIsNotNone(group_song.ready_date)

        self.client.post(readiness_url, {'user_id': self.alice.pk})
        group_song.refresh_from_db()
        self.assertFalse(SongReadiness.objects.get(
            group_song=group_song, user=self.alice).is_ready)
        self.assertEqual(group_song.status, 'on_deck')

    def test_admin_cannot_directly_mark_song_ready(self):
        group_song = GroupSong.objects.create(
            group=self.primary, song_name='No Override Song')
        self.client.force_login(self.admin)
        self.select_group(self.client, self.primary)

        response = self.client.post(
            reverse('song_status_update', args=[group_song.song_name]),
            {'status': 'active'},
        )

        self.assertEqual(response.status_code, 404)
        group_song.refresh_from_db()
        self.assertEqual(group_song.status, 'on_deck')

    def test_removing_member_clears_stale_readiness(self):
        group_song = GroupSong.objects.create(
            group=self.primary, song_name='Membership Song')
        SongReadiness.objects.create(
            group_song=group_song, user=self.alice, is_ready=True)
        self.client.force_login(self.admin)
        self.select_group(self.client, self.primary)

        response = self.client.post(
            reverse('admin_group_edit', args=[self.primary.pk]),
            {
                'name': self.primary.name,
                'color_scheme': self.primary.color_scheme,
                'members': [str(self.bob.pk)],
            },
        )

        self.assertRedirects(
            response,
            f'{reverse("admin_home")}#groups',
            fetch_redirect_response=False,
        )
        self.assertFalse(GroupMembership.objects.filter(
            group=self.primary, user=self.alice).exists())
        self.assertFalse(SongReadiness.objects.filter(
            group_song=group_song, user=self.alice).exists())


class UserDisplayNameTests(TestCase):
    def test_display_name_falls_back_to_username(self):
        user = User.objects.create_user(username='casey', password='test')

        self.assertEqual(user_display_name(user), 'casey')

    def test_admin_display_name_has_admin_suffix(self):
        user = User.objects.create_user(
            username='admin-user', password='test', is_staff=True)
        UserProfile.objects.create(user=user, display_name='Jordan')

        self.assertEqual(user_display_name(user), 'Jordan (admin)')

    def test_admin_can_update_a_users_display_name(self):
        admin = User.objects.create_user(
            username='admin', password='test', is_staff=True)
        user = User.objects.create_user(username='casey', password='test')
        self.client.force_login(admin)

        response = self.client.post(
            reverse('admin_user_edit', args=[user.pk]),
            {
                'username': user.username,
                'display_name': 'Casey Jones',
                'password': '',
                'is_staff': '0',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(user.bandmate_profile.display_name, 'Casey Jones')


class CalendarSourcePermissionsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='admin', password='test', is_staff=True)
        self.member = User.objects.create_user(
            username='member', password='test')
        self.group = Group.objects.create(name='Band')
        GroupMembership.objects.create(group=self.group, user=self.member)

    def select_group(self, client, group):
        session = client.session
        session['active_group_id'] = group.pk
        session.save()

    def test_member_can_manage_group_calendar_sources(self):
        self.client.force_login(self.member)
        self.select_group(self.client, self.group)

        with patch('player.calendar_sync.sync_due_calendar_sources'):
            response = self.client.post(reverse('calendar_sources'), {
                'calendar_id': [''],
                'calendar_name': ['Rehearsals'],
                'calendar_url': ['https://example.com/rehearsals.ics'],
                'calendar_color': ['#22cc88'],
            })

        self.assertEqual(response.status_code, 200)
        source = CalendarSource.objects.get(group=self.group)
        self.assertEqual(source.name, 'Rehearsals')
        self.assertEqual(source.color, '#22cc88')

    def test_admin_without_membership_cannot_manage_calendar_sources(self):
        self.client.force_login(self.admin)
        self.select_group(self.client, self.group)

        response = self.client.get(reverse('calendar_sources'))

        self.assertEqual(response.status_code, 404)

    def test_site_settings_only_show_universal_integrations(self):
        self.client.force_login(self.admin)
        self.select_group(self.client, self.group)

        response = self.client.get(reverse('admin_settings'))

        self.assertContains(response, 'Song Lookup API')
        self.assertContains(response, 'Spotify Integration')
        self.assertNotContains(response, 'Calendar Sources')

    def test_admin_settings_home_orders_all_management_sections(self):
        self.client.force_login(self.admin)
        self.select_group(self.client, self.group)

        response = self.client.get(reverse('admin_home'))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(content.index('Site Settings'), content.index('Groups'))
        self.assertLess(content.index('Groups'), content.index('Users'))
        self.assertContains(response, '+ Group')
        self.assertContains(response, '+ User')
