from datetime import timedelta
from django.urls import reverse
from .permissions import booking_role
from .models import BookingOpportunity, BookingFollowUp
from .services.records import SuppressionIndex


def milestones(user, group, start, end):
    if not booking_role(user, group):
        return [], []
    index, events = SuppressionIndex(group), []
    source_id = f'booking:{group.pk}'

    def add(opportunity, key, title, day, until=None, predicted=False):
        if not day or day > end.date() or (until or day) < start.date():
            return
        events.append({'id': f'booking:{key}:{opportunity.pk}', 'source_id': source_id,
            'source_name': 'Booking milestones', 'color': '#e5a84b' if predicted else '#9c7be8',
            'title': title + ' · ' + opportunity.name, 'all_day': True,
            'start': day.isoformat(), 'end': ((until or day) + timedelta(days=1)).isoformat(),
            'location': opportunity.city, 'description': 'Prediction, not an official date.' if predicted else '',
            'url': reverse('booking:record_detail', args=['opportunities', opportunity.pk])})

    for opportunity in BookingOpportunity.objects.filter(group=group, is_archived=False).select_related('organization', 'venue', 'contact', 'series'):
        if index.blocked(opportunity):
            continue
        add(opportunity, 'deadline', 'Verified deadline', opportunity.verified_deadline)
        add(opportunity, 'open', 'Applications open', opportunity.application_open_date)
        add(opportunity, 'event', 'Event', opportunity.event_date)
        add(opportunity, 'predicted', 'Predicted window', opportunity.predicted_window_start, opportunity.predicted_window_end, True)
        for reminder in BookingFollowUp.objects.filter(group=group, opportunity=opportunity, completed=False):
            add(opportunity, 'reminder-' + str(reminder.pk), 'Follow-up reminder', reminder.due_date)
    return [{'id': source_id, 'name': 'Booking milestones', 'color': '#9c7be8', 'mode': 'View only',
             'kind': 'booking', 'last_synced_at': '', 'last_error': ''}], events
