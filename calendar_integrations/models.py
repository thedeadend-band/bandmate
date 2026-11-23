from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError('Choose an IANA timezone, for example America/Los_Angeles.')


class GroupCalendarRole(models.Model):
    class Role(models.TextChoices):
        MANAGER = 'calendar_manager', 'Calendar manager'
        EDITOR = 'calendar_editor', 'Calendar editor'
        VIEWER = 'calendar_viewer', 'Calendar viewer'

    membership = models.OneToOneField('player.GroupMembership', on_delete=models.CASCADE,
                                      related_name='calendar_role')
    role = models.CharField(max_length=24, choices=Role.choices)

    class Meta:
        constraints = [models.CheckConstraint(
            check=models.Q(role__in=['calendar_manager', 'calendar_editor', 'calendar_viewer']),
            name='calendar_valid_role')]


class CalendarGoogleAccount(models.Model):
    """Calendar-only grant; refresh credentials are authenticated ciphertext."""
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    email = models.EmailField()
    granted_scopes = models.JSONField(default=list, blank=True)
    is_connected = models.BooleanField(default=False)
    refresh_ciphertext = models.TextField(blank=True)
    connected_by = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    last_error_code = models.CharField(max_length=80, blank=True)
    calendar_list = models.JSONField(default=list, blank=True)
    generation = models.PositiveIntegerField(default=1)
    calendar_list_requested = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'email'],
                                               name='calendar_group_account')]


class CalendarFields(models.Model):
    name = models.CharField(max_length=120)
    color = models.CharField(max_length=7, default='#4a9eff', validators=[
        RegexValidator(r'^#[0-9a-fA-F]{6}$', 'Use a six-digit hex color.')])
    timezone = models.CharField(max_length=64, default='UTC', validators=[validate_timezone])
    is_enabled = models.BooleanField(default=True)
    write_enabled = models.BooleanField(default=False)
    policy_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['name', 'pk']


class BandmateLocalCalendar(CalendarFields):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE,
                              related_name='local_calendars')


class ConnectedCalendar(CalendarFields):
    # Tenant derives from the account FK; no second, potentially conflicting group FK.
    account = models.ForeignKey(CalendarGoogleAccount, on_delete=models.CASCADE,
                                related_name='calendars')
    external_calendar_id = models.CharField(max_length=1024)
    access_role = models.CharField(max_length=40, blank=True)
    sync_token = models.TextField(blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_full_sync_at = models.DateTimeField(null=True, blank=True)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)

    class Meta(CalendarFields.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=['account', 'external_calendar_id'],
                                               name='calendar_account_external_id')]


class EventFields(models.Model):
    title = models.CharField(max_length=500)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_all_day = models.BooleanField(default=False)
    timezone = models.CharField(max_length=64, default='UTC', validators=[validate_timezone])
    location = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    is_deleted = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['starts_at', 'pk']


class LocalEvent(EventFields):
    calendar = models.ForeignKey(BandmateLocalCalendar, on_delete=models.CASCADE, related_name='events')
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL, related_name='+')
    updated_by = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL, related_name='+')

    class Meta(EventFields.Meta):
        abstract = False
        constraints = [models.CheckConstraint(check=models.Q(ends_at__gt=models.F('starts_at')), name='local_event_positive_duration')]


class GoogleEvent(EventFields):
    calendar = models.ForeignKey(ConnectedCalendar, on_delete=models.CASCADE, related_name='events')
    external_id = models.CharField(max_length=1024)
    etag = models.CharField(max_length=512)
    ical_uid = models.CharField(max_length=1024, blank=True)
    recurring_id = models.CharField(max_length=1024, blank=True)
    is_recurring = models.BooleanField(default=False)
    is_private = models.BooleanField(default=False)
    mutable = models.BooleanField(default=False)
    html_link = models.URLField(max_length=2000, blank=True)

    class Meta(EventFields.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=['calendar', 'external_id'], name='google_calendar_event_identity')]


class CalendarWriteOperation(models.Model):
    """Durable reviewed intent; Google writes are executed by the sync command."""
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    actor = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    key = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    local_calendar = models.ForeignKey(BandmateLocalCalendar, null=True, on_delete=models.PROTECT)
    google_calendar = models.ForeignKey(ConnectedCalendar, null=True, on_delete=models.PROTECT)
    event_id = models.CharField(max_length=1024, blank=True)
    action = models.CharField(max_length=10, choices=[(x, x) for x in ['create', 'update', 'delete', 'enable']])
    expected_version = models.CharField(max_length=512, blank=True)
    payload = models.JSONField(default=dict)
    before_payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default='pending', choices=[(x, x) for x in ['pending', 'processing', 'succeeded', 'failed', 'ambiguous', 'conflict']])
    error_code = models.CharField(max_length=80, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    retry_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['group', 'key'], name='calendar_operation_key'),
            models.CheckConstraint(check=(models.Q(local_calendar__isnull=False, google_calendar__isnull=True) | models.Q(local_calendar__isnull=True, google_calendar__isnull=False)), name='calendar_operation_one_source'),
        ]
        indexes = [models.Index(fields=['status', 'retry_at'])]


class CalendarMutationAudit(models.Model):
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    actor = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    operation = models.ForeignKey(CalendarWriteOperation, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=32)
    outcome = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)


class GoogleOAuthState(models.Model):
    account_generations = models.JSONField(default=dict)
    digest = models.CharField(max_length=64, unique=True)
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE)
    session_digest = models.CharField(max_length=64)
    verifier_ciphertext = models.TextField()
    target_calendar = models.ForeignKey(ConnectedCalendar, null=True, on_delete=models.CASCADE)
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
