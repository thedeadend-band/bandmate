import uuid
from datetime import datetime, timezone
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from booking.models import BookingSettings, BookingMembershipRole
from player.models import Group, GroupMembership, CalendarSource, CalendarEvent
from .models import GroupCalendarRole, BandmateLocalCalendar, LocalEvent, CalendarWriteOperation, CalendarMutationAudit
from .forms import EventForm


class LocalEventTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name='Local band')
        cls.other = Group.objects.create(name='Other band')
        cls.manager = User.objects.create_user('local-manager')
        cls.viewer = User.objects.create_user('local-viewer')
        cls.staff = User.objects.create_user('local-staff', is_staff=True)
        m = GroupMembership.objects.create(group=cls.group, user=cls.manager)
        GroupMembership.objects.create(group=cls.other, user=cls.manager)
        GroupMembership.objects.create(group=cls.group, user=cls.viewer)
        GroupCalendarRole.objects.create(membership=m, role='calendar_manager')
        BookingSettings.objects.create(group=cls.group, enabled=False)
        cls.calendar = BandmateLocalCalendar.objects.create(group=cls.group, name='Rehearsals', write_enabled=True, timezone='America/Los_Angeles')
        cls.other_calendar = BandmateLocalCalendar.objects.create(group=cls.other, name='Private other', write_enabled=True)
        cls.event = LocalEvent.objects.create(calendar=cls.calendar, title='Practice',
            starts_at=datetime(2027, 1, 5, 18, tzinfo=timezone.utc), ends_at=datetime(2027, 1, 5, 20, tzinfo=timezone.utc))

    def setUp(self):
        self.login(self.manager)

    def login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session['active_group_id'] = self.group.pk
        session.save()

    def payload(self, **extra):
        return dict({'title': 'New practice', 'timezone': 'America/Los_Angeles', 'start': '2027-01-05T18:00',
            'end': '2027-01-05T20:00', 'location': 'Studio', 'description': 'Bring charts',
            'operation_key': str(uuid.uuid4()), 'target': f'local:{self.calendar.pk}'}, **extra)

    def test_booking_disabled_create_and_duplicate_submit(self):
        data = self.payload()
        with patch('socket.socket.connect', side_effect=AssertionError('No network')):
            for _ in range(2):
                response = self.client.post(reverse('calendar_events:create'), data)
                self.assertEqual(response.status_code, 302)
        self.assertEqual(LocalEvent.objects.filter(title='New practice').count(), 1)
        event = LocalEvent.objects.get(title='New practice')
        self.assertEqual(event.starts_at.hour, 2)  # 18:00 Pacific => next day UTC
        self.assertEqual(CalendarWriteOperation.objects.get().status, 'succeeded')
        self.assertEqual(CalendarMutationAudit.objects.get().outcome, 'succeeded')

    def test_key_reuse_for_changed_content_fails(self):
        data = self.payload()
        self.client.post(reverse('calendar_events:create'), data)
        data['title'] = 'Different'
        response = self.client.post(reverse('calendar_events:create'), data)
        self.assertContains(response, 'already used')
        self.assertFalse(LocalEvent.objects.filter(title='Different').exists())

    def test_stale_edit_conflicts_and_does_not_overwrite(self):
        url = reverse('calendar_events:edit', args=['local', self.event.pk])
        response = self.client.post(url, self.payload(version='1'))
        self.assertEqual(response.status_code, 302)
        response = self.client.post(url, self.payload(version='1', title='Stale overwrite'))
        self.assertEqual(response.status_code, 409)
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'New practice')
        self.assertEqual(self.event.version, 2)

    def test_delete_get_is_confirmation_and_post_is_versioned(self):
        url = reverse('calendar_events:delete', args=['local', self.event.pk])
        self.assertContains(self.client.get(url), 'Confirm deletion')
        self.assertFalse(LocalEvent.objects.get(pk=self.event.pk).is_deleted)
        response = self.client.post(url, {'version': '0', 'confirm': 'yes', 'operation_key': uuid.uuid4()})
        self.assertEqual(response.status_code, 409)
        response = self.client.post(url, {'version': '1', 'confirm': 'yes', 'operation_key': uuid.uuid4()})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(LocalEvent.objects.get(pk=self.event.pk).is_deleted)

    def test_source_role_and_cross_band_tampering(self):
        data = self.payload(target=f'local:{self.other_calendar.pk}')
        self.assertEqual(self.client.post(reverse('calendar_events:create'), data).status_code, 404)
        for user in [self.viewer, self.staff]:
            self.login(user)
            self.assertEqual(self.client.post(reverse('calendar_events:create'), self.payload()).status_code, 404)
            self.assertEqual(self.client.post(reverse('calendar_events:edit', args=['local', self.event.pk]), self.payload(version='1')).status_code, 404)
        self.login(self.manager)
        BandmateLocalCalendar.objects.filter(pk=self.calendar.pk).update(write_enabled=False)
        self.assertEqual(self.client.post(reverse('calendar_events:edit', args=['local', self.event.pk]), self.payload(version='1')).status_code, 404)

    def test_read_only_source_details_are_visible_to_members(self):
        self.login(self.viewer)
        response = self.client.get(reverse('calendar_events:detail', args=['local', self.event.pk]))
        self.assertContains(response, 'Practice')
        self.assertNotContains(response, '>Edit</a>')

    def test_combined_calendar_has_stable_ids_and_does_no_sync(self):
        source = CalendarSource.objects.create(group=self.group, name='ICS', ics_url='https://example.com/a.ics')
        CalendarEvent.objects.create(source=source, uid='a', title='Imported', starts_at=self.event.starts_at, ends_at=self.event.ends_at)
        with patch('player.calendar_sync.sync_due_calendar_sources') as sync:
            response = self.client.get(reverse('calendar'))
        sync.assert_not_called()
        self.assertContains(response, f'local:{self.calendar.pk}')
        self.assertContains(response, f'ics:{source.pk}')
        self.assertContains(response, 'New event')
        self.assertNotContains(response, 'Private other')

    def test_ics_edit_and_delete_cannot_be_forged(self):
        source = CalendarSource.objects.create(group=self.group, name='ICS', ics_url='https://example.com/a.ics')
        event = CalendarEvent.objects.create(source=source, uid='a', title='Imported', starts_at=self.event.starts_at, ends_at=self.event.ends_at)
        for action in ['edit', 'delete']:
            self.assertEqual(self.client.post(reverse('calendar_events:' + action, args=['ics', event.pk]), self.payload()).status_code, 404)
        self.assertTrue(CalendarEvent.objects.filter(pk=event.pk).exists())

    def test_dst_missing_and_ambiguous_times_rejected(self):
        for start, end in [('2027-03-14T02:30', '2027-03-14T04:00'), ('2027-11-07T01:30', '2027-11-07T03:00')]:
            form = EventForm(self.payload(start=start, end=end))
            self.assertFalse(form.is_valid())
            self.assertIn('daylight saving', str(form.errors))

    def test_all_day_dates_survive_dst_with_exclusive_end(self):
        data = self.payload(is_all_day='on', start_date='2027-03-14', end_date='2027-03-15')
        self.assertEqual(self.client.post(reverse('calendar_events:create'), data).status_code, 302)
        event = LocalEvent.objects.get(title='New practice')
        self.assertTrue(event.is_all_day)
        self.assertEqual((event.ends_at - event.starts_at).total_seconds(), 23 * 3600)

    def test_invalid_dates_and_timezone_leave_no_record(self):
        for changes in [{'timezone': 'Invalid/Zone'}, {'end': '2027-01-05T17:00'}, {'start': 'nonsense'}]:
            self.assertEqual(self.client.post(reverse('calendar_events:create'), self.payload(**changes)).status_code, 200)
        self.assertEqual(LocalEvent.objects.count(), 1)

    def test_event_forms_render_and_csrf_is_required(self):
        self.assertContains(self.client.get(reverse('calendar_events:create')), 'Rehearsals')
        self.assertContains(self.client.get(reverse('calendar_events:edit', args=['local', self.event.pk])), 'Practice')
        self.client = Client(enforce_csrf_checks=True)
        self.login(self.manager)
        self.assertEqual(self.client.post(reverse('calendar_events:create'), self.payload()).status_code, 403)

    def test_edit_times_render_without_unsupported_subsecond_precision(self):
        self.event.starts_at = self.event.starts_at.replace(second=37, microsecond=123456)
        self.event.save(update_fields=['starts_at'])
        response = self.client.get(reverse('calendar_events:edit', args=['local', self.event.pk]))
        initial = response.context['form'].initial['start']
        self.assertTrue(initial.endswith(':37'))
        self.assertNotIn('.123456', initial)
