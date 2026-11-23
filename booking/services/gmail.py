"""Read-only Gmail adapter for explicitly scoped imports; no send methods."""
from urllib.parse import urlencode, quote
from django.conf import settings
from bandmate.credential_vault import unseal
from calendar_integrations.services.google import http_json, ProviderError
from booking.models import BookingGmailConnection, BookingResearchRun
from .connections import authorized, GMAIL_READ_SCOPE


class GmailReader:
    def __init__(self, job):
        self.job, self.token, self.profile_data = job, None, None

    def check(self):
        authorized(self.job)
        if not BookingResearchRun.objects.filter(job=self.job, group_id=self.job.group_id, status='running').exists():
            raise ProviderError('import_cancelled')
        connection = BookingGmailConnection.objects.filter(group_id=self.job.group_id,
            generation=self.job.credential_generation, is_connected=True).first()
        if not connection or GMAIL_READ_SCOPE not in connection.granted_scopes:
            raise ProviderError('gmail_disconnected')
        return connection

    def request(self, path, **params):
        connection = self.check()
        try:
            if not self.token:
                result = http_json('https://oauth2.googleapis.com/token', method='POST', form=True, body={
                    'client_id': settings.GMAIL_OAUTH_CLIENT_ID, 'client_secret': settings.GMAIL_OAUTH_CLIENT_SECRET,
                    'refresh_token': unseal(self.job.group_id, 'gmail-refresh', connection.refresh_ciphertext),
                    'grant_type': 'refresh_token'})
                self.token = result.get('access_token')
                if not isinstance(self.token, str) or not self.token:
                    raise ProviderError('invalid_token_response')
            self.check()
            url = 'https://gmail.googleapis.com/gmail/v1/users/me/' + path
            if params:
                url += '?' + urlencode(params, doseq=True)
            return http_json(url, token=self.token)
        except ProviderError as exc:
            if exc.status in (400, 401) and (not self.token or exc.status == 401):
                BookingGmailConnection.objects.filter(pk=connection.pk, generation=self.job.credential_generation).update(
                    is_connected=False, last_error_code='reauthorization_required')
            raise

    def profile(self):
        data = self.request('profile')
        if data.get('emailAddress', '').strip().lower() != self.check().mailbox:
            raise ProviderError('mailbox_identity_changed')
        if not isinstance(data.get('historyId'), str):
            raise ProviderError('invalid_history_cursor')
        self.profile_data = data
        return data

    def messages(self, query, page=''):
        return self.request('messages', q=query, maxResults=5, includeSpamTrash='false', **({'pageToken': page} if page else {}))

    def message(self, message_id):
        return self.request('messages/' + quote(message_id, safe=''), format='metadata',
            metadataHeaders=['From', 'To', 'Subject', 'Date'])

    def history(self, start, page=''):
        return self.request('history', startHistoryId=start, historyTypes=['messageAdded', 'messageDeleted'], maxResults=100,
            **({'pageToken': page} if page else {}))
