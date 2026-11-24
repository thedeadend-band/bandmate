"""Transparent ranking using only known coordinates, dates and saved priority."""
from math import radians, sin, cos, asin, sqrt
from django.utils import timezone
from .records import normalized


def assess(opportunity, config):
    score = opportunity.priority * 10
    reasons = [f'Saved priority: +{score}']
    distance = None
    venue = opportunity.venue
    if venue and venue.latitude is not None and venue.longitude is not None and config.search_latitude is not None and config.search_longitude is not None:
        a, b, c, d = map(radians, map(float, [config.search_latitude, config.search_longitude, venue.latitude, venue.longitude]))
        haversine = sin((c - a) / 2) ** 2 + cos(a) * cos(c) * sin((d - b) / 2) ** 2
        distance = 3958.7613 * 2 * asin(sqrt(min(1, max(0, haversine))))
        if distance <= config.radius_miles:
            score += 20
            reasons.append('Within straight-line radius: +20')
        else:
            reasons.append('Outside straight-line radius')
    else:
        reasons.append('Location unverified; kept for review')
    if opportunity.verified_deadline:
        days = (opportunity.verified_deadline - timezone.now().date()).days
        if 0 <= days <= 60:
            score += 15
            reasons.append('Verified deadline within 60 days: +15')
    # These are human-reviewed facts, never an inferred promise of suitability.
    import re
    tokens = lambda value: {part.strip().casefold() for part in re.split(r'[,;]', value) if part.strip()}
    matches = tokens(config.genres) & tokens(opportunity.genres)
    if matches:
        score += 10
        reasons.append('Matching saved genres (' + ', '.join(sorted(matches)) + '): +10')
    if opportunity.prior_booking and opportunity.history_evidence:
        score += 20
        reasons.append('Documented previous booking: +20')
    if opportunity.response_status == 'responded' and opportunity.history_evidence:
        score += 10
        reasons.append('Documented previous response: +10')
    elif opportunity.response_status == 'declined' and opportunity.history_evidence:
        score -= 10
        reasons.append('Documented previous decline: −10')
    if opportunity.stage in ('declined', 'not_selected', 'closed'):
        score -= 50
        reasons.append('Closed or declined application: −50')
    if opportunity.expected_fee is not None:
        reasons.append('Expected fee recorded; no currency or fee weighting assumed')
    excluded = normalized(opportunity.city or (venue.city if venue else '')) in {
        normalized(city) for city in config.excluded_cities.split(',') if city.strip()}
    if excluded:
        score -= 30
        reasons.append('Excluded city: −30')
    years = {int(year) for year in config.target_years.split(',') if year.strip().isdigit()}
    if years and opportunity.event_year not in years:
        reasons.append('Outside configured target years')
    return {'score': score, 'reasons': reasons, 'distance': round(distance, 1) if distance is not None else None,
            'excluded': excluded, 'outside_radius': distance is not None and distance > config.radius_miles}
