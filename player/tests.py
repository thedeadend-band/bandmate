from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from .context_processors import group_context
from .models import (
    CalendarSource, Group, GroupMembership, GroupSong, Setlist, SetlistEntry,
    SongCelebrationDismissal, SongReadiness, UserProfile,
)
from .user_display import user_display_name


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
