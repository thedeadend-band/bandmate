import base64
import json
import tempfile
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from bandmate.credential_vault import seal
from calendar_integrations.models import BandmateLocalCalendar, LocalEvent, GroupCalendarRole, CalendarWriteOperation, CalendarGoogleAccount, ConnectedCalendar
from calendar_integrations.services.google import ProviderError
from player.models import Group, GroupMembership
from .models import (BookingSettings, BookingMembershipRole, BookingGmailConnection, BookingOpenAICredential,
    BookingContact, BookingOpportunity, BookingDraft, BookingTemplateVersion, BookingEmailTemplate,
    BookingOutreachMessage, BookingJob, BookingSuppression, BookingReply, BookingConversationReference,
    BookingAIUsage, BookingBudgetReservation, BookingCalendarPublication, BookingResearchRun)
from .services.connections import GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE
from .services.outreach import compose, save_template, snapshot, fingerprint, approve_send, queue_read
from .services.jobs import process_batch
from .services.research_jobs import new_preview, confirm_preview
from .services.imports import purge_messages
from .services.records import digest


@override_settings(BOOKING_SEND_DAILY_LIMIT=10, BOOKING_OPENAI_MODELS=('test-model',),
    BOOKING_AI_PRICING={'test-model': {'input': '1', 'output': '2', 'version': 'test'}},
    GMAIL_OAUTH_CLIENT_ID='test', GMAIL_OAUTH_CLIENT_SECRET='test',
    GMAIL_OAUTH_REDIRECT_URI='https://bandmate.example/booking/gmail/callback/')
class OutreachTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a, cls.b = Group.objects.create(name='Band A'), Group.objects.create(name='Band B')
        cls.manager = User.objects.create_user('outreach-manager')
        cls.editor = User.objects.create_user('outreach-editor')
        cls.viewer = User.objects.create_user('outreach-viewer')
        cls.staff = User.objects.create_user('outreach-staff', is_staff=True)
        for group in (cls.a, cls.b):
            BookingSettings.objects.create(group=group, enabled=True, ai_enabled=True, ai_model='test-model',
                monthly_budget_usd=10, daily_request_limit=10)
            for user, role in [(cls.manager, 'booking_manager'), (cls.editor, 'booking_editor'), (cls.viewer, 'booking_viewer')]:
                membership = GroupMembership.objects.create(group=group, user=user)
                BookingMembershipRole.objects.create(membership=membership, role=role)
            contact = BookingContact.objects.create(group=group, name='Booker', email=f'booker{group.pk}@example.com')
            BookingOpportunity.objects.create(group=group, name='Summer concert', event_year=2027,
                contact=contact, stage='confirmed', event_date=date(2027, 7, 10))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        vault = Path(self.temp.name)/'key'
        vault.write_bytes(Fernet.generate_key()); vault.chmod(0o600)
        config = override_settings(BANDMATE_SECRET_KEY_FILE=str(vault)); config.enable(); self.addCleanup(config.disable)
        for group in (self.a, self.b):
            BookingGmailConnection.objects.create(group=group, mailbox=f'band{group.pk}@example.com',
                refresh_ciphertext=seal(group.pk, 'gmail-refresh', f'refresh-{group.pk}'),
                is_connected=True, granted_scopes=[GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE])
            BookingOpenAICredential.objects.create(group=group, key_ciphertext=seal(group.pk, 'booking-openai', f'ai-{group.pk}'),
                fingerprint=digest(str(group.pk)), test_status='succeeded', available_models=['test-model'])
        self.login()

    def login(self, user=None, group=None):
        self.client.force_login(user or self.manager)
        session = self.client.session; session['active_group_id'] = (group or self.a).pk; session.save()

    def draft(self, group=None):
        group = group or self.a
        opportunity = BookingOpportunity.objects.get(group=group)
        draft = compose(group, self.editor, opportunity, {'contact': opportunity.contact})
        draft.subject = 'Booking inquiry'
        draft.body = 'Hello Booker,\n\nWe are ' + group.name + '.\n\nThank you.'
        draft.save()
        return draft

    def approved(self, draft=None):
        draft = draft or self.draft()
        account = BookingGmailConnection.objects.get(group=draft.group)
        return approve_send(draft.group, self.manager, draft.pk, draft.revision,
            fingerprint(snapshot(draft, account.mailbox)), uuid.uuid4(), account.generation)

    def http(self, url, **kwargs):
        if url.endswith('/token'):
            return {'access_token': 'access-token'}
        if url.endswith('/profile'):
            return {'emailAddress': f'band{self.a.pk}@example.com'}
        if url.endswith('/messages/send'):
            return {'id': 'gmail-sent-1', 'threadId': 'gmail-thread-1'}
        raise AssertionError('Unexpected provider request')

    def sent(self):
        message = self.approved()
        with patch('booking.services.mail_delivery.http_json', side_effect=self.http):
            process_batch()
        message.refresh_from_db()
        self.assertEqual(message.status, 'sent', message.error_code)
        return message

    def test_template_versions_missing_placeholders_and_tenant_links(self):
        data = {'name': 'Venue pitch', 'category': 'venue', 'subject': '{{band_name}} at {{venue_name}}',
            'body': 'Hello {{contact_name}},\n\nOur EPK: {{epk_url}}'}
        row = save_template(self.a, self.manager, data)
        data.update(revision=1, subject='{{band_name}} booking inquiry')
        save_template(self.a, self.manager, data, row)
        self.assertEqual(row.versions.count(), 2)
        version = row.versions.first(); version.body = 'Changed'
        with self.assertRaises(ValidationError): version.save()
        opportunity = BookingOpportunity.objects.get(group=self.a)
        with self.assertRaisesMessage(ValidationError, 'epk_url'):
            compose(self.a, self.editor, opportunity, {'contact': opportunity.contact, 'template': row})
        response = self.client.post(reverse('booking:compose', args=[opportunity.pk]),
            {'contact': BookingContact.objects.get(group=self.b).pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BookingDraft.objects.exists())

    def test_preview_is_local_and_requires_explicit_csrf_manager_send(self):
        draft = self.draft()
        with patch('booking.services.mail_delivery.http_json') as http:
            response = self.client.get(reverse('booking:send', args=[draft.pk]))
            self.assertContains(response, 'From:')
            self.assertFalse(BookingJob.objects.exists())
            self.assertEqual(self.client.post(reverse('booking:send', args=[draft.pk]), {}).status_code, 400)
        http.assert_not_called()
        self.login(self.editor)
        self.assertEqual(self.client.get(reverse('booking:send', args=[draft.pk])).status_code, 404)
        client = Client(enforce_csrf_checks=True); client.force_login(self.manager)
        session = client.session; session['active_group_id'] = self.a.pk; session.save()
        self.assertEqual(client.post(reverse('booking:send', args=[draft.pk]), {}).status_code, 403)

    def test_double_click_creates_one_immutable_approval_and_send(self):
        draft = self.draft()
        page = self.client.get(reverse('booking:send', args=[draft.pk]))
        data = {'confirm': 'send_exact_message', 'revision': draft.revision,
            'generation': page.context['generation'], 'approval_hash': page.context['approval_hash'],
            'operation_key': page.context['operation_key']}
        self.assertEqual(self.client.post(reverse('booking:send', args=[draft.pk]), data).status_code, 302)
        self.assertEqual(self.client.post(reverse('booking:send', args=[draft.pk]), data).status_code, 302)
        self.assertEqual(BookingOutreachMessage.objects.count(), 1)
        with patch('booking.services.mail_delivery.http_json', side_effect=self.http) as http:
            process_batch(); process_batch()
        self.assertEqual(sum(call.args[0].endswith('/messages/send') for call in http.call_args_list), 1)
        message = BookingOutreachMessage.objects.get()
        self.assertEqual(message.status, 'sent')
        message.snapshot['body'] = 'Tampered'
        with self.assertRaises(ValidationError): message.save()
        with self.assertRaises(ProtectedError): draft.delete()

    def test_stale_preview_and_changed_gmail_generation_block_send(self):
        draft = self.draft()
        old_hash = fingerprint(snapshot(draft, f'band{self.a.pk}@example.com'))
        BookingDraft.objects.filter(pk=draft.pk).update(body='New text', revision=2)
        with self.assertRaises(ValidationError):
            approve_send(self.a, self.manager, draft.pk, 1, old_hash, uuid.uuid4(), 1)
        draft.refresh_from_db()
        with self.assertRaises(ValidationError):
            approve_send(self.a, self.manager, draft.pk, 2, fingerprint(snapshot(draft, f'band{self.a.pk}@example.com')), uuid.uuid4(), 99)
        self.assertFalse(BookingOutreachMessage.objects.exists())

    def test_suppression_after_approval_prevents_provider_calls(self):
        message = self.approved()
        BookingSuppression.objects.create(group=self.a, kind='email', value=message.snapshot['to'], reason='Opted out', do_not_contact=True)
        with patch('booking.services.mail_delivery.http_json') as http: process_batch()
        http.assert_not_called(); message.refresh_from_db()
        self.assertEqual(message.status, 'failed')

    def test_suppression_during_profile_prevents_send(self):
        message = self.approved()
        def response(url, **kwargs):
            if url.endswith('/profile'):
                BookingSuppression.objects.create(group=self.a, kind='email', value=message.snapshot['to'], reason='Opted out')
            return self.http(url, **kwargs)
        with patch('booking.services.mail_delivery.http_json', side_effect=response) as http: process_batch()
        self.assertFalse(any(c.args[0].endswith('/messages/send') for c in http.call_args_list))

    def test_role_and_key_revocation_block_queued_sends(self):
        message = self.approved()
        BookingGmailConnection.objects.filter(group=self.a).update(generation=2)
        with patch('booking.services.mail_delivery.http_json') as http: process_batch()
        http.assert_not_called(); message.refresh_from_db(); self.assertEqual(message.status, 'failed')

    def test_sender_mismatch_never_sends(self):
        self.approved()
        with patch('booking.services.mail_delivery.http_json', side_effect=[{'access_token': 'x'}, {'emailAddress': 'wrong@example.com'}]) as http:
            process_batch()
        self.assertEqual(http.call_count, 2)
        self.assertEqual(BookingOutreachMessage.objects.get().status, 'failed')

    def test_uncertain_send_is_never_replayed_after_lease_recovery(self):
        message = self.approved()
        def response(url, **kwargs):
            if url.endswith('/messages/send'): raise ProviderError('google_transport')
            return self.http(url, **kwargs)
        with patch('booking.services.mail_delivery.http_json', side_effect=response): process_batch()
        message.refresh_from_db(); self.assertEqual(message.status, 'uncertain')
        BookingJob.objects.filter(pk=message.job_id).update(status='processing', lease_until=timezone.now()-timedelta(seconds=1))
        with patch('booking.services.mail_delivery.http_json') as http: process_batch()
        http.assert_not_called()
        self.assertEqual(BookingOutreachMessage.objects.get().status, 'uncertain')

    def test_reconciliation_requires_exact_sent_content_and_never_resends(self):
        message = self.approved()
        BookingOutreachMessage.objects.filter(pk=message.pk).update(status='uncertain')
        BookingJob.objects.filter(pk=message.job_id).update(status='failed')
        message.refresh_from_db()
        queue_read(self.a, self.manager, message, 'gmail_reconcile')
        def response(url, **kwargs):
            if '/messages?' in url: return {'messages': [{'id': 'confirmed'}]}
            if '/messages/confirmed?' in url:
                return {'raw': message.raw, 'id': 'confirmed', 'threadId': 'thread-confirmed', 'labelIds': ['SENT']}
            return self.http(url, **kwargs)
        with patch('booking.services.mail_delivery.http_json', side_effect=response) as http: process_batch()
        message.refresh_from_db(); self.assertEqual(message.status, 'sent', message.error_code)
        self.assertFalse(any(c.kwargs.get('method') == 'POST' and '/gmail/v1/' in c.args[0] for c in http.call_args_list))

    def test_missing_reconciliation_result_stays_uncertain(self):
        message = self.approved(); BookingOutreachMessage.objects.filter(pk=message.pk).update(status='uncertain')
        BookingJob.objects.filter(pk=message.job_id).update(status='failed'); message.refresh_from_db()
        queue_read(self.a, self.manager, message, 'gmail_reconcile')
        with patch('booking.services.mail_delivery.http_json', side_effect=[{'access_token': 'x'},
            {'emailAddress': message.mailbox}, {'messages': []}]): process_batch()
        message.refresh_from_db(); self.assertEqual(message.status, 'uncertain')

    def test_send_confirmed_during_revocation_preserves_proof(self):
        message = self.approved()
        def response(url, **kwargs):
            if url.endswith('/messages/send'):
                BookingSettings.objects.filter(group=self.a).update(enabled=False)
            return self.http(url, **kwargs)
        with patch('booking.services.mail_delivery.http_json', side_effect=response): process_batch()
        message.refresh_from_db(); self.assertEqual(message.status, 'sent')

    def test_band_and_recipient_rate_limits(self):
        self.approved()
        with self.assertRaises(ValidationError): self.approved()
        with override_settings(BOOKING_SEND_DAILY_LIMIT=0):
            with self.assertRaises(ValidationError): self.approved(self.draft(self.b))

    def test_cross_band_staff_viewer_and_locked_edit_boundaries(self):
        message = self.approved()
        self.login(group=self.b)
        for route in ('draft', 'send'):
            self.assertEqual(self.client.get(reverse('booking:'+route, args=[message.draft_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse('booking:message', args=[message.pk])).status_code, 404)
        self.login(self.staff)
        self.assertEqual(self.client.get(reverse('booking:message', args=[message.pk])).status_code, 404)
        self.login()
        self.assertEqual(self.client.post(reverse('booking:draft', args=[message.draft_id]), {}).status_code, 400)

    def test_send_scope_is_explicit_and_bound_to_existing_mailbox(self):
        account = BookingGmailConnection.objects.get(group=self.a)
        account.granted_scopes = [GMAIL_READ_SCOPE]; account.save()
        response = self.client.post(reverse('booking:gmail_connect'), {'consent': 'enable_reviewed_send'})
        params = parse_qs(urlparse(response.url).query)
        self.assertIn(GMAIL_SEND_SCOPE, params['scope'][0])
        with patch('booking.gmail_views.exchange_gmail_code', return_value={'access_token': 'x', 'refresh_token': 'new',
            'scope': GMAIL_READ_SCOPE+' '+GMAIL_SEND_SCOPE}), patch('booking.gmail_views.gmail_profile', return_value={'emailAddress': 'different@example.com'}):
            response = self.client.get(reverse('booking:gmail_callback'), {'state': params['state'][0], 'code': 'x'})
        self.assertEqual(response.status_code, 400)
        account.refresh_from_db(); self.assertEqual(account.granted_scopes, [GMAIL_READ_SCOPE])

    def test_readonly_reconnect_does_not_inherit_send_scope(self):
        response = self.client.post(reverse('booking:gmail_connect'), {'consent': 'read_profile'})
        params = parse_qs(urlparse(response.url).query)
        with patch('booking.gmail_views.exchange_gmail_code', return_value={'access_token': 'x', 'refresh_token': 'new',
            'scope': GMAIL_READ_SCOPE+' '+GMAIL_SEND_SCOPE}), patch('booking.gmail_views.gmail_profile', return_value={'emailAddress': f'band{self.a.pk}@example.com'}):
            response = self.client.get(reverse('booking:gmail_callback'), {'state': params['state'][0], 'code': 'x'})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(GMAIL_SEND_SCOPE, BookingGmailConnection.objects.get(group=self.a).granted_scopes)

    def reply_response(self, message):
        return {'id': message.gmail_thread_id, 'messages': [{'id': 'reply-1', 'threadId': message.gmail_thread_id,
            'internalDate': str(int(timezone.now().timestamp()*1000)), 'snippet': 'Please send availability.',
            'payload': {'headers': [{'name': 'From', 'value': message.snapshot['to']},
                {'name': 'To', 'value': message.mailbox}, {'name': 'Subject', 'value': 'Re: Booking inquiry'}]}}]}

    def test_replies_are_scoped_expiring_and_do_not_send_or_change_stage(self):
        message = self.sent()
        queue_read(self.a, self.manager, message, 'gmail_replies', 7)
        with patch('booking.services.mail_delivery.http_json', side_effect=[{'access_token': 'x'},
            {'emailAddress': message.mailbox}, self.reply_response(message)]) as http: process_batch()
        self.assertEqual(BookingReply.objects.count(), 1)
        self.assertEqual(BookingOpportunity.objects.get(group=self.a).stage, 'confirmed')
        self.assertIn('format=metadata', http.call_args.args[0])
        purge_messages(self.a)
        self.assertFalse(BookingConversationReference.objects.filter(purged=False).exists())
        queue_read(self.a, self.manager, message, 'gmail_replies', 7)
        with patch('booking.services.mail_delivery.http_json', side_effect=[{'access_token': 'x'},
            {'emailAddress': message.mailbox}, self.reply_response(message)]): process_batch()
        self.assertFalse(BookingConversationReference.objects.filter(purged=False).exists())
        self.assertEqual(BookingOutreachMessage.objects.get().snapshot['body'], message.snapshot['body'])

    def test_purge_during_reply_read_discards_results(self):
        message = self.sent(); queue_read(self.a, self.manager, message, 'gmail_replies')
        def response(url, **kwargs):
            if '/threads/' in url:
                self.client.post(reverse('booking:purge_mail'), {'confirm': 'delete_imported_email'})
                return self.reply_response(message)
            return self.http(url, **kwargs)
        with patch('booking.services.mail_delivery.http_json', side_effect=response): process_batch()
        self.assertFalse(BookingReply.objects.exists())

    def test_followup_uses_only_confirmed_same_band_thread(self):
        message = self.sent()
        followup = compose(self.a, self.editor, message.draft.opportunity, {'contact': message.draft.contact}, message)
        self.assertEqual(snapshot(followup, message.mailbox)['thread_id'], message.gmail_thread_id)
        other = BookingOpportunity.objects.get(group=self.b)
        with self.assertRaises(ValidationError): compose(self.b, self.editor, other, {'contact': other.contact}, message)

    def calendar(self):
        member = GroupMembership.objects.get(group=self.a, user=self.manager)
        GroupCalendarRole.objects.create(membership=member, role='calendar_editor')
        return BandmateLocalCalendar.objects.create(group=self.a, name='Gig calendar', write_enabled=True)

    def publication_data(self, calendar, **changes):
        return {'target': f'local:{calendar.pk}', 'title': 'Reviewed concert', 'timezone': 'America/Los_Angeles',
            'is_all_day': 'on', 'start_date': '2027-07-10', 'end_date': '2027-07-11', 'location': 'Stage',
            'description': 'Public description', 'operation_key': uuid.uuid4(), 'action': 'create',
            'confirm': 'reviewed_calendar_action', **changes}

    def test_publication_requires_both_roles_and_confirmed_gig(self):
        calendar = BandmateLocalCalendar.objects.create(group=self.a, name='Local', write_enabled=True)
        opportunity = BookingOpportunity.objects.get(group=self.a)
        url = reverse('booking:publication', args=[opportunity.pk])
        self.assertEqual(self.client.post(url, self.publication_data(calendar)).status_code, 404)
        member = GroupMembership.objects.get(group=self.a, user=self.manager)
        GroupCalendarRole.objects.create(membership=member, role='calendar_editor')
        BookingOpportunity.objects.filter(pk=opportunity.pk).update(stage='offered')
        self.assertEqual(self.client.post(url, self.publication_data(calendar)).status_code, 400)
        self.assertFalse(LocalEvent.objects.exists())

    def test_publication_dedup_update_unlink_relink_remove(self):
        calendar = self.calendar(); opportunity = BookingOpportunity.objects.get(group=self.a)
        url = reverse('booking:publication', args=[opportunity.pk]); data = self.publication_data(calendar)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 400)
        self.assertEqual(LocalEvent.objects.count(), 1)
        link = BookingCalendarPublication.objects.get(); event = LocalEvent.objects.get()
        data.update(action='update', title='Updated', operation_key=uuid.uuid4(), version=event.version, publication_revision=link.revision)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        event.refresh_from_db(); self.assertEqual(event.title, 'Updated')
        for action in ('unlink', 'relink', 'delete'):
            link.refresh_from_db(); data.update(action=action, publication_revision=link.revision, operation_key=uuid.uuid4(), version=event.version)
            self.assertEqual(self.client.post(url, data).status_code, 302, action)
            event.refresh_from_db()
            self.assertEqual(event.is_deleted, action == 'delete')

    def test_publication_does_not_follow_opportunity_edits(self):
        calendar = self.calendar(); opportunity = BookingOpportunity.objects.get(group=self.a)
        url = reverse('booking:publication', args=[opportunity.pk])
        self.client.post(url, self.publication_data(calendar))
        BookingOpportunity.objects.filter(pk=opportunity.pk).update(name='Changed privately')
        self.assertEqual(LocalEvent.objects.get().title, 'Reviewed concert')
        self.login(group=self.b)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_ai_draft_requires_consent_and_only_selects_approved_paragraphs(self):
        draft = self.draft()
        run = new_preview(self.a, self.manager, 'ai_draft', {'draft_id': draft.pk})
        self.assertFalse(BookingJob.objects.exists())
        self.assertEqual(self.client.post(reverse('booking:research_confirm', args=[run.pk]), {'consent': 'scoped_read'}).status_code, 400)
        confirm_preview(self.a, self.manager, run.pk)
        response = {'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 20},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps({'paragraph_ids': ['0', '2']})}]}]}
        with patch('booking.services.draft_ai.http_json', side_effect=[{'input_tokens': 100}, response]) as http: process_batch()
        draft.refresh_from_db(); run.refresh_from_db()
        self.assertEqual(run.status, 'succeeded', run.last_error_code)
        self.assertEqual(draft.ai_suggestion['body'], 'Hello Booker,\n\nThank you.')
        self.assertIn('We are', draft.body)
        self.assertEqual(http.call_args.kwargs['token'], f'ai-{self.a.pk}')
        self.assertEqual(BookingAIUsage.objects.get().task_type, 'draft_writing')
        self.assertFalse(BookingOutreachMessage.objects.exists())
        self.assertEqual(self.client.post(reverse('booking:adopt_draft_ai', args=[draft.pk]),
            {'confirm': 'reviewed_draft_suggestion', 'run_id': run.pk}).status_code, 302)
        draft.refresh_from_db(); self.assertNotIn('We are', draft.body)

    def test_ai_invented_paragraph_is_rejected_and_usage_recorded(self):
        draft = self.draft(); run = new_preview(self.a, self.manager, 'ai_draft', {'draft_id': draft.pk})
        confirm_preview(self.a, self.manager, run.pk)
        response = {'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 20},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"paragraph_ids":["invented"]}'}]}]}
        with patch('booking.services.draft_ai.http_json', side_effect=[{'input_tokens': 100}, response]): process_batch()
        draft.refresh_from_db(); self.assertEqual(draft.ai_suggestion, {})
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'settled')
        self.assertEqual(BookingAIUsage.objects.get().result, 'invalid_draft_arrangement')

    def test_usage_report_and_export_are_tenant_scoped(self):
        BookingAIUsage.objects.create(group=self.b, task_type='private-other', key_fingerprint='secret', result='completed')
        response = self.client.get(reverse('booking:usage'))
        self.assertContains(response, 'By model'); self.assertNotContains(response, 'private-other')
        response = self.client.get(reverse('booking:usage_export'))
        self.assertEqual(response.json()['usage'], [])
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_readonly_account_cannot_approve_send(self):
        BookingGmailConnection.objects.filter(group=self.a).update(granted_scopes=[GMAIL_READ_SCOPE])
        with self.assertRaises(ValidationError): self.approved()
        self.assertFalse(BookingJob.objects.exists())

    def test_cancel_before_start_never_sends(self):
        message = self.approved()
        self.assertEqual(self.client.post(reverse('booking:cancel_send', args=[message.pk])).status_code, 302)
        with patch('booking.services.mail_delivery.http_json') as http: process_batch()
        http.assert_not_called(); message.refresh_from_db(); self.assertEqual(message.status, 'cancelled')

    def test_cc_domain_opt_out_is_checked(self):
        draft = self.draft(); draft.cc = ['second@restricted.example']; draft.save()
        BookingSuppression.objects.create(group=self.a, kind='domain', value='restricted.example', reason='No contact', active=False, do_not_contact=True)
        with self.assertRaises(ValidationError): self.approved(draft)

    def test_two_bands_send_only_from_their_own_mailboxes_and_keys(self):
        message = self.approved(self.draft(self.b))
        def response(url, **kwargs):
            if url.endswith('/token'):
                self.assertEqual(kwargs['body']['refresh_token'], f'refresh-{self.b.pk}')
                return {'access_token': 'other-band-token'}
            self.assertEqual(kwargs['token'], 'other-band-token')
            if url.endswith('/profile'): return {'emailAddress': message.mailbox}
            if url.endswith('/messages/send'):
                raw = base64.urlsafe_b64decode(kwargs['body']['raw']).decode()
                self.assertIn('From: '+message.mailbox, raw)
                return {'id': 'b-sent', 'threadId': 'b-thread'}
            raise AssertionError('Unexpected request')
        with patch('booking.services.mail_delivery.http_json', side_effect=response): process_batch()
        message.refresh_from_db(); self.assertEqual(message.status, 'sent')
        self.assertFalse(BookingOutreachMessage.objects.filter(group=self.a).exists())

    def test_ai_changed_draft_never_transmitted(self):
        draft = self.draft(); run = new_preview(self.a, self.manager, 'ai_draft', {'draft_id': draft.pk})
        confirm_preview(self.a, self.manager, run.pk)
        BookingDraft.objects.filter(pk=draft.pk).update(body='Edited after consent', revision=2)
        with patch('booking.services.draft_ai.http_json') as http: process_batch()
        http.assert_not_called(); run.refresh_from_db(); self.assertEqual(run.last_error_code, 'ai_scope_changed')

    def test_ai_unknown_result_retains_budget_and_never_replays(self):
        draft = self.draft(); run = new_preview(self.a, self.manager, 'ai_draft', {'draft_id': draft.pk})
        confirm_preview(self.a, self.manager, run.pk)
        with patch('booking.services.draft_ai.http_json', side_effect=[{'input_tokens': 100}, ProviderError('google_transport')]): process_batch()
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'started')
        self.assertEqual(BookingAIUsage.objects.get().result, 'uncertain')
        BookingJob.objects.filter(pk=run.job_id).update(status='processing', lease_until=timezone.now()-timedelta(seconds=1))
        BookingResearchRun.objects.filter(pk=run.pk).update(status='running')
        with patch('booking.services.draft_ai.http_json') as http: process_batch()
        http.assert_not_called()

    def test_ai_input_limit_releases_reservation(self):
        draft = self.draft(); run = new_preview(self.a, self.manager, 'ai_draft', {'draft_id': draft.pk})
        confirm_preview(self.a, self.manager, run.pk)
        with patch('booking.services.draft_ai.http_json', return_value={'input_tokens': 99999}) as http: process_batch()
        self.assertEqual(http.call_count, 1)
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'released')

    def test_header_injection_and_duplicate_recipients_are_rejected(self):
        draft = self.draft()
        url = reverse('booking:draft', args=[draft.pk])
        data = {'to': draft.to, 'subject': 'Pitch\nBcc: other@example.com', 'body': 'Test', 'revision': draft.revision}
        response = self.client.post(url, data)
        self.assertContains(response, 'single-line subject')
        data.update(subject='Pitch', cc=draft.to)
        self.assertContains(self.client.post(url, data), 'Do not repeat recipients')
        self.assertFalse(BookingJob.objects.exists())

    def test_reply_read_cannot_accept_arbitrary_thread_or_other_band_message(self):
        message = self.sent(); self.login(group=self.b)
        self.assertEqual(self.client.post(reverse('booking:mail_read', args=[message.pk, 'gmail_replies']),
            {'confirm': 'gmail_replies', 'thread_id': 'foreign'}).status_code, 404)
        self.login(); queue_read(self.a, self.manager, message, 'gmail_replies')
        result = self.reply_response(message); result['id'] = 'wrong-thread'
        with patch('booking.services.mail_delivery.http_json', side_effect=[{'access_token': 'x'}, {'emailAddress': message.mailbox}, result]): process_batch()
        self.assertFalse(BookingReply.objects.exists())

    def test_google_publication_revoked_booking_role_prevents_remote_write(self):
        from calendar_integrations.services.google_mutations import process_operation
        from unittest.mock import Mock
        self.calendar()
        account = CalendarGoogleAccount.objects.create(group=self.a, is_connected=True,
            granted_scopes=['https://www.googleapis.com/auth/calendar.events'])
        calendar = ConnectedCalendar.objects.create(account=account, external_calendar_id='public-calendar', name='Google',
            write_enabled=True, access_role='writer')
        opportunity = BookingOpportunity.objects.get(group=self.a)
        data = self.publication_data(calendar); data['target'] = f'google:{calendar.pk}'
        self.assertEqual(self.client.post(reverse('booking:publication', args=[opportunity.pk]), data).status_code, 302)
        operation = CalendarWriteOperation.objects.get()
        BookingMembershipRole.objects.filter(membership__group=self.a, membership__user=self.manager).delete()
        client = Mock(); process_operation(operation, client=client)
        client.write.assert_not_called(); client.calendar.assert_not_called()
        operation.refresh_from_db(); self.assertEqual(operation.status, 'failed')

    def test_google_publication_rechecks_booking_before_write(self):
        from calendar_integrations.services.google_mutations import process_operation
        from unittest.mock import Mock
        self.calendar()
        account = CalendarGoogleAccount.objects.create(group=self.a, is_connected=True,
            granted_scopes=['https://www.googleapis.com/auth/calendar.events'])
        calendar = ConnectedCalendar.objects.create(account=account, external_calendar_id='public-calendar', name='Google',
            write_enabled=True, access_role='writer')
        opportunity = BookingOpportunity.objects.get(group=self.a)
        data = self.publication_data(calendar); data['target'] = f'google:{calendar.pk}'
        self.client.post(reverse('booking:publication', args=[opportunity.pk]), data)
        operation = CalendarWriteOperation.objects.get()
        client = Mock(); client.calendar.return_value = {'accessRole': 'writer'}
        def revoke(*args):
            BookingSettings.objects.filter(group=self.a).update(enabled=False)
            raise ProviderError('not_found', 404)
        client.event.side_effect = revoke
        process_operation(operation, client=client)
        client.write.assert_not_called(); operation.refresh_from_db(); self.assertEqual(operation.status, 'failed')

    def test_operator_disable_blocks_previously_approved_sends(self):
        self.approved()
        with override_settings(BOOKING_SEND_DAILY_LIMIT=0), patch('booking.services.mail_delivery.http_json') as http:
            process_batch()
        http.assert_not_called()
        self.assertEqual(BookingOutreachMessage.objects.get().error_code, 'sending_disabled')

    def test_outreach_export_preserves_proof_and_excludes_other_bands(self):
        message = self.approved(); other = self.approved(self.draft(self.b))
        response = self.client.get(reverse('booking:outreach_export'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        rows = response.json()['messages']
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]['content_hash'], message.content_hash)
        self.assertNotIn('raw', rows[0]); self.assertNotIn('refresh_ciphertext', response.content.decode())
        self.assertNotContains(response, other.mailbox)
        self.login(self.viewer)
        self.assertEqual(self.client.get(reverse('booking:outreach_export')).status_code, 404)

    def test_linked_publication_suppresses_only_duplicate_event_overlay(self):
        from datetime import datetime, timezone as dt_timezone
        from .presentation import milestones
        calendar = self.calendar(); opportunity = BookingOpportunity.objects.get(group=self.a)
        url = reverse('booking:publication', args=[opportunity.pk])
        start, end = datetime(2027, 7, 1, tzinfo=dt_timezone.utc), datetime(2027, 8, 1, tzinfo=dt_timezone.utc)
        self.assertTrue(any(e['id'].startswith('booking:event:') for e in milestones(self.manager, self.a, start, end)[1]))
        self.client.post(url, self.publication_data(calendar))
        self.assertFalse(any(e['id'].startswith('booking:event:') for e in milestones(self.manager, self.a, start, end)[1]))
        BookingCalendarPublication.objects.update(linked=False)
        self.assertTrue(any(e['id'].startswith('booking:event:') for e in milestones(self.manager, self.a, start, end)[1]))

    def test_followup_subject_change_cannot_approve_wrong_thread(self):
        message = self.sent()
        followup = compose(self.a, self.editor, message.draft.opportunity, {'contact': message.draft.contact}, message)
        followup.subject = 'Different conversation'
        with self.assertRaisesMessage(ValidationError, 'retain the original subject'):
            snapshot(followup, message.mailbox)
