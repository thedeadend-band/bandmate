"""Fixed-endpoint public-data adapters with persisted global/per-band quotas."""
import html
import json
import math
from datetime import timedelta
from urllib import request, error, parse
from http.client import HTTPException
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from calendar_integrations.services.google import NoRedirect, ProviderError
from booking.models import BookingProviderGate, BookingProviderRequest, BookingCandidate
from .imports import live_run
from .records import digest, canonical_url, SuppressionIndex, lock_band


def configured(provider):
    if provider == 'brave':
        enabled = settings.BOOKING_SEARCH_PROVIDER == 'brave' and settings.BOOKING_BRAVE_KEY
    else:
        enabled = settings.BOOKING_GEOCODER == 'opencage' and settings.BOOKING_OPENCAGE_KEY
    if not enabled or settings.BOOKING_PROVIDER_DAILY_LIMIT <= 0 or settings.BOOKING_PROVIDER_BAND_DAILY_LIMIT <= 0:
        raise ProviderError('provider_not_configured')


@transaction.atomic
def claim_request(job, provider):
    configured(provider)
    gate, _ = BookingProviderGate.objects.get_or_create(provider=provider)
    BookingProviderGate.objects.filter(pk=gate.pk).update(last_started_at=F('last_started_at'))
    gate.refresh_from_db()
    live_run(job)
    now = timezone.now()
    rows = BookingProviderRequest.objects.filter(provider=provider, created_at__gte=now.replace(hour=0, minute=0, second=0, microsecond=0))
    if rows.count() >= settings.BOOKING_PROVIDER_DAILY_LIMIT or rows.filter(group=job.group).count() >= settings.BOOKING_PROVIDER_BAND_DAILY_LIMIT:
        raise ProviderError('provider_daily_quota')
    if gate.last_started_at and now - gate.last_started_at < timedelta(seconds=settings.BOOKING_PROVIDER_MIN_INTERVAL):
        raise ProviderError('provider_rate_limited', 429)
    gate.last_started_at = now
    gate.save(update_fields=['last_started_at'])
    return BookingProviderRequest.objects.create(group=job.group, job=job, provider=provider)


def provider_json(url, *, headers=None):
    # URLs are constructed only by the fixed adapters below. No redirects or
    # proxy inheritance; credentials, queries and response bodies never logged.
    req = request.Request(url, headers={'Accept': 'application/json', 'Accept-Encoding': 'identity', **(headers or {})})
    try:
        with request.build_opener(request.ProxyHandler({}), NoRedirect).open(req, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ProviderError('provider_response_too_large')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ProviderError('invalid_provider_response')
            return data
    except error.HTTPError as exc:
        raise ProviderError('public_provider_http_' + str(exc.code), exc.code) from None
    except (error.URLError, TimeoutError, OSError, ValueError, HTTPException):
        raise ProviderError('public_provider_transport') from None


class BraveSearch:
    def search(self, query, *, limit=10):
        configured('brave')
        data = provider_json('https://api.search.brave.com/res/v1/web/search?' + parse.urlencode({
            'q': query, 'count': min(10, limit), 'text_decorations': 'false', 'safesearch': 'moderate'}),
            headers={'X-Subscription-Token': settings.BOOKING_BRAVE_KEY})
        rows = data.get('web', {}).get('results', [])
        if not isinstance(rows, list):
            raise ProviderError('invalid_search_results')
        results = []
        for row in rows[:10]:
            if not isinstance(row, dict) or not isinstance(row.get('url'), str):
                continue
            try:
                url = canonical_url(row['url'])
            except Exception:
                continue
            # Search listings are evidence only: never fetch result URLs here.
            if not url.startswith('https://') or len(url) > 1000:
                continue
            results.append({'url': url, 'title': html.unescape(str(row.get('title', 'Public lead')))[:200],
                'excerpt': html.unescape(str(row.get('description', '')))[:2000]})
        return results


class OpenCageGeocoder:
    def locate(self, place):
        configured('opencage')
        data = provider_json('https://api.opencagedata.com/geocode/v1/json?' + parse.urlencode({
            'q': place, 'key': settings.BOOKING_OPENCAGE_KEY, 'limit': 5, 'no_annotations': 1, 'no_record': 1}))
        if data.get('status', {}).get('code') != 200 or not isinstance(data.get('results'), list):
            raise ProviderError('invalid_geocode_results')
        rows = []
        for row in data['results'][:5]:
            try:
                lat, lon = float(row['geometry']['lat']), float(row['geometry']['lng'])
                precision = row['confidence']
                if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180 or type(precision) is not int or not 0 <= precision <= 10:
                    continue
                rows.append({'name': str(row['formatted'])[:200], 'latitude': f'{lat:.6f}',
                    'longitude': f'{lon:.6f}', 'precision': precision})
            except (KeyError, TypeError, ValueError):
                continue
        return rows


def research_public(job):
    run = live_run(job)
    provider = 'brave' if job.kind == 'web_search' else 'opencage'
    attempt = claim_request(job, provider)
    try:
        # Recheck after quota claim, immediately before transmission.
        run = live_run(job)
        if job.kind == 'geocode' and run.scope.get('venue_id'):
            from booking.models import BookingVenue
            venue = BookingVenue.objects.filter(group=job.group, pk=run.scope['venue_id'],
                revision=run.scope['venue_revision'], is_archived=False).first()
            if not venue or SuppressionIndex(job.group).blocked(venue, sending=True):
                raise ProviderError('location_changed_or_suppressed')
        rows = BraveSearch().search(run.scope['query'], limit=run.scope['limit']) if provider == 'brave' else OpenCageGeocoder().locate(run.scope['query'])
        with transaction.atomic():
            lock_band(job.group)
            run = live_run(job)
            index = SuppressionIndex(job.group)
            for row in rows:
                if provider == 'brave':
                    if index.blocks_source(url=row['url'], name=row['title']):
                        continue
                    proposed = {'name': row['title'], 'website': row['url'], 'research_category': run.scope['category']}
                    source, excerpt, source_type = row['url'], row['excerpt'], 'search'
                else:
                    proposed = dict(row, venue_id=run.scope.get('venue_id'), venue_revision=run.scope.get('venue_revision'))
                    source, excerpt, source_type = 'https://opencagedata.com/', json.dumps(row, sort_keys=True), 'geocode'
                key = digest(provider + '|' + json.dumps(proposed, sort_keys=True) + '|' + excerpt)
                BookingCandidate.objects.get_or_create(group=job.group, source_key=key, defaults={
                    'run': run, 'source_type': source_type, 'source_ref': source, 'title': proposed['name'],
                    'proposed': proposed, 'excerpt': excerpt, 'content_hash': digest(excerpt)})
            run.processed = len(rows)
            run.save(update_fields=['processed'])
        attempt.result = 'succeeded'
    except ProviderError as exc:
        attempt.result = exc.code
        raise
    finally:
        attempt.save(update_fields=['result'])
    return True
