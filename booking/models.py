from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


class BookingSettings(models.Model):
    group = models.OneToOneField('player.Group', on_delete=models.CASCADE,
                                 related_name='booking_settings')
    enabled = models.BooleanField(default=False)
    search_center = models.CharField(max_length=200, blank=True)
    radius_miles = models.PositiveIntegerField(
        default=100, validators=[MinValueValidator(1), MaxValueValidator(10000)])
    genres = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class BookingMembershipRole(models.Model):
    class Role(models.TextChoices):
        MANAGER = 'booking_manager', 'Booking manager'
        EDITOR = 'booking_editor', 'Booking editor'
        VIEWER = 'booking_viewer', 'Booking viewer'

    membership = models.OneToOneField('player.GroupMembership', on_delete=models.CASCADE,
                                      related_name='booking_role')
    role = models.CharField(max_length=24, choices=Role.choices)

    class Meta:
        constraints = [models.CheckConstraint(
            check=models.Q(role__in=['booking_manager', 'booking_editor', 'booking_viewer']),
            name='booking_valid_role')]
