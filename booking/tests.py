import fcntl
from io import StringIO
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from calendar_integrations.models import GroupCalendarRole
from player.models import Group, GroupAccessAudit, GroupMembership
from .models import BookingMembershipRole, BookingSettings


class BookingFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Group.objects.create(name='Band A')
        cls.b = Group.objects.create(name='Band B')
        cls.manager = User.objects.create_user('manager')
        cls.editor = User.objects.create_user('editor')
        cls.viewer = User.objects.create_user('viewer')
        cls.member = User.objects.create_user('member')
        cls.staff = User.objects.create_user('staff', is_staff=True)
        cls.shared = User.objects.create_user('shared')
        cls.memberships = {}
        for user in [cls.manager, cls.editor, cls.viewer, cls.member, cls.shared]:
            cls.memberships[user.pk] = GroupMembership.objects.create(user=user, group=cls.a)
        for user, role in [(cls.manager, 'booking_manager'), (cls.editor, 'booking_editor'),
                           (cls.viewer, 'booking_viewer'), (cls.shared, 'booking_manager')]:
            BookingMembershipRole.objects.create(membership=cls.memberships[user.pk], role=role)
        cls.other_membership = GroupMembership.objects.create(user=cls.shared, group=cls.b)
        BookingMembershipRole.objects.create(membership=cls.other_membership, role='booking_manager')
        BookingSettings.objects.create(group=cls.a, enabled=True)
        BookingSettings.objects.create(group=cls.b, search_center='Private B location')

    def login(self, user, group=None):
        self.client.force_login(user)
        session = self.client.session
        session['active_group_id'] = (group or self.a).pk
        session.save()

    def test_default_disabled_and_no_implicit_roles(self):
        self.assertFalse(BookingSettings.objects.get(group=self.b).enabled)
        self.assertFalse(BookingMembershipRole.objects.filter(membership__user=self.staff).exists())

    def test_every_booking_page_role_matrix(self):
        for user, expected in [(self.manager, [200, 200, 200]),
                               (self.editor, [200, 404, 404]),
                               (self.viewer, [200, 404, 404]),
                               (self.member, [404, 404, 404]),
                               (self.staff, [404, 404, 404])]:
            self.login(user)
            for route, status in zip(['overview', 'settings', 'roles'], expected):
                with self.subTest(user=user.username, route=route):
                    self.assertEqual(self.client.get(reverse('booking:' + route)).status_code, status)

    def test_sidebar_matches_role_and_toggle(self):
        for user, visible in [(self.manager, True), (self.member, False), (self.staff, False)]:
            self.login(user)
            response = self.client.get(reverse('calendar'))
            self.assertEqual(reverse('booking:overview') in response.content.decode(), visible)
        BookingSettings.objects.filter(group=self.a).update(enabled=False)
        self.login(self.manager)
        self.assertNotContains(self.client.get(reverse('calendar')), 'href="/booking/"')

    def test_disable_blocks_all_reads_and_posts_preserving_profile(self):
        self.login(self.manager)
        BookingSettings.objects.filter(group=self.a).update(enabled=False, search_center='Seattle')
        for route in ['overview', 'settings', 'roles']:
            for method in [self.client.get, self.client.post]:
                with self.subTest(route=route, method=method):
                    self.assertIn(method(reverse('booking:' + route)).status_code, [404, 405])
        self.assertEqual(BookingSettings.objects.get(group=self.a).search_center, 'Seattle')

    def test_switching_shared_user_to_disabled_band_blocks_access(self):
        self.login(self.shared)
        self.assertEqual(self.client.get(reverse('booking:overview')).status_code, 200)
        self.login(self.shared, self.b)
        self.assertEqual(self.client.get(reverse('booking:overview')).status_code, 404)

    def test_settings_ignore_posted_tenant_and_feature_toggle(self):
        self.login(self.manager)
        response = self.client.post(reverse('booking:settings'), {
            'group': self.b.pk, 'enabled': False, 'search_center': 'Portland',
            'genres': 'Rock', 'radius_miles': 90,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BookingSettings.objects.get(group=self.a).search_center, 'Portland')
        self.assertTrue(BookingSettings.objects.get(group=self.a).enabled)
        self.assertEqual(BookingSettings.objects.get(group=self.b).search_center, 'Private B location')
        self.assertTrue(GroupAccessAudit.objects.filter(group=self.a, action='booking.profile.updated').exists())

    def test_invalid_radius_does_not_save(self):
        self.login(self.manager)
        response = self.client.post(reverse('booking:settings'), {'radius_miles': 0})
        self.assertContains(response, 'Ensure this value is greater than or equal to 1.')
        self.assertEqual(BookingSettings.objects.get(group=self.a).radius_miles, 100)

    def test_role_tampering_and_editor_self_promotion_denied(self):
        self.login(self.manager)
        response = self.client.post(reverse('booking:roles'), {
            'membership': self.other_membership.pk, 'role': 'booking_viewer'})
        self.assertEqual(response.status_code, 404)
        self.login(self.editor)
        response = self.client.post(reverse('booking:roles'), {
            'membership': self.memberships[self.editor.pk].pk, 'role': 'booking_manager'})
        self.assertEqual(response.status_code, 404)

    def test_role_changes_immediately_apply_and_are_audited(self):
        self.login(self.manager)
        self.client.post(reverse('booking:roles'), {
            'membership': self.memberships[self.editor.pk].pk, 'role': 'booking_manager'})
        self.login(self.editor)
        self.assertEqual(self.client.get(reverse('booking:settings')).status_code, 200)
        BookingMembershipRole.objects.filter(membership__user=self.editor).update(role='booking_viewer')
        self.assertEqual(self.client.post(reverse('booking:settings'), {'radius_miles': 20}).status_code, 404)
        self.assertTrue(GroupAccessAudit.objects.filter(action='booking.bookingmembershiprole.role').exists())

    def test_last_manager_cannot_be_removed(self):
        self.login(self.shared, self.b)
        BookingSettings.objects.filter(group=self.b).update(enabled=True)
        response = self.client.post(reverse('booking:roles'), {
            'membership': self.other_membership.pk, 'role': ''})
        self.assertContains(response, 'Assign another manager')

    def test_membership_removal_cascades_both_roles_without_resurrection(self):
        membership = self.memberships[self.manager.pk]
        GroupCalendarRole.objects.create(membership=membership, role='calendar_manager')
        membership.delete()
        GroupMembership.objects.create(group=self.a, user=self.manager)
        self.assertFalse(BookingMembershipRole.objects.filter(membership__user=self.manager).exists())
        self.assertFalse(GroupCalendarRole.objects.filter(membership__user=self.manager).exists())
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse('booking:overview')).status_code, 404)

    def test_invalid_role_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            BookingMembershipRole.objects.create(membership=self.memberships[self.member.pk], role='owner')

    def test_admin_membership_removal_revokes_and_audits_feature_roles(self):
        self.login(self.staff)
        GroupCalendarRole.objects.create(membership=self.memberships[self.manager.pk], role='calendar_manager')
        response = self.client.post(reverse('admin_group_edit', args=[self.a.pk]), {
            'name': self.a.name, 'color_scheme': 'blue', 'members': [self.member.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(BookingMembershipRole.objects.filter(membership__group=self.a).exists())
        self.assertFalse(GroupCalendarRole.objects.filter(membership__group=self.a).exists())
        self.assertTrue(GroupAccessAudit.objects.filter(
            action='membership.removed', target=f'user:{self.manager.pk}', actor=self.staff).exists())

    def test_inactive_manager_does_not_count_as_a_replacement(self):
        User.objects.filter(pk=self.shared.pk).update(is_active=False)
        self.login(self.manager)
        response = self.client.post(reverse('booking:roles'), {
            'membership': self.memberships[self.manager.pk].pk, 'role': ''})
        self.assertContains(response, 'Assign another manager')

    def test_staff_bootstrap_requires_member_and_does_not_grant_staff_access(self):
        self.login(self.staff, self.b)
        BookingMembershipRole.objects.filter(membership=self.other_membership).delete()
        url = reverse('admin_group_features', args=[self.b.pk])
        response = self.client.post(url, {'booking_enabled': 'on',
                                         'booking_manager': self.memberships[self.manager.pk].pk})
        self.assertContains(response, 'Select a valid choice')
        self.assertFalse(BookingSettings.objects.get(group=self.b).enabled)
        response = self.client.post(url, {'booking_enabled': 'on',
                                         'booking_manager': self.other_membership.pk,
                                         'calendar_manager': self.other_membership.pk})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(BookingSettings.objects.get(group=self.b).enabled)
        self.assertEqual(self.client.get(reverse('booking:overview')).status_code, 404)
        self.assertEqual(GroupCalendarRole.objects.get(membership=self.other_membership).role, 'calendar_manager')
        self.assertEqual(GroupAccessAudit.objects.filter(group=self.b).count(), 3)

    def test_staff_cannot_replace_existing_manager_from_feature_form(self):
        self.login(self.staff)
        self.client.post(reverse('admin_group_features', args=[self.a.pk]), {
            'booking_enabled': 'on', 'booking_manager': self.memberships[self.member.pk].pk})
        self.assertFalse(BookingMembershipRole.objects.filter(membership__user=self.member).exists())

    def test_feature_controls_staff_only(self):
        self.login(self.manager)
        url = reverse('admin_group_features', args=[self.a.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {'booking_enabled': 'on'}).status_code, 404)

    def test_anonymous_and_csrf_protection(self):
        self.assertEqual(self.client.get(reverse('booking:overview')).status_code, 302)
        self.client = Client(enforce_csrf_checks=True)
        self.login(self.manager)
        self.assertEqual(self.client.post(reverse('booking:settings'), {'radius_miles': 1}).status_code, 403)
        self.assertEqual(self.client.post(reverse('booking:roles')).status_code, 403)

    def test_calendar_manager_does_not_grant_booking(self):
        GroupCalendarRole.objects.create(membership=self.memberships[self.member.pk], role='calendar_manager')
        self.login(self.member)
        self.assertEqual(self.client.get(reverse('booking:overview')).status_code, 404)

    def test_background_stubs_never_call_network(self):
        with tempfile.TemporaryDirectory() as directory, patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
            for command in ['booking_check', 'booking_enqueue_due']:
                output = StringIO()
                call_command(command, stdout=output)
                self.assertTrue(output.getvalue())
            call_command('sync_google_calendars', lock_file=directory + '/google.lock', stdout=StringIO())
            call_command('booking_worker', once=True, lock_file=directory + '/worker.lock', stdout=StringIO())

    def test_worker_singleton(self):
        with tempfile.NamedTemporaryFile() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(CommandError):
                call_command('booking_worker', once=True, lock_file=lock.name, stdout=StringIO())
