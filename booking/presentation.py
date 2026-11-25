from datetime import timedelta
from django.urls import reverse
from .permissions import booking_role
from .models import BookingOpportunity, BookingFollowUp, BookingCalendarPublication, BookingRehearsal, BookingGigPlan
from .services.records import SuppressionIndex


def milestones(user, group, start, end):
    if not booking_role(user, group):
        return [], []
    index, events = SuppressionIndex(group), []
    source_id = f'booking:{group.pk}'
    # Only a durable, still-visible publication suppresses its event-date overlay.
    # Deadlines/reminders remain separate; unlink/removal restores the overlay.
    from calendar_integrations.models import LocalEvent, GoogleEvent
    published = set()
    for link in BookingCalendarPublication.objects.filter(group=group, linked=True).select_related('operation'):
        if link.operation.action == 'delete' and link.operation.status == 'succeeded':
            continue
        if link.local_calendar_id:
            exists = LocalEvent.objects.filter(pk=link.event_id, calendar_id=link.local_calendar_id,
                calendar__group=group, calendar__is_enabled=True, is_deleted=False,
                starts_at__lte=end, ends_at__gte=start).exists()
        else:
            exists = GoogleEvent.objects.filter(external_id=link.event_id, calendar_id=link.google_calendar_id,
                calendar__account__group=group, calendar__account__is_connected=True, calendar__is_enabled=True,
                is_deleted=False, starts_at__lte=end, ends_at__gte=start).exists()
        if exists:
            published.add(link.opportunity_id)

    def add(opportunity, key, title, day, until=None, predicted=False, url=None):
        if not day or day > end.date() or (until or day) < start.date():
            return
        events.append({'id': f'booking:{key}:{opportunity.pk}', 'source_id': source_id,
            'source_name': 'Booking milestones', 'color': '#e5a84b' if predicted else '#9c7be8',
            'title': title + ' · ' + opportunity.name, 'all_day': True,
            'start': day.isoformat(), 'end': ((until or day) + timedelta(days=1)).isoformat(),
            'location': opportunity.city, 'description': 'Prediction, not an official date.' if predicted else '',
            'url': url or reverse('booking:record_detail', args=['opportunities', opportunity.pk])})

    for opportunity in BookingOpportunity.objects.filter(group=group, is_archived=False).select_related('organization', 'venue', 'contact', 'series'):
        if index.blocked(opportunity):
            continue
        add(opportunity, 'deadline', 'Verified deadline', opportunity.verified_deadline)
        add(opportunity, 'open', 'Applications open', opportunity.application_open_date)
        if opportunity.pk not in published:
            add(opportunity, 'event', 'Event', opportunity.event_date)
        add(opportunity, 'predicted', 'Predicted window', opportunity.predicted_window_start, opportunity.predicted_window_end, True)
        for reminder in BookingFollowUp.objects.filter(group=group, opportunity=opportunity, completed=False):
            add(opportunity, 'reminder-' + str(reminder.pk), 'Follow-up reminder', reminder.due_date)
    for rehearsal in BookingRehearsal.objects.filter(group=group, status='planned', is_archived=False,
            opportunity__group=group, opportunity__stage='confirmed', opportunity__is_archived=False,
            date__gte=start.date(), date__lte=end.date()).select_related('opportunity'):
        if not index.blocked(rehearsal.opportunity):
            add(rehearsal.opportunity, 'rehearsal-' + str(rehearsal.pk), 'Rehearsal', rehearsal.date,
                url=reverse('booking:gig', args=[rehearsal.opportunity_id]))
    return [{'id': source_id, 'name': 'Booking milestones', 'color': '#9c7be8', 'mode': 'View only',
             'kind': 'booking', 'last_synced_at': '', 'last_error': ''}], events


def gig_links(user, group, *, setlist=None, kind=None, event=None):
    """Booking details are hidden from ordinary Calendar/Setlist members."""
    if not booking_role(user, group):
        return []
    opportunities = BookingOpportunity.objects.filter(group=group, is_archived=False, stage='confirmed')
    if setlist is not None:
        ids = BookingGigPlan.objects.filter(group=group, setlist__group=group,
            setlist=setlist).values_list('opportunity_id', flat=True)
        rehearsals = BookingRehearsal.objects.filter(group=group, setlist__group=group,
            setlist=setlist, status='planned').values_list('opportunity_id', flat=True)
        opportunities = opportunities.filter(pk__in=set(ids) | set(rehearsals))
    elif kind in ('local', 'google') and event is not None:
        links = BookingCalendarPublication.objects.filter(group=group, linked=True,
            event_id=str(event.pk) if kind == 'local' else event.external_id,
            **{kind + '_calendar_id': event.calendar_id})
        opportunities = opportunities.filter(pk__in=links.values_list('opportunity_id', flat=True))
    else:
        return []
    index = SuppressionIndex(group)
    return [row for row in opportunities.order_by('event_date', 'name') if not index.blocked(row)]
