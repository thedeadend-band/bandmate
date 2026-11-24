import json
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from bandmate.credential_vault import seal
from calendar_integrations.services.google import ProviderError
from player.models import Group, GroupMembership
from .models import (BookingSettings, BookingMembershipRole, BookingOpenAICredential, BookingResearchRun,
    BookingCandidate, BookingJob, BookingAIUsage, BookingBudgetReservation, BookingProviderRequest,
    BookingProviderGate, BookingVenue, BookingOpportunity, BookingConversationReference, BookingSuppression,
    BookingArtistReference, BookingArtistGig)
from .services.records import digest
from .services.research_jobs import new_preview, confirm_preview
from .services.jobs import process_batch
from .services.providers import BraveSearch, OpenCageGeocoder
from .services.extraction import validate_facts
from .services.imports import purge_messages


@override_settings(BOOKING_SEARCH_PROVIDER='brave', BOOKING_BRAVE_KEY='test-public-search',
    BOOKING_GEOCODER='opencage', BOOKING_OPENCAGE_KEY='test-geocoding',
    BOOKING_PROVIDER_DAILY_LIMIT=10, BOOKING_PROVIDER_BAND_DAILY_LIMIT=5,
    BOOKING_PROVIDER_MIN_INTERVAL=1, BOOKING_OPENAI_MODELS=('test-model',),
    BOOKING_AI_PRICING={'test-model': {'input': '1', 'output': '2', 'version': 'test-pricing'}})
class DiscoveryExtensionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a, cls.b = Group.objects.create(name='Extension A'), Group.objects.create(name='Private B')
        cls.manager = User.objects.create_user('extension-manager')
        cls.editor = User.objects.create_user('extension-editor')
        for group in [cls.a, cls.b]:
            BookingSettings.objects.create(group=group, enabled=True, ai_enabled=True, ai_model='test-model',
                monthly_budget_usd=10, daily_request_limit=10, max_output_tokens=1000)
            for user, role in [(cls.manager, 'booking_manager'), (cls.editor, 'booking_editor')]:
                member = GroupMembership.objects.create(group=group, user=user)
                BookingMembershipRole.objects.create(membership=member, role=role)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'key'
        path.write_bytes(Fernet.generate_key())
        path.chmod(0o600)
        setting = override_settings(BANDMATE_SECRET_KEY_FILE=str(path))
        setting.enable()
        self.addCleanup(setting.disable)
        for group in [self.a, self.b]:
            BookingOpenAICredential.objects.create(group=group, key_ciphertext=seal(group.pk, 'booking-openai', 'key-for-' + str(group.pk)),
                fingerprint=digest(str(group.pk)), test_status='succeeded', available_models=['test-model'])
        self.login()

    def login(self, user=None, group=None):
        self.client.force_login(user or self.manager)
        session = self.client.session
        session['active_group_id'] = (group or self.a).pk
        session.save()

    def candidate(self, group=None, source_type='url', excerpt='The 2027 concert applications close 2026-12-01 in Portland.'):
        group = group or self.a
        run = BookingResearchRun.objects.create(group=group, actor=self.manager, kind='web_discovery',
            scope={}, scope_hash='test', status='succeeded', expires_at=timezone.now()+timedelta(days=2))
        return BookingCandidate.objects.create(group=group, run=run, source_key=digest(str(run.pk)),
            source_type=source_type, source_ref='https://venue.example/', title='Public lead',
            proposed={'name': 'Public lead', 'website': 'https://venue.example/'}, excerpt=excerpt, content_hash=digest(excerpt))

    def queued(self, kind, scope, group=None):
        group = group or self.a
        return confirm_preview(group, self.manager, new_preview(group, self.manager, kind, scope).pk)

    def ai_run(self, candidate=None):
        return self.queued('ai_extract', {'candidate_id': (candidate or self.candidate()).pk})

    def ai_response(self, facts=None, **changes):
        facts = facts if facts is not None else [{'field': 'verified_deadline', 'value': '2026-12-01',
            'quote': 'The 2027 concert applications close 2026-12-01', 'cycle_year': 2027}]
        return {'status': 'completed', 'usage': {'input_tokens': 250, 'output_tokens': 100},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps({'facts': facts})}]}], **changes}

    def test_search_preview_discloses_scope_without_network_or_jobs(self):
        with patch('booking.services.providers.provider_json') as http:
            response = self.client.post(reverse('booking:research_preview', args=['web_search']),
                {'query': 'Portland rock cover bands 2027 concerts', 'category': 'artists', 'limit': 5})
        self.assertEqual(response.status_code, 302)
        http.assert_not_called()
        self.assertFalse(BookingJob.objects.exists())
        page = self.client.get(response.url)
        self.assertContains(page, 'Brave Search scope')
        self.assertContains(page, 'Portland rock cover bands')
        self.login(self.editor)
        self.assertEqual(self.client.post(reverse('booking:research_preview', args=['web_search']), {}).status_code, 404)

    @override_settings(BOOKING_BRAVE_KEY='')
    def test_unconfigured_provider_fails_closed(self):
        with self.assertRaisesMessage(ProviderError, 'provider_not_configured'):
            new_preview(self.a, self.manager, 'web_search', {'query': 'public'})

    def test_search_results_are_unverified_band_local_and_never_fetched(self):
        run = self.queued('web_search', {'query': 'public concerts', 'category': 'artists', 'limit': 5})
        with (patch('booking.services.providers.provider_json', return_value={'web': {'results': [
                {'title': 'Artist gigs', 'url': 'https://venue.example/gigs', 'description': 'Public dates'}]}}) as http,
                patch('booking.services.discovery.SiteFetcher.fetch') as fetch):
            process_batch()
        fetch.assert_not_called()
        candidate = BookingCandidate.objects.get(group=self.a)
        self.assertEqual(candidate.source_type, 'search')
        self.assertNotIn('verified_deadline', candidate.proposed)
        self.assertEqual(http.call_args.kwargs['headers']['X-Subscription-Token'], 'test-public-search')
        self.assertFalse(BookingCandidate.objects.filter(group=self.b).exists())
        run.refresh_from_db()
        self.assertEqual(run.status, 'succeeded')

    def test_public_provider_global_and_band_quotas(self):
        self.queued('web_search', {'query': 'public', 'category': 'opportunities', 'limit': 1})
        with patch('booking.services.providers.BraveSearch.search', return_value=[]):
            process_batch()
        with override_settings(BOOKING_PROVIDER_BAND_DAILY_LIMIT=1):
            run = self.queued('web_search', {'query': 'public', 'category': 'opportunities', 'limit': 1})
            with patch('booking.services.providers.BraveSearch.search') as search:
                process_batch()
            search.assert_not_called()
            run.refresh_from_db()
            self.assertEqual(run.last_error_code, 'provider_daily_quota')
        self.assertEqual(BookingProviderRequest.objects.count(), 1)

    def test_rate_gate_is_shared_across_bands(self):
        self.queued('web_search', {'query': 'public', 'category': 'artists', 'limit': 1})
        with patch('booking.services.providers.BraveSearch.search', return_value=[]):
            process_batch()
        run = self.queued('web_search', {'query': 'public', 'category': 'artists', 'limit': 1}, group=self.b)
        with patch('booking.services.providers.BraveSearch.search') as search:
            process_batch()
        search.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.last_error_code, 'provider_rate_limited')
        self.assertEqual(run.status, 'queued')

    def test_revocation_during_search_discards_results(self):
        run = self.queued('web_search', {'query': 'public', 'category': 'artists', 'limit': 1})
        def revoke(*args, **kwargs):
            BookingSettings.objects.filter(group=self.a).update(enabled=False)
            return [{'title': 'Do not store', 'url': 'https://venue.example/', 'excerpt': 'Public data'}]
        with patch('booking.services.providers.BraveSearch.search', side_effect=revoke):
            process_batch()
        self.assertFalse(BookingCandidate.objects.exists())
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')

    def location_candidate(self, venue):
        config = BookingSettings.objects.get(group=self.a)
        run = self.queued('geocode', {'query': 'Public venue Portland', 'venue_id': venue.pk if venue else None,
            'venue_revision': venue.revision if venue else None, 'center_revision': config.updated_at.isoformat(), 'ai_processing': False})
        with patch('booking.services.providers.OpenCageGeocoder.locate', return_value=[{
                'name': 'Portland, Oregon', 'latitude': '45.520000', 'longitude': '-122.670000', 'precision': 3}]):
            process_batch()
        return BookingCandidate.objects.get(run=run)

    def test_geocode_only_changes_coordinates_after_explicit_review(self):
        venue = BookingVenue.objects.create(group=self.a, name='Public venue')
        candidate = self.location_candidate(venue)
        self.assertContains(self.client.get(reverse('booking:candidate', args=[candidate.pk])), 'Public venue')
        venue.refresh_from_db()
        self.assertIsNone(venue.latitude)
        url = reverse('booking:apply_location', args=[candidate.pk])
        self.assertEqual(self.client.post(url, {}).status_code, 400)
        self.assertEqual(self.client.post(url, {'confirm': 'reviewed_location'}).status_code, 302)
        venue.refresh_from_db()
        self.assertEqual(venue.latitude, Decimal('45.52'))
        self.assertIn('precision 3/10', venue.location_evidence)
        self.assertEqual(self.client.post(url, {'confirm': 'reviewed_location'}).status_code, 404)

    def test_geocode_rejects_cross_band_stale_and_suppressed_venues(self):
        venue = BookingVenue.objects.create(group=self.a, name='Public venue')
        candidate = self.location_candidate(venue)
        self.login(group=self.b)
        self.assertEqual(self.client.post(reverse('booking:apply_location', args=[candidate.pk]), {'confirm': 'reviewed_location'}).status_code, 404)
        self.login()
        BookingVenue.objects.filter(pk=venue.pk).update(revision=2)
        self.assertEqual(self.client.post(reverse('booking:apply_location', args=[candidate.pk]), {'confirm': 'reviewed_location'}).status_code, 400)

    def test_geocode_center_stale_settings_are_not_overwritten(self):
        candidate = self.location_candidate(None)
        config = BookingSettings.objects.get(group=self.a)
        config.search_center = 'Manually changed center'
        config.save()
        self.assertEqual(self.client.post(reverse('booking:apply_location', args=[candidate.pk]), {'confirm': 'reviewed_location'}).status_code, 400)
        config.refresh_from_db()
        self.assertEqual(config.search_center, 'Manually changed center')

    def test_geocoder_validates_coordinates_and_sends_no_record_flag(self):
        with patch('booking.services.providers.provider_json', return_value={'status': {'code': 200}, 'results': [
                {'geometry': {'lat': 91, 'lng': 0}, 'confidence': 2, 'formatted': 'Invalid'},
                {'geometry': {'lat': 45.5, 'lng': -122.5}, 'confidence': 3, 'formatted': 'Public town'}]}) as http:
            rows = OpenCageGeocoder().locate('Public town')
        self.assertEqual(len(rows), 1)
        self.assertIn('no_record=1', http.call_args.args[0])

    def test_ai_preview_requires_distinct_disclosure_consent(self):
        candidate = self.candidate()
        with patch('booking.services.extraction.http_json') as http:
            response = self.client.post(reverse('booking:extraction_preview', args=[candidate.pk]))
        http.assert_not_called()
        run = BookingResearchRun.objects.get(kind='ai_extract')
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(response.url), candidate.excerpt)
        url = reverse('booking:research_confirm', args=[run.pk])
        self.assertEqual(self.client.post(url, {'consent': 'scoped_read'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'consent': 'analyze_evidence', 'maximum_usd': '0'}).status_code, 302)
        run.refresh_from_db()
        self.assertGreater(Decimal(run.scope['maximum_usd']), 0)

    def test_ai_success_has_budget_usage_quotes_and_no_record_mutation(self):
        candidate = self.candidate()
        run = self.ai_run(candidate)
        with patch('booking.services.extraction.http_json', side_effect=[{'input_tokens': 250}, self.ai_response()]) as http:
            process_batch()
        candidate.refresh_from_db(); run.refresh_from_db()
        self.assertEqual(run.status, 'succeeded', run.last_error_code)
        self.assertEqual(candidate.analysis['facts'][0]['verification'], 'unverified')
        self.assertNotIn('verified_deadline', candidate.proposed)
        self.assertFalse(BookingOpportunity.objects.exists())
        usage = BookingAIUsage.objects.get()
        self.assertEqual(usage.task_type, 'evidence_extraction')
        self.assertEqual(usage.estimated_usd, Decimal('0.000450'))
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'settled')
        self.assertEqual(http.call_args.kwargs['token'], 'key-for-' + str(self.a.pk))
        self.assertFalse(http.call_args.kwargs['body']['store'])
        self.assertEqual(http.call_args.kwargs['body']['tools'], [])

    def test_ai_two_bands_use_only_their_own_key(self):
        candidate = self.candidate(group=self.b)
        self.queued('ai_extract', {'candidate_id': candidate.pk}, group=self.b)
        with patch('booking.services.extraction.http_json', side_effect=[{'input_tokens': 250}, self.ai_response()]) as http:
            process_batch()
        self.assertEqual(http.call_args.kwargs['token'], 'key-for-' + str(self.b.pk))
        self.assertFalse(BookingAIUsage.objects.filter(group=self.a).exists())

    def test_ai_changed_evidence_never_transmitted(self):
        candidate = self.candidate()
        run = self.ai_run(candidate)
        candidate.excerpt = 'Changed private evidence'
        candidate.save(update_fields=['excerpt'])
        with patch('booking.services.extraction.http_json') as http:
            process_batch()
        http.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.last_error_code, 'ai_scope_changed')

    def test_ai_budget_off_pricing_missing_and_cross_band_are_blocked(self):
        other = self.candidate(group=self.b)
        self.assertEqual(self.client.post(reverse('booking:extraction_preview', args=[other.pk])).status_code, 404)
        candidate = self.candidate()
        with override_settings(BOOKING_AI_PRICING={}):
            self.assertEqual(self.client.post(reverse('booking:extraction_preview', args=[candidate.pk])).status_code, 400)
        BookingSettings.objects.filter(group=self.a).update(monthly_budget_usd=0)
        self.assertEqual(self.client.post(reverse('booking:extraction_preview', args=[candidate.pk])).status_code, 400)

    def test_ai_input_limit_releases_budget_without_inference(self):
        run = self.ai_run()
        with patch('booking.services.extraction.http_json', return_value={'input_tokens': 9000}) as http:
            process_batch()
        self.assertEqual(http.call_count, 1)
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'released')
        run.refresh_from_db()
        self.assertEqual(run.last_error_code, 'ai_input_limit')

    def test_ai_revocation_after_count_stops_paid_request(self):
        self.ai_run()
        def revoke(*args, **kwargs):
            BookingSettings.objects.filter(group=self.a).update(ai_enabled=False)
            return {'input_tokens': 250}
        with patch('booking.services.extraction.http_json', side_effect=revoke) as http:
            process_batch()
        self.assertEqual(http.call_count, 1)
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'released')

    def test_ai_ambiguous_response_is_charged_and_never_replayed(self):
        run = self.ai_run()
        with patch('booking.services.extraction.http_json', side_effect=[{'input_tokens': 250}, ProviderError('google_transport')]):
            process_batch()
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'started')
        self.assertEqual(BookingAIUsage.objects.get().result, 'uncertain')
        # Simulate an expired worker lease, not a new user-approved run.
        BookingResearchRun.objects.filter(pk=run.pk).update(status='running')
        BookingJob.objects.filter(pk=run.job_id).update(status='processing', lease_until=timezone.now()-timedelta(seconds=1))
        with patch('booking.services.extraction.http_json') as http:
            process_batch()
        http.assert_not_called()
        self.assertEqual(BookingAIUsage.objects.count(), 1)

    def test_ai_unsubstantiated_quote_is_rejected_but_usage_recorded(self):
        candidate = self.candidate()
        run = self.ai_run(candidate)
        facts = [{'field': 'contact_email', 'value': 'invented@example.com', 'quote': 'invented@example.com', 'cycle_year': None}]
        with patch('booking.services.extraction.http_json', side_effect=[{'input_tokens': 250}, self.ai_response(facts)]):
            process_batch()
        candidate.refresh_from_db(); run.refresh_from_db()
        self.assertEqual(candidate.analysis, {})
        self.assertEqual(run.last_error_code, 'unsupported_extraction')
        self.assertEqual(BookingAIUsage.objects.get().result, 'unsupported_extraction')

    def test_ai_incomplete_output_records_cost_without_suggestions(self):
        run = self.ai_run()
        with patch('booking.services.extraction.http_json', side_effect=[{'input_tokens': 250}, self.ai_response(status='incomplete')]):
            process_batch()
        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'settled')

    def test_ai_malformed_output_records_failure_with_known_usage(self):
        candidate = self.candidate()
        run = self.ai_run(candidate)
        with patch('booking.services.extraction.http_json', side_effect=[{'input_tokens': 250}, self.ai_response(output=[None])]):
            process_batch()
        run.refresh_from_db(); candidate.refresh_from_db()
        self.assertEqual(run.last_error_code, 'invalid_extraction')
        self.assertEqual(candidate.analysis, {})
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'settled')
        self.assertEqual(BookingAIUsage.objects.get().result, 'invalid_extraction')

    def test_ai_malformed_usage_retains_uncertain_reservation(self):
        run = self.ai_run()
        with patch('booking.services.extraction.http_json', side_effect=[{'input_tokens': 250}, self.ai_response(usage=None)]):
            process_batch()
        run.refresh_from_db()
        self.assertEqual(run.last_error_code, 'ai_usage_unknown')
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'started')
        self.assertEqual(BookingAIUsage.objects.get().result, 'uncertain')

    def test_gmail_expiry_and_removal_cover_derived_ai_analysis(self):
        candidate = self.candidate(source_type='gmail')
        reference = BookingConversationReference.objects.create(group=self.a, source_key=candidate.source_key,
            expires_at=timezone.now()+timedelta(days=1), excerpt=candidate.excerpt)
        self.ai_run(candidate)
        def remove_after_response(*args, **kwargs):
            purge_messages(self.a)
            return self.ai_response()
        calls = 0
        def response(*args, **kwargs):
            nonlocal calls
            calls += 1
            return {'input_tokens': 250} if calls == 1 else remove_after_response()
        with patch('booking.services.extraction.http_json', side_effect=response):
            process_batch()
        candidate.refresh_from_db()
        self.assertEqual(candidate.status, 'purged')
        self.assertEqual(candidate.analysis, {})
        self.assertEqual(BookingBudgetReservation.objects.get().status, 'settled')

    def test_ai_suppression_blocks_processing(self):
        candidate = self.candidate()
        self.ai_run(candidate)
        BookingSuppression.objects.create(group=self.a, kind='domain', value='venue.example', reason='Do not research')
        with patch('booking.services.extraction.http_json') as http:
            process_batch()
        http.assert_not_called()

    def test_application_cycle_and_date_quotes_are_checked(self):
        with self.assertRaises(ProviderError):
            validate_facts({'facts': [{'field': 'verified_deadline', 'value': '2026-12-01',
                'quote': 'Closes 2026-12-01', 'cycle_year': 2027}]}, 'Closes 2026-12-01')

    def test_public_artist_gig_review_creates_references_not_band_booking(self):
        candidate = self.candidate()
        candidate.proposed.update(artist_name='Public Cover Band', venue_name='Public Stage', event_date='2027-07-10', city='Portland')
        candidate.save(update_fields=['proposed'])
        url = reverse('booking:save_artist_gig', args=[candidate.pk])
        self.assertEqual(self.client.post(url, {}).status_code, 400)
        response = self.client.post(url, {'confirm': 'public_gig_reviewed'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BookingArtistReference.objects.get().name, 'Public Cover Band')
        gig = BookingArtistGig.objects.get()
        self.assertEqual(gig.venue.name, 'Public Stage')
        self.assertEqual(gig.venue.website, '')  # Artist listing is not a venue's official website.
        self.assertFalse(BookingOpportunity.objects.exists())
        self.assertContains(self.client.get(response.url), 'not a booking by your band')
        self.assertEqual(self.client.post(url, {'confirm': 'public_gig_reviewed'}).status_code, 404)

    def test_search_source_preview_is_scoped_and_never_fetches(self):
        candidate = self.candidate(source_type='search')
        with patch('booking.services.discovery.SiteFetcher.fetch') as fetch:
            response = self.client.post(reverse('booking:source_preview', args=[candidate.pk]))
        fetch.assert_not_called()
        self.assertEqual(response.status_code, 302)
        run = BookingResearchRun.objects.get(status='preview')
        self.assertEqual(run.scope['url'], candidate.source_ref)
        self.login(group=self.b)
        self.assertEqual(self.client.post(reverse('booking:source_preview', args=[candidate.pk])).status_code, 404)

    def test_schedule_frequency_is_validated_and_ai_cannot_be_scheduled(self):
        from .models import BookingResearchSchedule
        candidate = self.candidate()
        url = reverse('booking:research_schedule', args=[candidate.run_id])
        data = {'enable': 'yes', 'consent': 'weekly_same_url', 'interval_days': '1'}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(BookingResearchSchedule.objects.get().interval_days, 1)
        self.assertEqual(self.client.post(url, dict(data, interval_days='0')).status_code, 400)
        run = self.ai_run(candidate)
        self.assertEqual(self.client.post(reverse('booking:research_schedule', args=[run.pk]), data).status_code, 404)

    def test_changed_key_generation_and_pricing_stop_queued_ai(self):
        run = self.ai_run()
        BookingOpenAICredential.objects.filter(group=self.a).update(generation=2)
        with patch('booking.services.extraction.http_json') as http:
            process_batch()
        http.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.last_error_code, 'ai_scope_changed')

    def test_public_provider_transport_never_exposes_secret_url(self):
        from urllib.error import URLError
        from .services.providers import provider_json
        with patch('booking.services.providers.request.build_opener') as opener:
            opener.return_value.open.side_effect = URLError('secret-key-in-query')
            with self.assertRaises(ProviderError) as raised:
                provider_json('https://api.opencagedata.com/geocode/v1/json?key=secret-key-in-query')
        self.assertEqual(str(raised.exception), 'public_provider_transport')

    def test_geocode_suppression_before_worker_prevents_disclosure(self):
        venue = BookingVenue.objects.create(group=self.a, name='Do not geocode')
        self.queued('geocode', {'query': 'Private pending place', 'venue_id': venue.pk, 'venue_revision': venue.revision})
        BookingSuppression.objects.create(group=self.a, kind='identity', value=str(venue.identity), reason='Restricted')
        with patch('booking.services.providers.OpenCageGeocoder.locate') as locate:
            process_batch()
        locate.assert_not_called()

    def test_embedded_artist_and_venue_names_follow_suppression(self):
        from .services.records import SuppressionIndex, remember_aliases
        venue = BookingVenue.objects.create(group=self.a, name='Restricted stage', city='Portland')
        remember_aliases(venue)
        BookingSuppression.objects.create(group=self.a, kind='identity', value=str(venue.identity), reason='Restricted')
        self.assertTrue(SuppressionIndex(self.a).blocks_proposal({'name': 'New artist gig',
            'venue_name': 'Restricted stage', 'city': 'Portland', 'website': 'https://artist.example/'}))

    def test_ai_checks_all_imported_correspondents_for_new_opt_outs(self):
        candidate = self.candidate(source_type='gmail')
        BookingConversationReference.objects.create(group=self.a, source_key=candidate.source_key,
            expires_at=timezone.now()+timedelta(days=1), correspondents=['other@example.com', 'restricted@example.com'])
        self.ai_run(candidate)
        BookingSuppression.objects.create(group=self.a, kind='email', value='restricted@example.com',
            reason='Opted out after import', active=False, do_not_contact=True)
        with patch('booking.services.extraction.http_json') as http:
            process_batch()
        http.assert_not_called()

    def test_transparent_genre_history_and_application_scoring(self):
        from .services.ranking import assess
        config = BookingSettings.objects.get(group=self.a)
        config.genres = 'Rock, Alternative'
        row = BookingOpportunity(group=self.a, name='Known venue', event_year=2027, genres='rock',
            prior_booking=True, response_status='responded', history_evidence='Played in 2025; booker replied to the 2026 inquiry.')
        score = assess(row, config)
        self.assertEqual(score['score'], 60)
        self.assertTrue(any('previous booking' in reason for reason in score['reasons']))
        row.stage = 'closed'
        self.assertEqual(assess(row, config)['score'], 10)
