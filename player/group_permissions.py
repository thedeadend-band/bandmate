"""Shared, fresh membership checks. Site staff never bypass band roles."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import GroupAccessAudit, GroupMembership


def member_of(user, group):
    return bool(user.is_authenticated and user.is_active and group and
                GroupMembership.objects.filter(group=group, user=user).exists())


def role_for(model, user, group):
    if not user.is_authenticated or not user.is_active or not group:
        return None
    return model.objects.filter(membership__group=group, membership__user=user
                                ).values_list('role', flat=True).first()


def require_role(model, user, group, roles):
    if role_for(model, user, group) not in roles:
        raise Http404


def audit(group, actor, action, target='', previous='', new=''):
    GroupAccessAudit.objects.create(
        group=group, actor=actor, action=action, target=str(target),
        previous_value=str(previous), new_value=str(new))


@transaction.atomic
def change_role(model, group, actor, membership_id, role):
    """Manager-controlled roles; cascade membership removal handles revocation."""
    require_role(model, actor, group, [model.Role.MANAGER])
    membership = get_object_or_404(GroupMembership, pk=membership_id, group=group)
    if role and role not in model.Role.values:
        raise ValidationError('Choose a valid role.')
    current = model.objects.filter(membership=membership).first()
    old = current.role if current else ''
    if (old == model.Role.MANAGER and role != old and
            not model.objects.filter(membership__group=group, membership__user__is_active=True, role=old
                                     ).exclude(pk=current.pk).exists()):
        raise ValidationError('Assign another manager before removing the last manager.')
    if role:
        model.objects.update_or_create(membership=membership, defaults={'role': role})
    elif current:
        current.delete()
    if old != role:
        audit(group, actor, model._meta.label_lower + '.role', membership.pk, old, role)
