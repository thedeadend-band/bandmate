"""Small fixed-endpoint Google adapter. Never log tokens, URLs or provider bodies."""
import json
from http.client import HTTPException
from urllib import request, error, parse
from django.conf import settings
from bandmate.credential_vault import unseal
from calendar_integrations.models import CalendarGoogleAccount, ConnectedCalendar


class ProviderError(Exception):
    def __init__(self, code, status=0):
        self.code, self.status = code, status
        super().__init__(code)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_json(url, *, method='GET', body=None, token=None, etag=None, form=False):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if etag:
        headers['If-Match'] = etag
    data = None
    if body is not None:
        data = (parse.urlencode(body) if form else json.dumps(body)).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.build_opener(NoRedirect).open(req, timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ProviderError('response_too_large')
            result = json.loads(raw) if raw else {}
            if not isinstance(result, dict):
                raise ProviderError('invalid_provider_response')
            return result
    except error.HTTPError as exc:
        # No request URLs, access codes, response bodies or headers in exceptions.
        raise ProviderError('google_http_' + str(exc.code), exc.code) from None
    except (error.URLError, TimeoutError, OSError, ValueError, HTTPException):
        raise ProviderError('google_transport') from None


def exchange_code(code, verifier):
    return http_json('https://oauth2.googleapis.com/token', method='POST', form=True, body={
        'client_id': settings.GOOGLE_CALENDAR_CLIENT_ID, 'client_secret': settings.GOOGLE_CALENDAR_CLIENT_SECRET,
        'redirect_uri': settings.GOOGLE_CALENDAR_REDIRECT_URI, 'code': code, 'code_verifier': verifier,
        'grant_type': 'authorization_code'})


def identity(token):
    return http_json('https://openidconnect.googleapis.com/v1/userinfo', token=token)


class GoogleCalendarClient:
    def __init__(self, account):
        self.account_id = account.pk
        self.group_id = account.group_id
        self.generation = account.generation
        self.token = None

    def _account(self):
        account = CalendarGoogleAccount.objects.filter(pk=self.account_id, group_id=self.group_id,
            generation=self.generation, is_connected=True).first()
        if not account or not account.refresh_ciphertext:
            raise ProviderError('calendar_disconnected', 401)
        return account

    def _request(self, path, *, method='GET', body=None, etag=None, params=None):
        account = self._account()
        if not self.token:
            try:
                tokens = http_json('https://oauth2.googleapis.com/token', method='POST', form=True, body={
                    'client_id': settings.GOOGLE_CALENDAR_CLIENT_ID,
                    'client_secret': settings.GOOGLE_CALENDAR_CLIENT_SECRET,
                    'refresh_token': unseal(self.group_id, 'google-calendar-refresh', account.refresh_ciphertext),
                    'grant_type': 'refresh_token'})
                self.token = tokens['access_token']
            except ProviderError as exc:
                if exc.status in (400, 401):
                    self.disconnect()
                raise
            except (KeyError, TypeError):
                raise ProviderError('invalid_token_response') from None
        self._account()  # Recheck disconnect after refresh, before provider request.
        url = 'https://www.googleapis.com/calendar/v3/' + path
        if params:
            url += '?' + parse.urlencode(params)
        try:
            return http_json(url, method=method, body=body, token=self.token, etag=etag)
        except ProviderError as exc:
            if exc.status == 401:
                self.disconnect()
            raise

    def disconnect(self):
        CalendarGoogleAccount.objects.filter(pk=self.account_id, generation=self.generation).update(
            is_connected=False, last_error_code='reauthorization_required')
        ConnectedCalendar.objects.filter(account_id=self.account_id).update(write_enabled=False)

    def list_calendars(self):
        items, page = [], None
        for _ in range(settings.GOOGLE_CALENDAR_MAX_PAGES):
            data = self._request('users/me/calendarList', params={'maxResults': 250, **({'pageToken': page} if page else {})})
            items.extend(data.get('items', []))
            page = data.get('nextPageToken')
            if not page:
                return items
        raise ProviderError('calendar_page_limit')

    def calendar(self, calendar_id):
        return self._request('users/me/calendarList/' + parse.quote(calendar_id, safe=''))

    def events(self, calendar_id, *, sync_token='', page_token='', window=None):
        # Single-event expansion keeps recurring instances visible, but read-only.
        params = {'maxResults': 250, 'singleEvents': 'true', 'showDeleted': 'true'}
        if sync_token:
            params['syncToken'] = sync_token
        if page_token:
            params['pageToken'] = page_token
        if not sync_token and window:
            params.update(timeMin=window[0], timeMax=window[1])
        return self._request('calendars/' + parse.quote(calendar_id, safe='') + '/events', params=params)

    def event(self, calendar_id, event_id):
        return self._request(self._path(calendar_id, event_id))

    def write(self, calendar_id, event_id, action, payload, etag=''):
        path = self._path(calendar_id, '' if action == 'create' else event_id)
        return self._request(path, method={'create': 'POST', 'update': 'PATCH', 'delete': 'DELETE'}[action],
            body=payload if action != 'delete' else None, etag=etag or None, params={'sendUpdates': 'none'})

    @staticmethod
    def _path(calendar_id, event_id):
        path = 'calendars/' + parse.quote(calendar_id, safe='') + '/events'
        return path + ('/' + parse.quote(event_id, safe='') if event_id else '')
