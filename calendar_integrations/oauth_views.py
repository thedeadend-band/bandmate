import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlparse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.debug import sensitive_variables
from bandmate.credential_vault import seal, unseal, VaultUnavailable
from player.group_permissions import audit
from .permissions import require_calendar_manager, GOOGLE_READ_SCOPES, GOOGLE_EVENT_WRITE_SCOPE, GOOGLE_WRITER_ROLES
from .models import GoogleOAuthState, CalendarGoogleAccount, ConnectedCalendar
from .services.google import exchange_code, identity, ProviderError


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def configured():
    return bool(settings.GOOGLE_CALENDAR_CLIENT_ID and settings.GOOGLE_CALENDAR_CLIENT_SECRET and
                urlparse(settings.GOOGLE_CALENDAR_REDIRECT_URI).scheme == 'https')


@login_required
@require_POST
@sensitive_variables()
def connect(request):
    require_calendar_manager(request.user, request.active_group)
    if not configured():
        return HttpResponseBadRequest('Calendar OAuth needs an HTTPS callback and server client configuration.')
    target = None
    if request.POST.get('calendar'):
        if not request.POST['calendar'].isdigit():
            raise Http404
        target = get_object_or_404(ConnectedCalendar, pk=request.POST['calendar'], account__group=request.active_group)
    nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    try:
        encrypted = seal(request.active_group.pk, 'calendar-pkce', verifier)
    except VaultUnavailable:
        return HttpResponseBadRequest('The credential vault must be configured before connecting Google.')
    GoogleOAuthState.objects.filter(expires_at__lt=timezone.now()).delete()
    GoogleOAuthState.objects.create(digest=digest(nonce), group=request.active_group, user=request.user,
        session_digest=digest(request.session.session_key or ''), verifier_ciphertext=encrypted,
        account_generations={str(pk): generation for pk, generation in CalendarGoogleAccount.objects.filter(
            group=request.active_group).values_list('pk', 'generation')},
        target_calendar=target, expires_at=timezone.now() + timedelta(minutes=10))
    scopes = list(GOOGLE_READ_SCOPES) + ['openid', 'email']
    if target:
        scopes.append(GOOGLE_EVENT_WRITE_SCOPE)
    params = {'client_id': settings.GOOGLE_CALENDAR_CLIENT_ID,
        'redirect_uri': settings.GOOGLE_CALENDAR_REDIRECT_URI, 'response_type': 'code',
        'scope': ' '.join(scopes), 'state': nonce, 'access_type': 'offline',
        'prompt': 'consent', 'code_challenge_method': 'S256',
        'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()}
    if target:
        params['login_hint'] = target.account.email
    audit(request.active_group, request.user, 'calendar.oauth.requested', target.pk if target else '', new='write' if target else 'read')
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params))


@login_required
@require_GET
@sensitive_variables()
def callback(request):
    # Deliberately reject a switched group/session rather than guessing OAuth intent.
    state = GoogleOAuthState.objects.filter(digest=digest(request.GET.get('state', '')),
        user=request.user, session_digest=digest(request.session.session_key or ''),
        consumed=False, expires_at__gt=timezone.now()).select_related('target_calendar__account').first()
    if not state or not GoogleOAuthState.objects.filter(pk=state.pk, consumed=False).update(consumed=True):
        return HttpResponseBadRequest('Invalid, expired or already used Calendar authorization.')
    try:
        require_calendar_manager(request.user, request.active_group)
        if not request.active_group or request.active_group.pk != state.group_id:
            raise ProviderError('band_changed')
        if request.GET.get('error') or not request.GET.get('code'):
            raise ProviderError('consent_not_completed')
        tokens = exchange_code(request.GET['code'], unseal(state.group_id, 'calendar-pkce', state.verifier_ciphertext))
        scopes = tokens.get('scope', '').split()
        if not set(GOOGLE_READ_SCOPES).issubset(scopes):
            raise ProviderError('read_scopes_not_granted')
        require_calendar_manager(request.user, request.active_group)
        if not GoogleOAuthState.objects.filter(pk=state.pk).exclude(verifier_ciphertext='').exists():
            raise ProviderError('connection_changed')
        who = identity(tokens['access_token'])
        if not who.get('email_verified') or not who.get('email'):
            raise ProviderError('unverified_account')
        email = who['email'].strip().lower()
        if state.target_calendar and email != state.target_calendar.account.email.lower():
            raise ProviderError('wrong_google_account')
        if not tokens.get('refresh_token'):
            raise ProviderError('offline_consent_required')
        encrypted = seal(state.group_id, 'google-calendar-refresh', tokens['refresh_token'])
        with transaction.atomic():
            require_calendar_manager(request.user, request.active_group)
            if not GoogleOAuthState.objects.filter(pk=state.pk).exclude(verifier_ciphertext='').exists():
                raise ProviderError('connection_changed')
            account, created = CalendarGoogleAccount.objects.get_or_create(group=state.group, email=email)
            if not created and state.account_generations.get(str(account.pk)) != account.generation:
                raise ProviderError('connection_changed')
            account.refresh_ciphertext = encrypted
            account.granted_scopes = scopes
            account.is_connected = True
            account.connected_by = request.user
            account.last_error_code = ''
            account.calendar_list_requested = True
            account.generation += 1
            account.save()
            # Reauthorization does not silently enable selected sources. A separate
            # explicit switch uses the refreshed CalendarList after the next sync.
            if GOOGLE_EVENT_WRITE_SCOPE not in scopes:
                account.calendars.update(write_enabled=False)
            audit(state.group, request.user, 'calendar.oauth.connected', account.pk)
        return redirect('calendar_integrations:google_accounts')
    except Exception:
        # OAuth callback query contains an authorization code: no raw error/trace logging.
        audit(state.group, request.user, 'calendar.oauth.failed')
        return HttpResponseBadRequest('Calendar authorization did not complete. Check consent, the chosen Google account and vault configuration, then reconnect.')
    finally:
        GoogleOAuthState.objects.filter(pk=state.pk).update(verifier_ciphertext='')


@login_required
@require_GET
def accounts(request):
    require_calendar_manager(request.user, request.active_group)
    return render(request, 'calendar_integrations/google_accounts.html', {
        'accounts': CalendarGoogleAccount.objects.filter(group=request.active_group),
        'oauth_configured': configured(), 'nav_active': 'calendar'})


@login_required
@require_POST
def refresh_list(request, account_id):
    require_calendar_manager(request.user, request.active_group)
    account = get_object_or_404(CalendarGoogleAccount, pk=account_id, group=request.active_group, is_connected=True)
    account.calendar_list_requested = True
    account.save(update_fields=['calendar_list_requested'])
    return redirect('calendar_integrations:google_accounts')


@login_required
@require_POST
def select_calendar(request, account_id):
    require_calendar_manager(request.user, request.active_group)
    account = get_object_or_404(CalendarGoogleAccount, pk=account_id, group=request.active_group, is_connected=True)
    chosen = next((c for c in account.calendar_list if c.get('id') == request.POST.get('external_id')), None)
    if not chosen:
        raise Http404
    # Warn every selection, avoiding disclosure of other bands' account connections.
    if request.POST.get('acknowledge_shared') != 'yes':
        return HttpResponseBadRequest('Acknowledge that changes to a shared Google calendar are visible to its other users and bands.')
    from .models import validate_timezone
    from django.core.exceptions import ValidationError
    zone = chosen.get('timeZone') or 'UTC'
    try:
        validate_timezone(zone)
    except ValidationError:
        zone = 'UTC'
    calendar, created = ConnectedCalendar.objects.get_or_create(account=account,
        external_calendar_id=chosen['id'], defaults={'name': (chosen.get('summary') or chosen['id'])[:120],
            'timezone': zone, 'access_role': chosen.get('accessRole', '')})
    audit(request.active_group, request.user, 'calendar.google.selected', calendar.pk)
    return redirect('calendar_integrations:sources')


@login_required
@require_POST
def disconnect(request, account_id):
    require_calendar_manager(request.user, request.active_group)
    with transaction.atomic():
        account = get_object_or_404(CalendarGoogleAccount, pk=account_id, group=request.active_group)
        account.is_connected = False
        account.refresh_ciphertext = ''
        account.granted_scopes = []
        account.calendar_list = []
        account.calendar_list_requested = False
        account.generation += 1
        account.save()
        account.calendars.update(write_enabled=False)
        GoogleOAuthState.objects.filter(group=request.active_group).update(consumed=True, verifier_ciphertext='')
        audit(request.active_group, request.user, 'calendar.oauth.disconnected', account.pk)
    return redirect('calendar_integrations:google_accounts')
