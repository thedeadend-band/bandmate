import copy
import logging
import tempfile
import uuid
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlparse, parse_qs
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from bandmate.credential_vault import seal, unseal, rotate, VaultUnavailable
from bandmate.log_filters import OAuthQueryRedactor
from player.models import Group, GroupMembership
from .models import CalendarGoogleAccount, ConnectedCalendar, GoogleEvent, GroupCalendarRole, GoogleOAuthState, CalendarWriteOperation
from .permissions import GOOGLE_READ_SCOPES, GOOGLE_EVENT_WRITE_SCOPE
from .services.google import ProviderError, GoogleCalendarClient
from .services.sync import sync_calendar, event_values, cache_event
from .services.mutations import submit
from .services.google_mutations import process_operation


class GoogleCalendarTests(TestCase):
    def test_acl_downgrade_redacts_old_cache_even_when_sync_fails(self):
        cache_event(self.calendar, self.event())
        self.provider.calendar.return_value = {'accessRole': 'freeBusyReader'}
        self.provider.events.side_effect = ProviderError('down', 503)
        with self.assertRaises(ProviderError):
            sync_calendar(self.calendar, self.provider)
        event = GoogleEvent.objects.get(calendar=self.calendar)
        self.assertEqual((event.title, event.description, event.location), ('Busy', '', ''))
        self.assertFalse(event.mutable)
        self.assertEqual(self.provider.events.call_args.kwargs['sync_token'], '')

    def test_pending_oauth_cannot_undo_disconnect(self):
        params = self.start_oauth()
        self.client.post(reverse('calendar_integrations:google_disconnect', args=[self.account.pk]))
        with patch('calendar_integrations.oauth_views.exchange_code', return_value={'access_token': 'access',
                'refresh_token': 'refresh', 'scope': ' '.join(GOOGLE_READ_SCOPES)}), patch(
                'calendar_integrations.oauth_views.identity', return_value={'email': self.account.email, 'email_verified': True}):
            response = self.client.get(reverse('calendar_integrations:google_callback'), {'state': params['state'][0], 'code': 'code'})
        self.assertEqual(response.status_code, 400)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_connected)

    def test_malformed_event_preserves_cache_and_cursor(self):
        cache_event(self.calendar, self.event())
        self.provider.events.return_value = {'items': [None], 'nextSyncToken': 'bad'}
        with self.assertRaises(ProviderError):
            sync_calendar(self.calendar, self.provider)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.sync_token, 'previous')
        self.assertEqual(GoogleEvent.objects.filter(is_deleted=False).count(), 1)

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name='Google band')
        cls.other = Group.objects.create(name='Other Google band')
        cls.user = User.objects.create_user('google-manager')
        membership = GroupMembership.objects.create(user=cls.user, group=cls.group)
        GroupCalendarRole.objects.create(membership=membership, role='calendar_manager')
        cls.other_membership = GroupMembership.objects.create(user=cls.user, group=cls.other)
        GroupCalendarRole.objects.create(membership=cls.other_membership, role='calendar_manager')
        cls.account = CalendarGoogleAccount.objects.create(group=cls.group, email='band@example.com',
            is_connected=True, granted_scopes=[*GOOGLE_READ_SCOPES, GOOGLE_EVENT_WRITE_SCOPE])
        cls.calendar = ConnectedCalendar.objects.create(account=cls.account, name='Google', external_calendar_id='shared@example.com',
            access_role='writer', write_enabled=True, sync_token='previous', last_full_sync_at=timezone.now())
        cls.other_account = CalendarGoogleAccount.objects.create(group=cls.other, email='other@example.com', is_connected=True)
        cls.other_calendar = ConnectedCalendar.objects.create(account=cls.other_account, name='Other', external_calendar_id='other')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key_file = Path(self.temp.name) / 'keys'
        self.key_file.write_bytes(Fernet.generate_key() + b'\n')
        self.key_file.chmod(0o600)
        self.config = override_settings(BANDMATE_SECRET_KEY_FILE=str(self.key_file), GOOGLE_CALENDAR_CLIENT_ID='client',
            GOOGLE_CALENDAR_CLIENT_SECRET='client-secret', GOOGLE_CALENDAR_REDIRECT_URI='https://bandmate.example/calendar/manage/google/callback/')
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.account.refresh_ciphertext = seal(self.group.pk, 'google-calendar-refresh', 'refresh-a')
        self.account.save()
        self.client.force_login(self.user)
        session = self.client.session
        session['active_group_id'] = self.group.pk
        session.save()
        self.provider = Mock()
        self.provider.calendar.return_value = {'id': self.calendar.external_calendar_id, 'accessRole': 'writer'}

    def event(self, **changes):
        return {**{'id': 'existing', 'etag': 'version-1', 'summary': 'External rehearsal',
            'start': {'dateTime': '2027-01-05T18:00:00-08:00', 'timeZone': 'America/Los_Angeles'},
            'end': {'dateTime': '2027-01-05T20:00:00-08:00', 'timeZone': 'America/Los_Angeles'},
            'description': 'Notes', 'location': 'Studio'}, **changes}

    def payload(self):
        return {'title': 'Reviewed rehearsal', 'starts_at': '2027-01-06T02:00:00+00:00', 'ends_at': '2027-01-06T04:00:00+00:00',
            'is_all_day': False, 'timezone': 'America/Los_Angeles', 'location': 'Studio', 'description': 'Approved notes'}

    def operation(self, action='create'):
        event = None
        if action != 'create':
            cache_event(self.calendar, self.event())
            event = GoogleEvent.objects.get(calendar=self.calendar)
        return submit(self.user, self.group, self.calendar, action, uuid.uuid4(),
                      self.payload() if action != 'delete' else {}, event, 'version-1' if event else '')

    def test_vault_tenant_purpose_rotation_and_permissions(self):
        ciphertext = seal(self.group.pk, 'google-calendar-refresh', 'private-token')
        self.assertNotIn('private-token', ciphertext)
        self.assertEqual(unseal(self.group.pk, 'google-calendar-refresh', ciphertext), 'private-token')
        for group, purpose in [(self.other.pk, 'google-calendar-refresh'), (self.group.pk, 'gmail')]:
            with self.assertRaises(VaultUnavailable):
                unseal(group, purpose, ciphertext)
        old = self.key_file.read_bytes()
        self.key_file.write_bytes(Fernet.generate_key() + b'\n' + old)
        rotated = rotate(ciphertext)
        self.key_file.write_bytes(self.key_file.read_bytes().splitlines()[0] + b'\n')
        self.assertEqual(unseal(self.group.pk, 'google-calendar-refresh', rotated), 'private-token')
        self.key_file.chmod(0o644)
        with self.assertRaises(VaultUnavailable):
            seal(self.group.pk, 'test', 'secret')

    def test_incremental_pagination_and_tombstones(self):
        cache_event(self.calendar, self.event())
        self.provider.events.side_effect = [{'items': [self.event(id='new')], 'nextPageToken': 'page-2'},
            {'items': [{'id': 'existing', 'status': 'cancelled'}], 'nextSyncToken': 'next'}]
        sync_calendar(self.calendar, self.provider)
        self.assertTrue(GoogleEvent.objects.get(external_id='existing').is_deleted)
        self.assertEqual(GoogleEvent.objects.filter(is_deleted=False).count(), 1)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.sync_token, 'next')
        self.assertEqual(self.provider.events.call_args_list[1].kwargs['page_token'], 'page-2')
        self.assertEqual(self.provider.events.call_args_list[0].kwargs['sync_token'], 'previous')

    def test_410_resets_only_selected_calendar(self):
        cache_event(self.calendar, self.event())
        cache_event(self.other_calendar, self.event(id='other-event'))
        self.provider.events.side_effect = [ProviderError('expired', 410), {'items': [self.event(id='replacement')], 'nextSyncToken': 'reset'}]
        sync_calendar(self.calendar, self.provider)
        self.assertTrue(GoogleEvent.objects.get(calendar=self.calendar, external_id='existing').is_deleted)
        self.assertFalse(GoogleEvent.objects.get(calendar=self.other_calendar).is_deleted)
        self.assertEqual(self.provider.events.call_args_list[1].kwargs['sync_token'], '')

    def test_partial_sync_and_page_limit_preserve_cache_and_cursor(self):
        cache_event(self.calendar, self.event())
        self.provider.events.side_effect = [{'items': [self.event(id='new')], 'nextPageToken': 'p'}, ProviderError('down', 503)]
        with self.assertRaises(ProviderError):
            sync_calendar(self.calendar, self.provider)
        self.assertEqual(GoogleEvent.objects.count(), 1)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.sync_token, 'previous')

    def test_private_and_freebusy_details_never_cached(self):
        private = self.event(visibility='private', summary='PRIVATE', description='SENSITIVE')
        cache_event(self.calendar, private)
        event = GoogleEvent.objects.get(calendar=self.calendar)
        self.assertEqual(event.title, 'Busy')
        self.assertEqual(event.description, '')
        self.assertFalse(event.mutable)
        self.calendar.access_role = 'freeBusyReader'
        self.assertEqual(event_values(self.event(), self.calendar)['title'], 'Busy')

    def test_recurring_attendee_and_special_events_read_only(self):
        for changes in [{'recurringEventId': 'series'}, {'recurrence': ['RRULE:FREQ=WEEKLY']},
                        {'attendees': [{'email': 'person@example.com'}]}, {'eventType': 'outOfOffice'}]:
            self.assertFalse(event_values(self.event(**changes), self.calendar)['mutable'])

    def test_create_is_queued_and_provider_confirmation_required(self):
        op = self.operation()
        self.assertEqual(op.status, 'pending')
        self.assertFalse(GoogleEvent.objects.exists())
        self.provider.event.side_effect = ProviderError('not_found', 404)
        self.provider.write.return_value = self.event(id=op.event_id)
        process_operation(op, self.provider)
        op.refresh_from_db()
        self.assertEqual(op.status, 'succeeded')
        args = self.provider.write.call_args
        self.assertEqual(args.args[3]['id'], op.event_id)
        self.assertIn('bandmateOperation', args.args[3]['extendedProperties']['private'])

    def test_existing_event_patch_uses_etag_and_preserves_provider_properties(self):
        op = self.operation('update')
        self.provider.event.return_value = self.event(extendedProperties={'private': {'otherApp': 'keep'}})
        self.provider.write.return_value = self.event(etag='version-2')
        process_operation(op, self.provider)
        self.assertEqual(self.provider.write.call_args.kwargs['etag'], 'version-1')
        self.assertEqual(self.provider.write.call_args.args[3]['extendedProperties']['private']['otherApp'], 'keep')
        op.refresh_from_db()
        self.assertEqual(op.status, 'succeeded')

    def test_stale_etag_and_provider_412_conflict_without_overwrite(self):
        for etag, error in [('changed', None), ('version-1', ProviderError('conflict', 412))]:
            op = self.operation('update')
            self.provider.reset_mock()
            self.provider.event.side_effect = None
            self.provider.event.return_value = self.event(etag=etag)
            self.provider.write.side_effect = error
            process_operation(op, self.provider)
            op.refresh_from_db()
            self.assertEqual(op.status, 'conflict')
            if etag == 'changed':
                self.provider.write.assert_not_called()

    def test_stale_toggle_role_and_live_provider_acl_block_queued_write(self):
        op = self.operation()
        ConnectedCalendar.objects.filter(pk=self.calendar.pk).update(write_enabled=False)
        process_operation(op, self.provider)
        self.provider.write.assert_not_called()
        op.refresh_from_db()
        self.assertEqual(op.status, 'failed')
        ConnectedCalendar.objects.filter(pk=self.calendar.pk).update(write_enabled=True)
        op = self.operation()
        self.provider.calendar.return_value = {'accessRole': 'reader'}
        process_operation(op, self.provider)
        self.provider.write.assert_not_called()
        op.refresh_from_db()
        self.assertEqual(op.status, 'failed')

    def test_permission_revoked_during_remote_preflight_is_rechecked(self):
        op = self.operation()
        def revoke(*args):
            GroupCalendarRole.objects.filter(membership__user=self.user, membership__group=self.group).delete()
            raise ProviderError('absent', 404)
        self.provider.event.side_effect = revoke
        process_operation(op, self.provider)
        self.provider.write.assert_not_called()
        op.refresh_from_db()
        self.assertEqual(op.status, 'failed')

    def test_uncertain_create_reconciles_without_duplicate(self):
        op = self.operation()
        self.provider.event.side_effect = ProviderError('absent', 404)
        self.provider.write.side_effect = ProviderError('timeout')
        process_operation(op, self.provider)
        op.refresh_from_db()
        self.assertEqual(op.status, 'processing')
        self.provider.event.side_effect = None
        self.provider.event.return_value = self.event(id=op.event_id, extendedProperties={'private': {'bandmateOperation': str(op.key)}})
        process_operation(op, self.provider)
        op.refresh_from_db()
        self.assertEqual(op.status, 'succeeded')
        self.assertEqual(self.provider.write.call_count, 1)

    def test_uncertain_patch_not_automatically_replayed(self):
        op = self.operation('update')
        self.provider.event.return_value = self.event()
        self.provider.write.side_effect = ProviderError('timeout')
        process_operation(op, self.provider)
        op.refresh_from_db()
        process_operation(op, self.provider)
        op.refresh_from_db()
        self.assertEqual(op.status, 'ambiguous')
        self.assertEqual(self.provider.write.call_count, 1)

    def test_delete_and_retry_of_remote_absence(self):
        op = self.operation('delete')
        self.provider.event.return_value = self.event()
        self.provider.write.return_value = {}
        process_operation(op, self.provider)
        op.refresh_from_db()
        self.assertEqual(op.status, 'succeeded')
        self.assertTrue(GoogleEvent.objects.get(external_id='existing').is_deleted)

    def test_429_bounded_backoff(self):
        op = self.operation()
        self.provider.calendar.side_effect = ProviderError('limited', 429)
        for attempt in range(5):
            process_operation(op, self.provider)
            op.refresh_from_db()
        self.assertEqual(op.status, 'failed')
        self.provider.write.assert_not_called()

    def test_write_policy_enable_checks_live_acl_and_stale_disable(self):
        self.calendar.write_enabled = False
        self.calendar.save()
        url = reverse('calendar_integrations:write_mode', args=['google', self.calendar.pk])
        self.assertEqual(self.client.post(url, {'write_enabled': 'true'}).status_code, 302)
        op = CalendarWriteOperation.objects.get()
        self.client.post(url, {'write_enabled': 'false'})
        process_operation(op, self.provider)
        self.calendar.refresh_from_db()
        self.assertFalse(self.calendar.write_enabled)
        op.refresh_from_db()
        self.assertEqual(op.status, 'failed')
        self.client.post(url, {'write_enabled': 'true'})
        op = CalendarWriteOperation.objects.latest('pk')
        process_operation(op, self.provider)
        self.calendar.refresh_from_db()
        self.assertTrue(self.calendar.write_enabled)

    def start_oauth(self, target=None):
        response = self.client.post(reverse('calendar_integrations:google_connect'), {'calendar': target.pk} if target else {})
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlparse(response.url).query)

    def test_oauth_readonly_pkce_single_use_and_encrypted_tokens(self):
        params = self.start_oauth()
        self.assertNotIn(GOOGLE_EVENT_WRITE_SCOPE, params['scope'][0].split())
        self.assertEqual(params['code_challenge_method'], ['S256'])
        with patch('calendar_integrations.oauth_views.exchange_code', return_value={'access_token': 'access', 'refresh_token': 'brand-new-refresh', 'scope': ' '.join(GOOGLE_READ_SCOPES)}), patch('calendar_integrations.oauth_views.identity', return_value={'email': 'band@example.com', 'email_verified': True}):
            url = reverse('calendar_integrations:google_callback')
            self.assertEqual(self.client.get(url, {'state': params['state'][0], 'code': 'code'}).status_code, 302)
            self.assertEqual(self.client.get(url, {'state': params['state'][0], 'code': 'code'}).status_code, 400)
        self.account.refresh_from_db()
        self.assertNotIn('brand-new-refresh', self.account.refresh_ciphertext)
        self.assertEqual(unseal(self.group.pk, 'google-calendar-refresh', self.account.refresh_ciphertext), 'brand-new-refresh')
        self.assertTrue(self.account.calendar_list_requested)
        self.assertEqual(GoogleOAuthState.objects.get().verifier_ciphertext, '')

    def test_oauth_group_switch_expiry_and_wrong_account(self):
        params = self.start_oauth(self.calendar)
        self.assertIn(GOOGLE_EVENT_WRITE_SCOPE, params['scope'][0].split())
        session = self.client.session
        session['active_group_id'] = self.other.pk
        session.save()
        with patch('calendar_integrations.oauth_views.exchange_code') as exchange:
            response = self.client.get(reverse('calendar_integrations:google_callback'), {'state': params['state'][0], 'code': 'code'})
            self.assertEqual(response.status_code, 400)
            exchange.assert_not_called()
        session['active_group_id'] = self.group.pk
        session.save()
        GoogleOAuthState.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.get(reverse('calendar_integrations:google_callback'), {'state': params['state'][0], 'code': 'code'}).status_code, 400)

    def test_disconnect_stops_client_and_clears_credential(self):
        client = GoogleCalendarClient(self.account)
        self.client.post(reverse('calendar_integrations:google_disconnect', args=[self.account.pk]))
        with patch('calendar_integrations.services.google.http_json') as http:
            with self.assertRaises(ProviderError):
                client.calendar('any')
            http.assert_not_called()
        self.account.refresh_from_db()
        self.assertEqual(self.account.refresh_ciphertext, '')
        self.assertFalse(self.account.is_connected)

    def test_account_tampering_and_shared_acknowledgement(self):
        self.assertEqual(self.client.post(reverse('calendar_integrations:google_disconnect', args=[self.other_account.pk])).status_code, 404)
        self.account.calendar_list = [{'id': 'new', 'summary': 'New', 'timeZone': 'UTC', 'accessRole': 'writer'}]
        self.account.save()
        url = reverse('calendar_integrations:google_select', args=[self.account.pk])
        self.assertEqual(self.client.post(url, {'external_id': 'new'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'external_id': 'forged', 'acknowledge_shared': 'yes'}).status_code, 404)
        self.assertEqual(self.client.post(url, {'external_id': 'new', 'acknowledge_shared': 'yes'}).status_code, 302)
        self.assertFalse(ConnectedCalendar.objects.get(external_calendar_id='new').write_enabled)

    def test_callback_log_redaction(self):
        record = logging.LogRecord('django.server', logging.INFO, '', 1, 'GET %s HTTP/1.1',
            ('/calendar/manage/google/callback/?state=secret&code=private-code',), None)
        OAuthQueryRedactor().filter(record)
        self.assertNotIn('private-code', record.getMessage())
        self.assertIn('[redacted]', record.getMessage())

    def test_each_account_refreshes_only_its_own_bound_token(self):
        self.other_account.granted_scopes = list(GOOGLE_READ_SCOPES)
        self.other_account.refresh_ciphertext = seal(self.other.pk, 'google-calendar-refresh', 'refresh-b')
        self.other_account.save()
        with patch('calendar_integrations.services.google.http_json', side_effect=[
            {'access_token': 'access-a'}, {'accessRole': 'writer'}, {'access_token': 'access-b'}, {'accessRole': 'reader'}]) as http:
            GoogleCalendarClient(self.account).calendar('a')
            GoogleCalendarClient(self.other_account).calendar('b')
        self.assertEqual(http.call_args_list[0].kwargs['body']['refresh_token'], 'refresh-a')
        self.assertEqual(http.call_args_list[2].kwargs['body']['refresh_token'], 'refresh-b')
        self.assertEqual(http.call_args_list[3].kwargs['token'], 'access-b')

    def test_invalid_grant_disables_only_its_account(self):
        with patch('calendar_integrations.services.google.http_json', side_effect=ProviderError('invalid_grant', 400)):
            with self.assertRaises(ProviderError):
                GoogleCalendarClient(self.account).calendar('a')
        self.account.refresh_from_db()
        self.other_account.refresh_from_db()
        self.assertFalse(self.account.is_connected)
        self.assertTrue(self.other_account.is_connected)

    def test_provider_write_uses_fixed_endpoint_conditional_header_and_no_notifications(self):
        client = GoogleCalendarClient(self.account)
        client.token = 'access'
        with patch('calendar_integrations.services.google.http_json', return_value={}) as http:
            client.write('calendar/with/slash', 'event/id', 'update', {'summary': 'Reviewed'}, etag='etag')
        args = http.call_args
        self.assertIn('calendar%2Fwith%2Fslash/events/event%2Fid?sendUpdates=none', args.args[0])
        self.assertEqual(args.kwargs['etag'], 'etag')
        self.assertEqual(args.kwargs['method'], 'PATCH')

    def test_wrong_identity_and_missing_write_scope_do_not_enable(self):
        params = self.start_oauth(self.calendar)
        original = self.account.refresh_ciphertext
        with patch('calendar_integrations.oauth_views.exchange_code', return_value={'access_token': 'access',
                'refresh_token': 'wrong', 'scope': ' '.join(GOOGLE_READ_SCOPES)}), patch('calendar_integrations.oauth_views.identity',
                return_value={'email': 'different@example.com', 'email_verified': True}):
            response = self.client.get(reverse('calendar_integrations:google_callback'), {'state': params['state'][0], 'code': 'code'})
        self.assertEqual(response.status_code, 400)
        self.account.refresh_from_db()
        self.assertEqual(self.account.refresh_ciphertext, original)

    def test_rotation_command_preserves_binding(self):
        old = self.key_file.read_bytes()
        self.key_file.write_bytes(Fernet.generate_key() + b'\n' + old)
        call_command('rotate_calendar_credentials', stdout=StringIO())
        self.key_file.write_bytes(self.key_file.read_bytes().splitlines()[0] + b'\n')
        self.account.refresh_from_db()
        self.assertEqual(unseal(self.group.pk, 'google-calendar-refresh', self.account.refresh_ciphertext), 'refresh-a')

    def test_sync_command_isolates_failures_and_runs_no_unrequested_writes(self):
        with patch('calendar_integrations.management.commands.sync_google_calendars.sync_calendar',
                   side_effect=[ProviderError('gone', 403), None]) as sync, patch('calendar_integrations.management.commands.sync_google_calendars.process_operation') as write:
            call_command('sync_google_calendars', lock_file=str(Path(self.temp.name) / 'sync.lock'), stdout=StringIO())
        self.assertEqual(sync.call_count, 2)
        write.assert_not_called()
        self.calendar.refresh_from_db()
        self.assertFalse(self.calendar.write_enabled)
        self.assertEqual(self.calendar.last_error_code, 'gone')

    def test_google_pages_render_without_remote_requests(self):
        with patch('socket.socket.connect', side_effect=AssertionError('No network on render')):
            self.assertContains(self.client.get(reverse('calendar_integrations:google_accounts')), 'band@example.com')
            self.assertContains(self.client.get(reverse('calendar_integrations:sources')), 'Verify permissions and enable editing' if not self.calendar.write_enabled else 'Disable editing')

    def test_provider_access_downgrade_never_exposes_private_events(self):
        cache_event(self.calendar, self.event())
        self.provider.calendar.return_value = {'accessRole': 'writerWithoutPrivateAccess'}
        self.provider.events.return_value = {'items': [self.event(visibility='private')], 'nextSyncToken': 'private'}
        sync_calendar(self.calendar, self.provider)
        self.assertEqual(GoogleEvent.objects.get(external_id='existing').title, 'Busy')
