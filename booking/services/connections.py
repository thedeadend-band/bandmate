"""Per-band provider factories. Connection tests never perform model inference."""
from django.conf import settings
from bandmate.credential_vault import unseal
from calendar_integrations.services.google import http_json, ProviderError
from booking.models import BookingOpenAICredential, BookingGmailConnection
from booking.permissions import booking_role
from booking.models import BookingMembershipRole

GMAIL_READ_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
GMAIL_SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send'


def authorized(job):
    if not job.actor_id or booking_role(job.actor, job.group) != BookingMembershipRole.Role.MANAGER:
        raise ProviderError('authorization_changed')


def test_openai(job):
    authorized(job)
    credential = BookingOpenAICredential.objects.filter(group_id=job.group_id,
        generation=job.credential_generation).exclude(key_ciphertext='').first()
    if not credential:
        raise ProviderError('credential_changed')
    key = unseal(job.group_id, 'booking-openai', credential.key_ciphertext)
    authorized(job)
    if not BookingOpenAICredential.objects.filter(pk=credential.pk, generation=job.credential_generation).exists():
        raise ProviderError('credential_changed')
    try:
        data = http_json('https://api.openai.com/v1/models', token=key)
    except ProviderError as exc:
        raise ProviderError('openai_http_' + str(exc.status) if exc.status else 'openai_transport', exc.status) from None
    rows = data.get('data')
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in rows):
        raise ProviderError('invalid_model_response')
    return sorted({row['id'] for row in rows if row['id'] in settings.BOOKING_OPENAI_MODELS})


def exchange_gmail_code(code, verifier):
    return http_json('https://oauth2.googleapis.com/token', method='POST', form=True, body={
        'client_id': settings.GMAIL_OAUTH_CLIENT_ID, 'client_secret': settings.GMAIL_OAUTH_CLIENT_SECRET,
        'redirect_uri': settings.GMAIL_OAUTH_REDIRECT_URI, 'code': code, 'code_verifier': verifier,
        'grant_type': 'authorization_code'})


def gmail_profile(token):
    return http_json('https://gmail.googleapis.com/gmail/v1/users/me/profile', token=token)


def test_gmail(job):
    authorized(job)
    connection = BookingGmailConnection.objects.filter(group_id=job.group_id,
        generation=job.credential_generation, is_connected=True).first()
    if not connection or GMAIL_READ_SCOPE not in connection.granted_scopes:
        raise ProviderError('gmail_disconnected')
    try:
        tokens = http_json('https://oauth2.googleapis.com/token', method='POST', form=True, body={
            'client_id': settings.GMAIL_OAUTH_CLIENT_ID, 'client_secret': settings.GMAIL_OAUTH_CLIENT_SECRET,
            'refresh_token': unseal(job.group_id, 'gmail-refresh', connection.refresh_ciphertext),
            'grant_type': 'refresh_token'})
        if not isinstance(tokens.get('access_token'), str):
            raise ProviderError('invalid_token_response')
        authorized(job)
        if not BookingGmailConnection.objects.filter(pk=connection.pk,
                generation=job.credential_generation, is_connected=True).exists():
            raise ProviderError('credential_changed')
        profile = gmail_profile(tokens['access_token'])
        if profile.get('emailAddress', '').strip().lower() != connection.mailbox:
            raise ProviderError('mailbox_identity_changed', 401)
        return profile
    except ProviderError as exc:
        if exc.status in (400, 401):
            BookingGmailConnection.objects.filter(pk=connection.pk, generation=job.credential_generation).update(
                is_connected=False, last_error_code='reauthorization_required')
        raise
