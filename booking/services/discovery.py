"""Bounded public-page discovery with pinned DNS, robots checks and review only."""
import http.client
import ipaddress
import json
import socket
import ssl
import time
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
from typing import Protocol
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from calendar_integrations.services.google import ProviderError
from booking.models import BookingCandidate
from .records import canonical_url, digest, lock_band, SuppressionIndex
from .imports import live_run

USER_AGENT = 'BandMateResearch/1.0'
MAX_BYTES = 1024 * 1024


class SearchProvider(Protocol):
    def search(self, query: str, *, limit: int) -> list[dict]: ...


class Geocoder(Protocol):
    def locate(self, place: str) -> dict: ...


class AiExtractor(Protocol):
    def extract(self, evidence: str, *, group_id: int) -> list[dict]: ...


class PublicEventSource(Protocol):
    def candidates(self, text: str, url: str) -> list[dict]: ...


def public_address(url):
    url = canonical_url(url)
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.port not in (None, 443):
        raise ProviderError('https_public_urls_only')
    if any(key in parsed.query.casefold() for key in ['token=', 'api_key=', 'apikey=', 'signature=', 'password=', 'access_key=']):
        raise ProviderError('credential_url_rejected')
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        ips = {address[4][0] for address in addresses}
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ProviderError('non_public_address')
    except (socket.gaierror, ValueError, OSError):
        raise ProviderError('dns_unavailable') from None
    return url, parsed, sorted(ips)[0]


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, ip):
        super().__init__(host, port=443, timeout=15, context=ssl.create_default_context())
        self.validated_ip = ip

    def connect(self):
        # Connect directly to the validated address; TLS still checks the original
        # hostname. No proxy inheritance or second DNS lookup after validation.
        raw = socket.create_connection((self.validated_ip, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class SiteFetcher:
    def __init__(self, before_request=lambda url: None):
        self.before_request = before_request

    def _get(self, url, *, limit=MAX_BYTES, rules=None):
        origin = urlsplit(canonical_url(url)).hostname
        for _ in range(4):
            self.before_request(url)
            url, parsed, ip = public_address(url)
            if parsed.hostname != origin:
                raise ProviderError('cross_origin_redirect_requires_review')
            if rules and not rules.can_fetch(USER_AGENT, url):
                raise ProviderError('robots_disallowed')
            connection = PinnedHTTPSConnection(parsed.hostname, ip)
            try:
                connection.request('GET', parsed.path + ('?' + parsed.query if parsed.query else ''),
                    headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/ld+json,application/json,text/plain', 'Accept-Encoding': 'identity'})
                response = connection.getresponse()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location:
                        raise ProviderError('invalid_redirect')
                    url = urljoin(url, location)
                    continue
                if response.status == 404:
                    return url, 404, '', ''
                if response.status != 200:
                    raise ProviderError('source_http_' + str(response.status), response.status)
                content_type = response.getheader('Content-Type', '').split(';', 1)[0].lower()
                if content_type not in ('text/html', 'text/plain', 'application/ld+json', 'application/json'):
                    raise ProviderError('unsupported_source_type')
                if response.getheader('Content-Encoding', 'identity') not in ('', 'identity'):
                    raise ProviderError('compressed_source_rejected')
                length = response.getheader('Content-Length')
                if length and (not length.isdigit() or int(length) > limit):
                    raise ProviderError('source_too_large')
                chunks, size, deadline = [], 0, time.monotonic() + 30
                while True:
                    chunk = response.read(min(16384, limit + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > limit or time.monotonic() > deadline:
                        raise ProviderError('source_limit')
                return url, 200, content_type, b''.join(chunks).decode('utf-8', errors='replace')
            except (OSError, http.client.HTTPException):
                raise ProviderError('source_transport') from None
            finally:
                connection.close()
        raise ProviderError('source_redirect_limit')

    def fetch(self, url):
        url = canonical_url(url)
        parsed = urlsplit(url)
        robots_url = f'{parsed.scheme}://{parsed.netloc}/robots.txt'
        _, status, _, robots = self._get(robots_url, limit=65536)
        rules = RobotFileParser()
        rules.parse(robots.splitlines() if status == 200 else [])
        if rules.crawl_delay(USER_AGENT) or rules.request_rate(USER_AGENT):
            raise ProviderError('robots_rate_policy_requires_manual_review')
        final, status, content_type, text = self._get(url, rules=rules)
        if status != 200:
            raise ProviderError('source_removed', status)
        return final, content_type, text


class SourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title, self.text, self.structured = [], [], []
        self.in_title, self.script, self.ignored = False, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.in_title = True
        if tag in ('script', 'style'):
            self.ignored += 1
        if tag == 'script' and dict(attrs).get('type', '').lower() == 'application/ld+json':
            self.script = []

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        if tag == 'script' and self.script is not None:
            if len(self.structured) < 20:
                try:
                    self.structured.append(json.loads(''.join(self.script)))
                except (ValueError, RecursionError):
                    pass
            self.script = None
        if tag in ('script', 'style'):
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        elif not self.ignored:
            if self.in_title:
                self.title.append(data)
            self.text.append(data)


def structured_events(data, depth=0):
    if depth > 8:
        return []
    if isinstance(data, list):
        return [row for value in data[:100] for row in structured_events(value, depth + 1)][:30]
    if not isinstance(data, dict):
        return []
    types = data.get('@type', [])
    types = [types] if isinstance(types, str) else types
    if isinstance(types, list) and any(value in ('Event', 'MusicEvent', 'Festival') for value in types):
        return [data]
    return structured_events(data.get('@graph', []), depth + 1)


def extract_candidates(text, url, content_type='text/html'):
    parser = SourceParser()
    if content_type in ('application/ld+json', 'application/json'):
        try:
            parser.structured = [json.loads(text)]
        except (ValueError, RecursionError):
            raise ProviderError('invalid_source_json') from None
    else:
        parser.feed(text)
    excerpt = ' '.join(' '.join(parser.text).split())[:2000]
    rows = []
    for item in structured_events(parser.structured)[:20]:
        if not isinstance(item.get('name'), str):
            continue
        proposed = {'name': item['name'][:200], 'website': url}
        start = item.get('startDate')
        try:
            event_date = date.fromisoformat(start[:10])
            proposed.update(event_date=event_date.isoformat(), event_year=event_date.year)
        except (TypeError, ValueError):
            pass
        location = item.get('location', {})
        if isinstance(location, dict):
            address = location.get('address', {})
            if isinstance(address, dict) and isinstance(address.get('addressLocality'), str):
                proposed['city'] = address['addressLocality'][:160]
        organizer = item.get('organizer', {})
        if isinstance(organizer, dict) and isinstance(organizer.get('email'), str):
            try:
                validate_email(organizer['email'])
                proposed['contact_email'] = organizer['email'].lower()
            except ValidationError:
                pass
        # Event startDate is never an application deadline. No date forecasting
        # or instructions from page text can change the approved request scope.
        # Preserve the structured facts behind proposed dates and contacts even
        # when the page's visible prose does not repeat them.
        evidence = {key: item[key] for key in ('name', 'startDate', 'location', 'organizer') if key in item}
        rows.append((proposed, (json.dumps(evidence, ensure_ascii=False) + '\n' + excerpt)[:2000]))
    if not rows:
        rows.append(({'name': (' '.join(parser.title).strip() or urlsplit(url).hostname)[:200], 'website': url}, excerpt))
    return rows


def discover(job, fetcher=None):
    run = live_run(job)
    source = run.scope['url']
    if SuppressionIndex(job.group).blocks_source(url=source):
        raise ProviderError('source_suppressed')
    def check_url(url):
        live_run(job)
        if SuppressionIndex(job.group).blocks_source(url=url):
            raise ProviderError('source_suppressed')
    fetcher = fetcher or SiteFetcher(before_request=check_url)
    final_url, content_type, text = fetcher.fetch(source)
    rows = extract_candidates(text, final_url, content_type)
    with transaction.atomic():
        lock_band(job.group)
        run = live_run(job)
        index = SuppressionIndex(job.group)
        if index.blocks_source(url=final_url):
            raise ProviderError('source_suppressed')
        for proposed, excerpt in rows:
            if index.blocks_source(url=final_url, emails=[proposed['contact_email']] if proposed.get('contact_email') else [],
                    name=proposed['name'], city=proposed.get('city', '')):
                continue
            # Repeated identical evidence deduplicates; changed evidence returns
            # to review without overwriting a human-maintained booking record.
            source_key = digest(final_url + '|' + json.dumps(proposed, sort_keys=True) + '|' + excerpt)
            BookingCandidate.objects.get_or_create(group=job.group, source_key=source_key, defaults={
                'run': run, 'source_type': 'url', 'source_ref': final_url, 'title': proposed['name'],
                'proposed': proposed, 'excerpt': excerpt, 'content_hash': digest(text)})
        run.processed = len(rows)
        run.save(update_fields=['processed'])
    return True
