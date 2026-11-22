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
    """Connection metadata only. Encrypted credential onboarding lands in Phase 1."""
    group = models.ForeignKey('player.Group', on_delete=models.CASCADE)
    email = models.EmailField()
    granted_scopes = models.JSONField(default=list, blank=True)
    is_connected = models.BooleanField(default=False)

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
    last_error_code = models.CharField(max_length=80, blank=True)

    class Meta(CalendarFields.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=['account', 'external_calendar_id'],
                                               name='calendar_account_external_id')]
