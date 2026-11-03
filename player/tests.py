from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from .context_processors import group_context
from .models import (
    Group, GroupMembership, GroupSong, Setlist, SongCelebrationDismissal,
    SongReadiness,
)


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

    def test_admin_can_override_readiness(self):
        group_song = GroupSong.objects.create(
            group=self.primary, song_name='Override Song')
        self.client.force_login(self.admin)
        self.select_group(self.client, self.primary)

        response = self.client.post(
            reverse('song_status_update', args=[group_song.song_name]),
            {'status': 'active'},
        )

        self.assertEqual(response.status_code, 302)
        group_song.refresh_from_db()
        self.assertEqual(group_song.status, 'active')
        self.assertIsNotNone(group_song.ready_date)

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

        self.assertRedirects(response, reverse('admin_groups'))
        self.assertFalse(GroupMembership.objects.filter(
            group=self.primary, user=self.alice).exists())
        self.assertFalse(SongReadiness.objects.filter(
            group_song=group_song, user=self.alice).exists())
