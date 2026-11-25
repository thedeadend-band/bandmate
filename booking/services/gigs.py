"""Private gig preparation. No provider calls or automatic calendar writes."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from player.models import Setlist
from player.group_permissions import audit
from booking.models import BookingOpportunity, BookingGigPlan, BookingRehearsal, BookingResource, BookingMembershipRole
from booking.permissions import require_booking
from .records import EDIT_ROLES, SuppressionIndex, lock_band


def ready(opportunity):
    return (not opportunity.is_archived and opportunity.stage == 'confirmed'
        and not SuppressionIndex(opportunity.group).blocked(opportunity))


def require_ready(opportunity):
    if not ready(opportunity):
        raise ValidationError('Gig preparation requires an active, unsuppressed confirmed opportunity.')


def same_band_setlist(group, value):
    if value is None:
        return None
    return get_object_or_404(Setlist, pk=value.pk, group=group)


@transaction.atomic
def save_plan(group, actor, opportunity_id, data, *, create_setlist=False):
    lock_band(group)
    require_booking(actor, group, EDIT_ROLES)
    opportunity = get_object_or_404(BookingOpportunity, group=group, pk=opportunity_id)
    require_ready(opportunity)
    plan = BookingGigPlan.objects.filter(group=group, opportunity=opportunity).first()
    if data['revision'] != (plan.revision if plan else 0):
        raise ValidationError('Gig preparation changed. Reload before saving.')
    plan = plan or BookingGigPlan(group=group, opportunity=opportunity, revision=0)
    if create_setlist:
        if plan.setlist_id:
            raise ValidationError('Unlink the current setlist before creating a new one.')
        plan.setlist = Setlist.objects.create(group=group, owner=actor,
            name=opportunity.name[:255], date=opportunity.event_date)
    else:
        plan.setlist = same_band_setlist(group, data.get('setlist'))
    plan.notes = data.get('notes', '')
    plan.revision += 1
    plan.full_clean()
    plan.save()
    audit(group, actor, 'booking.gig.setlist_created' if create_setlist else 'booking.gig.saved', plan.identity)
    return plan


@transaction.atomic
def save_resource(group, actor, resource_id, data):
    lock_band(group)
    require_booking(actor, group, [BookingMembershipRole.Role.MANAGER])
    row = get_object_or_404(BookingResource, group=group, pk=resource_id) if resource_id else BookingResource(group=group, revision=0)
    if row.revision != data['revision']:
        raise ValidationError('Resource changed. Reload and review the link again.')
    for name in ('name', 'kind', 'url', 'notes', 'approved_for_outreach', 'is_archived'):
        setattr(row, name, data[name])
    row.revision += 1
    row.full_clean()
    row.save()
    audit(group, actor, 'booking.resource.saved', row.identity, new=row.revision)
    return row


@transaction.atomic
def save_rehearsal(group, actor, opportunity_id, data, rehearsal_id=None):
    lock_band(group)
    require_booking(actor, group, EDIT_ROLES)
    opportunity = get_object_or_404(BookingOpportunity, group=group, pk=opportunity_id)
    require_ready(opportunity)
    selected = same_band_setlist(group, data.get('setlist'))
    if rehearsal_id:
        row = get_object_or_404(BookingRehearsal, group=group, opportunity=opportunity, pk=rehearsal_id)
        if row.revision != data['revision'] or row.status != 'planned':
            raise ValidationError('Rehearsal changed or is no longer planned. Reload before editing.')
    else:
        row = BookingRehearsal.objects.filter(creation_key=data['creation_key']).first()
        if row:
            if (row.group_id == group.pk and row.opportunity_id == opportunity.pk
                    and row.date == data['date'] and row.focus == data['focus']
                    and row.setlist_id == (selected.pk if selected else None)):
                return row
            raise ValidationError('This rehearsal action was already used. Reload before adding another.')
        row = BookingRehearsal(group=group, opportunity=opportunity, creation_key=data['creation_key'], revision=0)
    row.date, row.focus, row.setlist = data['date'], data['focus'], selected
    row.revision += 1
    row.full_clean()
    row.save()
    audit(group, actor, 'booking.rehearsal.saved', row.identity, new=row.revision)
    return row


@transaction.atomic
def rehearsal_status(group, actor, rehearsal_id, revision, status):
    lock_band(group)
    require_booking(actor, group, EDIT_ROLES)
    row = get_object_or_404(BookingRehearsal, group=group, pk=rehearsal_id)
    if row.revision != revision:
        raise ValidationError('Rehearsal changed. Reload before changing its status.')
    if status not in ('planned', 'complete', 'cancelled') or row.status == status:
        raise ValidationError('Choose a different rehearsal status.')
    if status == 'planned':
        require_ready(row.opportunity)
    row.status, row.revision = status, row.revision + 1
    row.save(update_fields=['status', 'revision', 'updated_at'])
    audit(group, actor, 'booking.rehearsal.status', row.identity, new=status)
    return row
