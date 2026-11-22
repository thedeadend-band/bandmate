from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from booking.models import BookingMembershipRole, BookingSettings
from player.models import CalendarSource, Group, GroupAccessAudit, GroupMembership
from .models import BandmateLocalCalendar, CalendarGoogleAccount, ConnectedCalendar, GroupCalendarRole
from .permissions import GOOGLE_EVENT_WRITE_SCOPE, source_write_allowed


class CalendarFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Group.objects.create(name='Band A')
        cls.b = Group.objects.create(name='Band B')
        cls.manager = User.objects.create_user('manager')
        cls.editor = User.objects.create_user('editor')
        cls.member = User.objects.create_user('member')
        cls.staff = User.objects.create_user('staff', is_staff=True)
        cls.booker = User.objects.create_user('booker')
        for user in [cls.manager, cls.editor, cls.member, cls.booker]:
            GroupMembership.objects.create(group=cls.a, user=user)
        GroupMembership.objects.create(group=cls.b, user=cls.manager)
        for user, role in [(cls.manager, 'calendar_manager'), (cls.editor, 'calendar_editor')]:
            GroupCalendarRole.objects.create(
                membership=GroupMembership.objects.get(group=cls.a, user=user), role=role)
        BookingMembershipRole.objects.create(
            membership=GroupMembership.objects.get(group=cls.a, user=cls.booker), role='booking_manager')
        cls.local = BandmateLocalCalendar.objects.create(group=cls.a, name='Rehearsal', write_enabled=True)
        cls.other = BandmateLocalCalendar.objects.create(group=cls.b, name='B private calendar', write_enabled=True)
        cls.account = CalendarGoogleAccount.objects.create(
            group=cls.a, email='a@example.com', is_connected=True, granted_scopes=[GOOGLE_EVENT_WRITE_SCOPE])
        cls.google = ConnectedCalendar.objects.create(account=cls.account, name='Google editable',
            external_calendar_id='a', access_role='writer', write_enabled=True)
        cls.readonly = ConnectedCalendar.objects.create(account=cls.account, name='Google read only',
            external_calendar_id='a-readonly', access_role='owner')
        other_account = CalendarGoogleAccount.objects.create(
            group=cls.b, email='b@example.com', is_connected=True, granted_scopes=[GOOGLE_EVENT_WRITE_SCOPE])
        cls.other_google = ConnectedCalendar.objects.create(account=other_account, name='B google',
            external_calendar_id='b', access_role='owner', write_enabled=True)
        cls.ics = CalendarSource.objects.create(group=cls.a, name='ICS', ics_url='https://example.com/calendar.ics')

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session['active_group_id'] = self.a.pk
        session.save()

    def toggle(self, source, value, kind='local'):
        return self.client.post(reverse('calendar_integrations:write_mode', args=[kind, source.pk]),
                                {'write_enabled': value})

    def test_calendar_setup_and_policy_work_when_booking_disabled(self):
        BookingSettings.objects.create(group=self.a, enabled=False)
        self.login(self.manager)
        self.assertEqual(self.client.get(reverse('calendar_integrations:sources')).status_code, 200)
        self.assertTrue(source_write_allowed(self.manager, self.a, self.local))
        self.assertEqual(self.toggle(self.local, 'false').status_code, 302)
        self.assertFalse(source_write_allowed(self.manager, self.a, self.local))

    def test_local_and_google_sources_default_view_only(self):
        local = BandmateLocalCalendar.objects.create(group=self.a, name='New')
        self.assertFalse(local.write_enabled)
        self.assertFalse(self.readonly.write_enabled)
        self.assertFalse(CalendarGoogleAccount(group=self.a, email='new@example.com').is_connected)

    def test_write_policy_matrix_including_staff_booking_only_and_ics(self):
        for user in [self.manager, self.editor, self.member, self.staff, self.booker]:
            for source in [self.local, self.google, self.readonly, self.other, self.other_google, self.ics]:
                expected = user in [self.manager, self.editor] and source in [self.local, self.google]
                with self.subTest(user=user.username, source=source):
                    self.assertEqual(source_write_allowed(user, self.a, source), expected)

    def test_sources_and_roles_are_manager_only(self):
        for user in [self.editor, self.member, self.staff, self.booker]:
            self.login(user)
            for route in ['sources', 'roles']:
                with self.subTest(user=user.username, route=route):
                    self.assertEqual(self.client.get(reverse('calendar_integrations:' + route)).status_code, 404)
                    self.assertEqual(self.client.post(reverse('calendar_integrations:' + route)).status_code, 404)
            self.assertEqual(self.toggle(self.local, 'false').status_code, 404)

    def test_source_id_tampering_and_ics_blocked(self):
        self.login(self.manager)
        self.assertEqual(self.toggle(self.other, 'false').status_code, 404)
        self.assertEqual(self.toggle(self.other_google, 'false', kind='google').status_code, 404)
        self.assertEqual(self.toggle(self.ics, 'true', kind='ics').status_code, 404)
        self.other.refresh_from_db()
        self.assertTrue(self.other.write_enabled)

    def test_stale_source_instances_recheck_toggle_scope_acl_and_connection(self):
        for model, pk, change, restore in [
                (ConnectedCalendar, self.google.pk, {'write_enabled': False}, {'write_enabled': True}),
                (ConnectedCalendar, self.google.pk, {'is_enabled': False}, {'is_enabled': True}),
                (ConnectedCalendar, self.google.pk, {'access_role': 'reader'}, {'access_role': 'writer'}),
                (CalendarGoogleAccount, self.account.pk, {'granted_scopes': []}, {'granted_scopes': [GOOGLE_EVENT_WRITE_SCOPE]}),
                (CalendarGoogleAccount, self.account.pk, {'is_connected': False}, {'is_connected': True})]:
            with self.subTest(change=change):
                model.objects.filter(pk=pk).update(**change)
                self.assertFalse(source_write_allowed(self.manager, self.a, self.google))
                model.objects.filter(pk=pk).update(**restore)
                self.assertTrue(source_write_allowed(self.manager, self.a, self.google))

    def test_private_event_restriction_and_unknown_access_fail_closed(self):
        ConnectedCalendar.objects.filter(pk=self.google.pk).update(access_role='writerWithoutPrivateAccess')
        self.assertTrue(source_write_allowed(self.manager, self.a, self.google))
        self.assertFalse(source_write_allowed(self.manager, self.a, self.google, private=True))
        ConnectedCalendar.objects.filter(pk=self.google.pk).update(access_role='unknown')
        self.assertFalse(source_write_allowed(self.manager, self.a, self.google))

    def test_revoked_role_blocks_stale_toggle_and_write_policy(self):
        self.login(self.manager)
        GroupCalendarRole.objects.filter(membership__user=self.manager).update(role='calendar_viewer')
        self.assertFalse(source_write_allowed(self.manager, self.a, self.local))
        self.assertEqual(self.toggle(self.local, 'false').status_code, 404)

    def test_google_enable_fails_closed_until_live_consent_available(self):
        self.login(self.manager)
        self.assertEqual(self.toggle(self.readonly, 'true', kind='google').status_code, 400)
        self.readonly.refresh_from_db()
        self.assertFalse(self.readonly.write_enabled)
        self.assertTrue(GroupAccessAudit.objects.filter(action='calendar.google.write_enable_blocked').exists())
        self.assertEqual(self.toggle(self.google, 'false', kind='google').status_code, 302)
        self.assertFalse(source_write_allowed(self.manager, self.a, self.google))

    def test_explicit_boolean_and_post_required_for_toggle(self):
        self.login(self.manager)
        url = reverse('calendar_integrations:write_mode', args=['local', self.local.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.toggle(self.local, 'yes').status_code, 400)

    def test_local_creation_ignores_tenant_input_and_validates_timezone(self):
        self.login(self.manager)
        data = {'name': 'Tour', 'color': '#abcdef', 'timezone': 'America/Los_Angeles', 'group': self.b.pk}
        response = self.client.post(reverse('calendar_integrations:sources'), data)
        self.assertEqual(response.status_code, 302)
        created = BandmateLocalCalendar.objects.get(name='Tour')
        self.assertEqual(created.group_id, self.a.pk)
        self.assertFalse(created.write_enabled)
        data.update(name='Invalid', timezone='not/a/zone')
        self.assertContains(self.client.post(reverse('calendar_integrations:sources'), data), 'IANA timezone')
        self.assertFalse(BandmateLocalCalendar.objects.filter(name='Invalid').exists())

    def test_names_autoescaped_and_other_group_sources_hidden(self):
        BandmateLocalCalendar.objects.filter(pk=self.local.pk).update(name='<script>alert(1)</script>')
        self.login(self.manager)
        response = self.client.get(reverse('calendar_integrations:sources'))
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, 'B private calendar')

    def test_calendar_role_change_scoped_and_audited(self):
        self.login(self.manager)
        member = GroupMembership.objects.get(group=self.a, user=self.member)
        url = reverse('calendar_integrations:roles')
        self.assertEqual(self.client.post(url, {'membership': member.pk, 'role': 'calendar_editor'}).status_code, 302)
        self.assertTrue(source_write_allowed(self.member, self.a, self.local))
        other = GroupMembership.objects.get(group=self.b, user=self.manager)
        self.assertEqual(self.client.post(url, {'membership': other.pk, 'role': 'calendar_editor'}).status_code, 404)
        self.assertTrue(GroupAccessAudit.objects.filter(action='calendar_integrations.groupcalendarrole.role').exists())

    def test_calendar_last_manager_and_invalid_role(self):
        self.login(self.manager)
        own = GroupMembership.objects.get(group=self.a, user=self.manager)
        url = reverse('calendar_integrations:roles')
        self.assertContains(self.client.post(url, {'membership': own.pk, 'role': ''}), 'Assign another manager')
        self.assertContains(self.client.post(url, {'membership': own.pk, 'role': 'owner'}), 'Choose a valid role')

    def test_unique_provider_identity_and_role_constraints(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConnectedCalendar.objects.create(account=self.account, external_calendar_id='a', name='Duplicate')
        with self.assertRaises(IntegrityError), transaction.atomic():
            CalendarGoogleAccount.objects.create(group=self.a, email='a@example.com')
        with self.assertRaises(IntegrityError), transaction.atomic():
            GroupCalendarRole.objects.create(membership=GroupMembership.objects.get(group=self.a, user=self.member), role='invalid')

    def test_invalid_color_model_validation(self):
        self.local.color = '<html>'
        with self.assertRaises(ValidationError):
            self.local.full_clean()

    def test_new_mutations_have_csrf_protection(self):
        self.client = Client(enforce_csrf_checks=True)
        self.login(self.manager)
        self.assertEqual(self.toggle(self.local, 'false').status_code, 403)
        self.assertEqual(self.client.post(reverse('calendar_integrations:sources')).status_code, 403)
        self.assertEqual(self.client.post(reverse('calendar_integrations:roles')).status_code, 403)
