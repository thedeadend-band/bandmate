"""Staff may enable a feature and explicitly bootstrap its first band manager.

    Private Booking/Calendar pages still require membership and the relevant role.
"""
from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from booking.models import BookingMembershipRole, BookingSettings
from calendar_integrations.models import GroupCalendarRole
from .group_permissions import audit
from .models import Group, GroupMembership


class GroupFeaturesForm(forms.Form):
    booking_enabled = forms.BooleanField(required=False, label='Enable Booking for this band')
    booking_manager = forms.ModelChoiceField(
        queryset=GroupMembership.objects.none(), required=False, label='First Booking manager')
    calendar_manager = forms.ModelChoiceField(
        queryset=GroupMembership.objects.none(), required=False, label='First Calendar manager')

    def __init__(self, *args, group, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        for field, model in [('booking_manager', BookingMembershipRole),
                             ('calendar_manager', GroupCalendarRole)]:
            if model.objects.filter(membership__group=group, membership__user__is_active=True,
                                    role=model.Role.MANAGER).exists():
                del self.fields[field]
            else:
                self.fields[field].queryset = group.memberships.filter(
                    user__is_active=True).select_related('user')

    def clean(self):
        data = super().clean()
        if (data.get('booking_enabled') and 'booking_manager' in self.fields
                and not data.get('booking_manager')):
            self.add_error('booking_manager', 'Designate a current band member to manage Booking.')
        return data


@login_required
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def group_features(request, group_id):
    if not request.user.is_staff:
        raise Http404
    group = get_object_or_404(Group, pk=group_id)
    enabled = BookingSettings.objects.filter(group=group, enabled=True).exists()
    form = GroupFeaturesForm(
        request.POST if request.method == 'POST' else None,
        group=group, initial={'booking_enabled': enabled})
    if request.method == 'POST' and form.is_valid():
        config, _ = BookingSettings.objects.get_or_create(group=group)
        config.enabled = form.cleaned_data['booking_enabled']
        config.save(update_fields=['enabled', 'updated_at'])
        if enabled != config.enabled:
            audit(group, request.user, 'booking.enabled', group.pk, enabled, config.enabled)
        for field, model in [('booking_manager', BookingMembershipRole),
                             ('calendar_manager', GroupCalendarRole)]:
            membership = form.cleaned_data.get(field)
            if membership:
                current = model.objects.filter(membership=membership).first()
                old = current.role if current else ''
                model.objects.update_or_create(membership=membership,
                                               defaults={'role': model.Role.MANAGER})
                audit(group, request.user, model._meta.label_lower + '.bootstrap',
                      membership.pk, old, model.Role.MANAGER)
        return redirect('admin_group_features', group_id=group.pk)
    return render(request, 'player/admin_group_features.html', {
        'group': group, 'form': form, 'nav_active': 'settings',
    })
