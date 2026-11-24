import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlparse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_POST, require_GET
from bandmate.credential_vault import seal, unseal, VaultUnavailable
from calendar_integrations.services.google import ProviderError
from player.group_permissions import audit
from .connection_views import manager
from .models import GmailOAuthState, BookingGmailConnection, BookingSettings
from .services.connections import exchange_gmail_code, gmail_profile, GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@login_required
@require_POST
@sensitive_variables()
def connect(request):
    manager(request)
    sending = request.POST.get('consent') == 'enable_reviewed_send'
    if request.POST.get('consent') not in ('read_profile', 'enable_reviewed_send'):
        return HttpResponseBadRequest('Acknowledge the Gmail read-only connection before continuing.')
    if not (settings.GMAIL_OAUTH_CLIENT_ID and settings.GMAIL_OAUTH_CLIENT_SECRET and
            urlparse(settings.GMAIL_OAUTH_REDIRECT_URI).scheme == 'https'):
        return HttpResponseBadRequest('Gmail needs its separate OAuth client and HTTPS callback configuration.')
    nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    try:
        encrypted = seal(request.active_group.pk, 'gmail-pkce', verifier)
    except VaultUnavailable:
        return HttpResponseBadRequest('The credential vault must be configured before connecting Gmail.')
    connection, _ = BookingGmailConnection.objects.get_or_create(group=request.active_group)
    if sending and not connection.is_connected:
        return HttpResponseBadRequest('Connect and verify the read-only mailbox before enabling reviewed sending.')
    GmailOAuthState.objects.filter(expires_at__lt=timezone.now()).delete()
    GmailOAuthState.objects.create(digest=digest(nonce), group=request.active_group, user=request.user,
        session_digest=digest(request.session.session_key or ''), verifier_ciphertext=encrypted,
        connection_generation=connection.generation, request_send=sending,
        expected_mailbox=connection.mailbox if sending else '', expires_at=timezone.now() + timedelta(minutes=10))
    audit(request.active_group, request.user, 'booking.gmail.consent_requested')
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': settings.GMAIL_OAUTH_CLIENT_ID, 'redirect_uri': settings.GMAIL_OAUTH_REDIRECT_URI,
        'response_type': 'code', 'scope': GMAIL_READ_SCOPE + (' ' + GMAIL_SEND_SCOPE if sending else ''), 'state': nonce, 'access_type': 'offline',
        'prompt': 'consent', 'code_challenge_method': 'S256',
        'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()}))


@login_required
@require_GET
@sensitive_variables()
def callback(request):
    # Consume the nonce even if the active band/role changed during consent.
    state = GmailOAuthState.objects.filter(digest=digest(request.GET.get('state', '')),
        user=request.user, session_digest=digest(request.session.session_key or ''), consumed=False,
        expires_at__gt=timezone.now()).first()
    if not state or not GmailOAuthState.objects.filter(pk=state.pk, consumed=False).update(consumed=True):
        return HttpResponseBadRequest('Invalid, expired or already used Gmail authorization.')
    try:
        manager(request)
        if not request.active_group or state.group_id != request.active_group.pk:
            raise ProviderError('band_changed')
        if request.GET.get('error') or not request.GET.get('code'):
            raise ProviderError('consent_not_completed')
        if not BookingGmailConnection.objects.filter(group_id=state.group_id, generation=state.connection_generation).exists():
            raise ProviderError('connection_changed')
        tokens = exchange_gmail_code(request.GET['code'], unseal(state.group_id, 'gmail-pkce', state.verifier_ciphertext))
        scopes = tokens.get('scope', '').split()
        if GMAIL_READ_SCOPE not in scopes or not tokens.get('refresh_token'):
            raise ProviderError('offline_read_consent_required')
        if state.request_send and GMAIL_SEND_SCOPE not in scopes:
            raise ProviderError('send_consent_required')
        # An old broader grant cannot silently elevate a read-only reconnect.
        scopes = [scope for scope in scopes if scope != GMAIL_SEND_SCOPE or state.request_send]
        manager(request)
        if not BookingGmailConnection.objects.filter(group_id=state.group_id, generation=state.connection_generation).exists():
            raise ProviderError('connection_changed')
        profile = gmail_profile(tokens['access_token'])
        mailbox = profile.get('emailAddress', '').strip().lower()
        validate_email(mailbox)
        if state.expected_mailbox and mailbox != state.expected_mailbox:
            raise ProviderError('mailbox_identity_changed')
        encrypted = seal(state.group_id, 'gmail-refresh', tokens['refresh_token'])
        with transaction.atomic():
            BookingSettings.objects.filter(group_id=state.group_id).update(updated_at=F('updated_at'))
            manager(request)
            connection = BookingGmailConnection.objects.get(group_id=state.group_id)
            if connection.generation != state.connection_generation:
                raise ProviderError('connection_changed')
            # Database unique mailbox constraint handles simultaneous callbacks.
            # Never expose which other band has a conflicting mailbox.
            connection.mailbox, connection.refresh_ciphertext = mailbox, encrypted
            connection.granted_scopes, connection.is_connected = scopes, True
            connection.connected_by, connection.connected_at = request.user, timezone.now()
            connection.tested_at, connection.last_error_code = timezone.now(), ''
            connection.history_id = ''  # Phase 3 establishes its own import checkpoint.
            connection.history_scope_hash = ''
            connection.generation += 1
            connection.save()
            audit(state.group, request.user, 'booking.gmail.connected', connection.pk)
        return redirect('booking:connections')
    except Exception:
        audit(state.group, request.user, 'booking.gmail.authorization_failed')
        return HttpResponseBadRequest('Gmail authorization did not complete. Check the active band, read-only consent, mailbox availability and vault configuration, then reconnect.')
    finally:
        GmailOAuthState.objects.filter(pk=state.pk).update(verifier_ciphertext='')


@login_required
@require_POST
def disconnect(request):
    manager(request)
    with transaction.atomic():
        BookingSettings.objects.filter(group=request.active_group).update(updated_at=F('updated_at'))
        manager(request)
        BookingGmailConnection.objects.filter(group=request.active_group).update(mailbox=None,
            refresh_ciphertext='', granted_scopes=[], is_connected=False, history_id='', history_scope_hash='',
            generation=F('generation') + 1, tested_at=None, last_error_code='')
        GmailOAuthState.objects.filter(group=request.active_group, consumed=False).update(consumed=True, verifier_ciphertext='')
        audit(request.active_group, request.user, 'booking.gmail.disconnected')
    return redirect('booking:connections')
