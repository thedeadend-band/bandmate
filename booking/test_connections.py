import logging
import tempfile
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.db import connections, OperationalError
from django.urls import reverse
from django.utils import timezone
from bandmate.credential_vault import seal, unseal, VaultUnavailable
from bandmate.log_filters import OAuthQueryRedactor
from calendar_integrations.models import CalendarGoogleAccount
from calendar_integrations.services.google import ProviderError
from player.models import Group, GroupMembership
from .models import (BookingSettings, BookingMembershipRole, BookingOpenAICredential,
                     BookingGmailConnection, GmailOAuthState, BookingJob, BookingAIUsage, BookingBudgetReservation)
from .services.jobs import enqueue, process_batch
from .services.connections import GMAIL_READ_SCOPE, test_openai, test_gmail
from .services.budgets import reserve, begin, release, settle, BudgetPaused


class BookingConnectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Group.objects.create(name='Band A')
        cls.b = Group.objects.create(name='Band B')
        cls.manager = User.objects.create_user('connection-manager')
        cls.viewer = User.objects.create_user('connection-viewer')
        cls.staff = User.objects.create_user('connection-staff', is_staff=True)
        for group in [cls.a, cls.b]:
            member = GroupMembership.objects.create(group=group, user=cls.manager)
            BookingMembershipRole.objects.create(membership=member, role='booking_manager')
            BookingSettings.objects.create(group=group, enabled=True, ai_enabled=True,
                ai_model='test-model', monthly_budget_usd=1, daily_request_limit=2)
        member = GroupMembership.objects.create(group=cls.a, user=cls.viewer)
        BookingMembershipRole.objects.create(membership=member, role='booking_viewer')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.key_file = Path(self.directory.name) / 'vault'
        self.key_file.write_bytes(Fernet.generate_key() + b'\n')
        self.key_file.chmod(0o600)
        config = override_settings(BANDMATE_SECRET_KEY_FILE=str(self.key_file), BOOKING_OPENAI_MODELS=('test-model',),
            GMAIL_OAUTH_CLIENT_ID='gmail-client', GMAIL_OAUTH_CLIENT_SECRET='gmail-secret',
            GMAIL_OAUTH_REDIRECT_URI='https://bandmate.example/booking/gmail/callback/',
            GOOGLE_CALENDAR_CLIENT_ID='different-calendar-client')
        config.enable()
        self.addCleanup(config.disable)
        self.login()

    def login(self, user=None, group=None):
        self.client.force_login(user or self.manager)
        session = self.client.session
        session['active_group_id'] = (group or self.a).pk
        session.save()

    def credential(self, group=None, secret='sk-private-example-1234567890'):
        group = group or self.a
        return BookingOpenAICredential.objects.create(group=group, key_ciphertext=seal(group.pk, 'booking-openai', secret),
            fingerprint='fingerprint-' + str(group.pk), test_status='succeeded', available_models=['test-model'])

    def gmail(self, group=None, mailbox='band@example.com'):
        group = group or self.a
        return BookingGmailConnection.objects.create(group=group, mailbox=mailbox, is_connected=True,
            refresh_ciphertext=seal(group.pk, 'gmail-refresh', 'refresh-' + str(group.pk)), granted_scopes=[GMAIL_READ_SCOPE])

    def job(self, credential, kind='openai_test', key=None):
        return enqueue(credential.group, self.manager, kind, key or uuid.uuid4(), credential.generation)

    def oauth(self):
        response = self.client.post(reverse('booking:gmail_connect'), {'consent': 'read_profile'})
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlparse(response.url).query)

    def callback(self, params, mailbox='band@example.com', **token_changes):
        tokens = {'access_token': 'access-private', 'refresh_token': 'refresh-private', 'scope': GMAIL_READ_SCOPE, **token_changes}
        with patch('booking.gmail_views.exchange_gmail_code', return_value=tokens), patch(
                'booking.gmail_views.gmail_profile', return_value={'emailAddress': mailbox}):
            return self.client.get(reverse('booking:gmail_callback'), {'state': params['state'][0], 'code': 'private-auth-code'})

    def test_key_encrypted_masked_rotate_remove_and_no_page_network(self):
        key = 'sk-never-display-this-key-12345678'
        with patch('booking.services.connections.http_json') as http:
            response = self.client.post(reverse('booking:connections'), {'action': 'key', 'api_key': key, 'project_label': 'Our project'})
            self.assertEqual(response.status_code, 302)
            credential = BookingOpenAICredential.objects.get(group=self.a)
            self.assertNotIn(key, credential.key_ciphertext)
            self.assertEqual(unseal(self.a.pk, 'booking-openai', credential.key_ciphertext), key)
            page = self.client.get(reverse('booking:connections'))
            self.assertNotContains(page, key)
            self.assertNotContains(page, credential.key_ciphertext)
            self.assertContains(page, 'Our project')
            invalid = self.client.post(reverse('booking:connections'), {'action': 'key', 'api_key': key, 'project_label': 'x' * 121})
            self.assertNotContains(invalid, key)
            self.client.get(reverse('booking:usage'))
            http.assert_not_called()
        original_generation = credential.generation
        self.client.post(reverse('booking:connections'), {'action': 'key', 'api_key': 'sk-replacement-example-12345678'})
        credential.refresh_from_db()
        self.assertEqual(credential.generation, original_generation + 1)
        self.assertEqual(credential.test_status, 'untested')
        self.client.post(reverse('booking:remove_key'))
        credential.refresh_from_db()
        self.assertEqual(credential.key_ciphertext, '')
        self.assertFalse(BookingSettings.objects.get(group=self.a).ai_enabled)

    def test_vault_unavailable_never_saves_key(self):
        with override_settings(BANDMATE_SECRET_KEY_FILE=''):
            response = self.client.post(reverse('booking:connections'), {'action': 'key', 'api_key': 'sk-example-secret-1234567890'})
        self.assertContains(response, 'vault must be configured')
        self.assertFalse(BookingOpenAICredential.objects.exists())

    def test_connections_role_gate_toggle_and_csrf(self):
        routes = ['booking:connections', 'booking:remove_key', 'booking:gmail_connect', 'booking:gmail_disconnect']
        for user in [self.viewer, self.staff]:
            self.login(user)
            for route in routes:
                self.assertEqual(self.client.post(reverse(route)).status_code, 404)
        self.login()
        BookingSettings.objects.filter(group=self.a).update(enabled=False)
        for route in routes + ['booking:usage']:
            self.assertEqual(self.client.get(reverse(route)).status_code in (404, 405), True)
            self.assertEqual(self.client.post(reverse(route)).status_code in (404, 405), True)
        BookingSettings.objects.filter(group=self.a).update(enabled=True)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        session = client.session
        session['active_group_id'] = self.a.pk
        session.save()
        self.assertEqual(client.post(reverse('booking:remove_key')).status_code, 403)

    def test_policy_allowlist_positive_limits_and_tenant_fields(self):
        response = self.client.post(reverse('booking:connections'), {'action': 'policy', 'ai_enabled': 'on',
            'ai_model': 'unapproved', 'monthly_budget_usd': 1, 'daily_request_limit': 2, 'max_output_tokens': 1000})
        self.assertContains(response, 'Select a valid choice')
        response = self.client.post(reverse('booking:connections'), {'action': 'policy', 'ai_enabled': 'on',
            'ai_model': 'test-model', 'monthly_budget_usd': 0, 'daily_request_limit': 2, 'max_output_tokens': 1000})
        self.assertContains(response, 'positive cost and request limits')
        self.client.post(reverse('booking:connections'), {'action': 'policy', 'group': self.b.pk, 'enabled': '',
            'ai_model': 'test-model', 'monthly_budget_usd': '2.5', 'daily_request_limit': 5, 'max_output_tokens': 1000})
        self.assertTrue(BookingSettings.objects.get(group=self.a).enabled)
        self.assertEqual(BookingSettings.objects.get(group=self.a).monthly_budget_usd, Decimal('2.5'))
        self.assertEqual(BookingSettings.objects.get(group=self.b).monthly_budget_usd, 1)

    def test_two_keys_and_no_global_fallback(self):
        a, b = self.credential(), self.credential(self.b, 'sk-other-band-private-key')
        with patch('booking.services.connections.http_json', return_value={'data': [{'id': 'test-model'}, {'id': 'not-allowed'}]}) as http:
            for credential in [a, b]:
                job = self.job(credential)
                self.assertEqual(test_openai(job), ['test-model'])
            self.assertEqual([c.kwargs['token'] for c in http.call_args_list], ['sk-private-example-1234567890', 'sk-other-band-private-key'])
            self.assertTrue(all(c.args[0] == 'https://api.openai.com/v1/models' for c in http.call_args_list))
        a.key_ciphertext = b.key_ciphertext
        a.save()
        with patch('booking.services.connections.http_json') as http, self.assertRaises(VaultUnavailable):
            test_openai(self.job(a))
        http.assert_not_called()

    def test_worker_explicit_queue_idempotency_and_usage(self):
        credential = self.credential()
        key = uuid.uuid4()
        with patch('booking.services.connections.http_json', return_value={'data': [{'id': 'test-model'}]}) as http:
            for _ in range(2):
                self.client.post(reverse('booking:test_connection', args=['openai']), {'operation_key': key})
            http.assert_not_called()
            self.assertEqual(BookingJob.objects.count(), 1)
            call_command('booking_worker', once=True, lock_file=self.directory.name + '/worker.lock', stdout=StringIO())
            process_batch()
            http.assert_called_once()
        credential.refresh_from_db()
        self.assertEqual(credential.available_models, ['test-model'])
        self.assertEqual(BookingAIUsage.objects.get().estimated_usd, 0)
        self.assertEqual(BookingJob.objects.get().status, 'succeeded')
        self.assertEqual(self.client.post(reverse('booking:test_connection', args=['openai']), {'operation_key': 'bad'}).status_code, 400)

    def test_disabled_or_revoked_manager_or_rotated_key_blocks_queued_checks(self):
        credential = self.credential()
        for reason in ['disabled', 'role', 'inactive', 'rotated']:
            job = self.job(credential)
            if reason == 'disabled':
                BookingSettings.objects.filter(group=self.a).update(enabled=False)
            elif reason == 'role':
                BookingMembershipRole.objects.filter(membership__group=self.a, membership__user=self.manager).update(role='booking_viewer')
            elif reason == 'inactive':
                User.objects.filter(pk=self.manager.pk).update(is_active=False)
            else:
                BookingOpenAICredential.objects.filter(pk=credential.pk).update(generation=2)
            with patch('booking.services.connections.http_json') as http:
                process_batch()
                http.assert_not_called()
            job.refresh_from_db()
            self.assertEqual(job.status, 'failed')
            BookingSettings.objects.filter(group=self.a).update(enabled=True)
            BookingMembershipRole.objects.filter(membership__group=self.a, membership__user=self.manager).update(role='booking_manager')
            User.objects.filter(pk=self.manager.pk).update(is_active=True)

    def test_retry_backoff_and_failure_isolation(self):
        a, b = self.credential(), self.credential(self.b)
        ja, jb = self.job(a), self.job(b)
        with patch('booking.services.connections.http_json', side_effect=[ProviderError('private-secret-body', 429), {'data': []}]):
            process_batch()
        ja.refresh_from_db(); jb.refresh_from_db()
        self.assertEqual((ja.status, jb.status), ('pending', 'succeeded'))
        self.assertGreater(ja.run_after, timezone.now())
        self.assertNotIn('private', ja.error_code)
        BookingJob.objects.filter(pk=ja.pk).update(run_after=timezone.now(), attempts=3)
        with patch('booking.services.connections.http_json', side_effect=ProviderError('down', 503)):
            process_batch()
        ja.refresh_from_db()
        self.assertEqual((ja.status, ja.attempts), ('failed', 4))

    def test_expired_read_lease_recovers_once(self):
        job = self.job(self.credential())
        BookingJob.objects.filter(pk=job.pk).update(status='processing', attempts=1, lease_until=timezone.now() - timedelta(minutes=1))
        with patch('booking.services.connections.http_json', return_value={'data': []}) as http:
            process_batch(); process_batch()
            http.assert_called_once()
        self.assertEqual(BookingAIUsage.objects.count(), 1)

    def test_gmail_separate_pkce_single_use_encryption_and_identity(self):
        params = self.oauth()
        self.assertEqual(params['client_id'], ['gmail-client'])
        self.assertEqual(params['scope'], [GMAIL_READ_SCOPE])
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertNotIn('code_verifier', params)
        response = self.callback(params, mailbox='Band@Example.COM')
        self.assertEqual(response.status_code, 302)
        connection = BookingGmailConnection.objects.get(group=self.a)
        self.assertEqual(connection.mailbox, 'band@example.com')
        self.assertEqual(unseal(self.a.pk, 'gmail-refresh', connection.refresh_ciphertext), 'refresh-private')
        self.assertFalse(CalendarGoogleAccount.objects.exists())
        self.assertEqual(self.callback(params).status_code, 400)
        state = GmailOAuthState.objects.get()
        self.assertTrue(state.consumed)
        self.assertEqual(state.verifier_ciphertext, '')

    def test_switched_band_consumes_gmail_state_without_exchange(self):
        params = self.oauth()
        session = self.client.session
        session['active_group_id'] = self.b.pk
        session.save()
        with patch('booking.gmail_views.exchange_gmail_code') as exchange:
            response = self.client.get(reverse('booking:gmail_callback'), {'state': params['state'][0], 'code': 'code'})
        self.assertEqual(response.status_code, 400)
        exchange.assert_not_called()
        self.assertTrue(GmailOAuthState.objects.get().consumed)

    def test_gmail_expired_wrong_session_scope_and_duplicate_mailbox(self):
        params = self.oauth()
        GmailOAuthState.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.callback(params).status_code, 400)
        params = self.oauth()
        GmailOAuthState.objects.filter(consumed=False).update(session_digest='another-session')
        self.assertEqual(self.callback(params).status_code, 400)
        params = self.oauth()
        self.assertEqual(self.callback(params, scope='https://www.googleapis.com/auth/calendar.events').status_code, 400)
        self.gmail(self.b)
        params = self.oauth()
        response = self.callback(params)
        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, 'Band B', status_code=400)
        self.assertFalse(BookingGmailConnection.objects.get(group=self.a).is_connected)

    def test_gmail_disconnect_clears_authorization_and_invalidates_queued_work(self):
        connection = self.gmail()
        job = self.job(connection, 'gmail_test')
        params = self.oauth()
        self.client.post(reverse('booking:gmail_disconnect'))
        with patch('booking.services.connections.http_json') as http:
            process_batch()
            http.assert_not_called()
        connection.refresh_from_db()
        self.assertIsNone(connection.mailbox)
        self.assertEqual(connection.refresh_ciphertext, '')
        self.assertEqual(self.callback(params).status_code, 400)
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')

    def test_gmail_invalid_grant_only_disconnects_own_band(self):
        a, b = self.gmail(), self.gmail(self.b, 'other@example.com')
        with patch('booking.services.connections.http_json', side_effect=ProviderError('invalid_grant', 400)):
            with self.assertRaises(ProviderError):
                test_gmail(self.job(a, 'gmail_test'))
        a.refresh_from_db(); b.refresh_from_db()
        self.assertFalse(a.is_connected)
        self.assertTrue(b.is_connected)

    def test_gmail_profile_refresh_uses_own_credential_and_checks_identity(self):
        connection = self.gmail()
        with patch('booking.services.connections.http_json', side_effect=[{'access_token': 'access'}, {'emailAddress': 'band@example.com'}]) as http:
            test_gmail(self.job(connection, 'gmail_test'))
            self.assertEqual(http.call_args_list[0].kwargs['body']['client_id'], 'gmail-client')
            self.assertEqual(http.call_args_list[0].kwargs['body']['refresh_token'], 'refresh-' + str(self.a.pk))
            self.assertEqual(http.call_args_list[1].args[0], 'https://gmail.googleapis.com/gmail/v1/users/me/profile')
        with patch('booking.services.connections.http_json', side_effect=[{'access_token': 'access'}, {'emailAddress': 'wrong@example.com'}]):
            with self.assertRaises(ProviderError):
                test_gmail(self.job(connection, 'gmail_test'))

    def test_usage_is_group_scoped(self):
        BookingAIUsage.objects.create(group=self.b, task_type='private-band-b', key_fingerprint='secret', result='succeeded')
        self.login(self.viewer)
        page = self.client.get(reverse('booking:usage'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'private-band-b')
        self.assertNotContains(page, 'secret')

    def test_rotation_and_callback_log_redaction(self):
        credential, gmail = self.credential(), self.gmail()
        self.oauth()
        old = self.key_file.read_bytes()
        self.key_file.write_bytes(Fernet.generate_key() + b'\n' + old)
        output = StringIO()
        call_command('rotate_booking_credentials', stdout=output)
        self.key_file.write_bytes(self.key_file.read_bytes().splitlines()[0] + b'\n')
        credential.refresh_from_db(); gmail.refresh_from_db()
        self.assertTrue(unseal(self.a.pk, 'booking-openai', credential.key_ciphertext).startswith('sk-'))
        self.assertTrue(unseal(self.a.pk, 'gmail-refresh', gmail.refresh_ciphertext).startswith('refresh-'))
        state = GmailOAuthState.objects.get()
        self.assertTrue(unseal(self.a.pk, 'gmail-pkce', state.verifier_ciphertext))
        record = logging.LogRecord('django.server', 20, '', 0, 'GET /booking/gmail/callback/?code=SECRET&state=NONCE HTTP/1.1', (), None)
        OAuthQueryRedactor().filter(record)
        self.assertNotIn('SECRET', record.getMessage())
        self.assertNotIn('NONCE', record.getMessage())

    def test_budget_idempotency_cap_and_two_band_isolation(self):
        self.credential(); self.credential(self.b)
        key = uuid.uuid4()
        first = reserve(self.a, key, 'test-model', '.6')
        self.assertEqual(reserve(self.a, key, 'test-model', '.6').pk, first.pk)
        with self.assertRaises(BudgetPaused):
            reserve(self.a, key, 'test-model', '.5')
        with self.assertRaises(BudgetPaused):
            reserve(self.a, uuid.uuid4(), 'test-model', '.5')
        reserve(self.b, uuid.uuid4(), 'test-model', '1')
        self.assertEqual(BookingBudgetReservation.objects.count(), 2)

    def test_budget_daily_limit_and_invalid_cost_model(self):
        self.credential()
        for invalid in ['-1', 'NaN', 'Infinity', '.0000001', '1000000']:
            with self.assertRaises(BudgetPaused):
                reserve(self.a, uuid.uuid4(), 'test-model', invalid)
        with self.assertRaises(BudgetPaused):
            reserve(self.a, uuid.uuid4(), 'not-allowed', 0)
        for _ in range(2):
            reserve(self.a, uuid.uuid4(), 'test-model', '.01')
        with self.assertRaises(BudgetPaused):
            reserve(self.a, uuid.uuid4(), 'test-model', '.01')

    def test_budget_preflight_rechecks_disable_rotation_model_and_limit(self):
        credential = self.credential()
        key = uuid.uuid4()
        reserve(self.a, key, 'test-model', '.6')
        for changes in [{'ai_enabled': False}, {'monthly_budget_usd': '.5'}, {'ai_model': ''}, {'enabled': False}]:
            BookingSettings.objects.filter(group=self.a).update(**changes)
            with self.assertRaises(BudgetPaused):
                begin(self.a, key)
            BookingSettings.objects.filter(group=self.a).update(ai_enabled=True, enabled=True, monthly_budget_usd=1, ai_model='test-model')
        BookingOpenAICredential.objects.filter(pk=credential.pk).update(generation=2)
        with self.assertRaises(BudgetPaused):
            begin(self.a, key)

    def test_budget_settlement_exactly_once_and_uncertain_attempt_keeps_charge(self):
        self.credential()
        key = uuid.uuid4()
        reserve(self.a, key, 'test-model', '.8')
        begin(self.a, key)
        self.assertEqual(release(self.a, key), 0)
        with self.assertRaises(BudgetPaused):
            begin(self.a, key)
        with self.assertRaises(BudgetPaused):
            reserve(self.a, uuid.uuid4(), 'test-model', '.3')
        args = dict(input_tokens=10, output_tokens=20, pricing_version='test-only', result='succeeded')
        usage = settle(self.a, key, '.2', **args)
        self.assertEqual(settle(self.a, key, '.2', **args).pk, usage.pk)
        with self.assertRaises(BudgetPaused):
            settle(self.a, key, '.3', **args)
        reserve(self.a, uuid.uuid4(), 'test-model', '.8')

    def test_budget_release_and_utc_rollover(self):
        self.credential()
        key = uuid.uuid4()
        reserve(self.a, key, 'test-model', '.8')
        BookingBudgetReservation.objects.filter(job_key=key).update(day=timezone.now().date() - timedelta(days=1))
        with self.assertRaises(BudgetPaused):
            begin(self.a, key)
        self.assertEqual(release(self.a, key), 1)
        self.assertEqual(release(self.a, key), 0)
        with self.assertRaises(BudgetPaused):
            begin(self.a, key)
        reserve(self.a, uuid.uuid4(), 'test-model', '1')


@override_settings(BOOKING_OPENAI_MODELS=('test-model',))
class ConcurrentBudgetTests(TransactionTestCase):
    def test_simultaneous_reservations_cannot_exceed_band_cap(self):
        group = Group.objects.create(name='Concurrent budget band')
        BookingSettings.objects.create(group=group, enabled=True, ai_enabled=True, ai_model='test-model',
            monthly_budget_usd=1, daily_request_limit=10)
        BookingOpenAICredential.objects.create(group=group, key_ciphertext='not-decrypted-by-budget-service',
            fingerprint='test', test_status='succeeded', available_models=['test-model'])
        barrier = Barrier(2)

        def attempt():
            key = uuid.uuid4()
            barrier.wait(timeout=5)
            try:
                # Shared-memory SQLite returns SQLITE_LOCKED immediately. Retry
                # the entire short transaction, never a partial allowance check.
                for _ in range(50):
                    try:
                        reserve(group, key, 'test-model', '.6')
                        return 'reserved'
                    except BudgetPaused:
                        return 'paused'
                    except OperationalError as exc:
                        if 'locked' not in str(exc).lower():
                            raise
                        time.sleep(.01)
                raise AssertionError('Concurrent reservation did not finish')
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertCountEqual(results, ['reserved', 'paused'])
        self.assertEqual(BookingBudgetReservation.objects.get().authorized_usd, Decimal('.6'))
