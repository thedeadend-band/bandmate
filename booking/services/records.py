"""Human-reviewed records, durable identities and band-local suppression."""
import hashlib
import json
import unicodedata
from urllib.parse import urlsplit, urlunsplit
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from player.group_permissions import audit
from booking.permissions import require_booking
from booking.models import (BookingSettings, BookingMembershipRole, BookingOrganization, BookingVenue,
    BookingContact, BookingEventSeries, BookingOpportunity, BookingIdentityAlias, BookingSuppression, BookingEvidence,
    BookingArtistReference, BookingArtistGig)

RECORDS = {'organizations': BookingOrganization, 'venues': BookingVenue, 'contacts': BookingContact,
           'series': BookingEventSeries, 'opportunities': BookingOpportunity,
           'artists': BookingArtistReference, 'artist_gigs': BookingArtistGig}
EDIT_ROLES = [BookingMembershipRole.Role.MANAGER, BookingMembershipRole.Role.EDITOR]


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def canonical_url(value):
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        host = parsed.hostname.encode('idna').decode().lower()
        port = parsed.port
        netloc = host if port in (None, 443 if parsed.scheme == 'https' else 80) else f'{host}:{port}'
        return urlunsplit((parsed.scheme, netloc, parsed.path or '/', parsed.query, ''))
    except (ValueError, UnicodeError):
        raise ValidationError('Enter a valid public HTTP or HTTPS URL without credentials.') from None


def direct_keys(record):
    keys = {('identity', str(record.identity)), ('name_city', normalized(record.name) + '|' + normalized(record.city))}
    if getattr(record, 'email', ''):
        keys.add(('email', record.email.lower()))
        keys.add(('domain', record.email.rsplit('@', 1)[-1].lower()))
    if record.website:
        url = canonical_url(record.website)
        keys.add(('url', url))
        keys.add(('domain', urlsplit(url).hostname))
    return keys


def remember_aliases(record):
    for kind, value in direct_keys(record):
        BookingIdentityAlias.objects.get_or_create(group=record.group, identity=record.identity, kind=kind, value=value)


class SuppressionIndex:
    def __init__(self, group):
        self.group = group
        self.active, self.contact = set(), set()
        for row in BookingSuppression.objects.filter(group=group):
            if row.active:
                self.active.add((row.kind, row.value))
            if row.active or row.do_not_contact:
                self.contact.add((row.kind, row.value))
        self.aliases = {}
        for row in BookingIdentityAlias.objects.filter(group=group):
            self.aliases.setdefault(row.identity, set()).add((row.kind, row.value))

    def record_keys(self, record):
        keys = direct_keys(record) | self.aliases.get(record.identity, set())
        for field in ['organization', 'venue', 'contact', 'series', 'artist']:
            if getattr(record, field + '_id', None):
                related = getattr(record, field)
                if related.group_id != self.group.pk:
                    # Corrupt cross-band references must not become a read shortcut.
                    raise ValidationError('Invalid band relationship.')
                keys |= self.record_keys(related)
        return keys

    def blocked(self, record, *, sending=False):
        return bool(self.record_keys(record) & (self.contact if sending else self.active))

    def blocks_source(self, *, url='', emails=(), name='', city=''):
        keys = {('name_city', normalized(name) + '|' + normalized(city))}
        if url:
            url = canonical_url(url)
            keys |= {('url', url), ('domain', urlsplit(url).hostname)}
        for email in emails:
            keys |= {('email', email.lower()), ('domain', email.rsplit('@', 1)[-1].lower())}
        # Follow retained aliases so renamed/merged/blacklisted entities cannot
        # silently reappear through an old email, source URL or name.
        for identity, aliases in self.aliases.items():
            if {key for key in keys & aliases if key[0] != 'domain'}:
                keys |= aliases | {('identity', str(identity))}
        return bool(keys & self.contact)

    def blocks_proposal(self, proposed, *, title=''):
        if self.blocks_source(url=proposed.get('website', ''),
                emails=[proposed['contact_email']] if proposed.get('contact_email') else [],
                name=proposed.get('name') or title, city=proposed.get('city', '')):
            return True
        return any(self.blocks_source(name=proposed[field], city=proposed.get('city', ''))
            for field in ('venue_name', 'artist_name', 'organization_name', 'series_name') if proposed.get(field))


def lock_band(group):
    BookingSettings.objects.filter(group=group).update(updated_at=F('updated_at'))


@transaction.atomic
def save_record(form, actor, group, revision=None, *, source=None):
    lock_band(group)
    require_booking(actor, group, EDIT_ROLES)
    record = form.instance
    if record.group_id != group.pk:
        raise ValidationError('Invalid band.')
    if record.pk:
        current = type(record).objects.get(pk=record.pk, group=group)
        if current.revision != revision or current.is_archived:
            raise ValidationError('This record changed. Reload it before saving.')
        remember_aliases(current)
        record.revision = current.revision + 1
    dates_changed = isinstance(record, BookingOpportunity) and (record.verified_deadline or record.application_open_date) and (not record.pk or any(
        getattr(current, field) != getattr(record, field) for field in ['application_open_date', 'verified_deadline', 'deadline_source', 'deadline_excerpt', 'event_year']))
    if dates_changed:
        record.last_verified_at = timezone.now()
    record = form.save()
    remember_aliases(record)
    if dates_changed:
        for field in ['application_open_date', 'verified_deadline']:
            if getattr(record, field):
                BookingEvidence.objects.create(group=group, record_identity=record.identity, field_name=field,
                    source_type='manual', source_ref=record.deadline_source, excerpt=record.deadline_excerpt,
                    content_hash=digest(record.deadline_excerpt), event_year=record.event_year)
    if source:
        BookingEvidence.objects.create(group=group, record_identity=record.identity, field_name='discovery',
            source_type=source.source_type, source_ref=source.source_ref, excerpt=source.excerpt,
            content_hash=source.content_hash, confidence='human_reviewed', event_year=getattr(record, 'event_year', None))
    audit(group, actor, 'booking.record.saved', record.identity, new=record._meta.model_name)
    return record


@transaction.atomic
def merge_records(group, actor, kind, source_id, target_id):
    lock_band(group)
    require_booking(actor, group, [BookingMembershipRole.Role.MANAGER])
    model = RECORDS[kind]
    source = model.objects.get(group=group, pk=source_id, is_archived=False)
    target = model.objects.get(group=group, pk=target_id, is_archived=False)
    if source.pk == target.pk:
        raise ValidationError('Choose two different records.')
    if kind == 'opportunities' and source.event_year != target.event_year:
        raise ValidationError('Annual editions from different years cannot be merged.')
    remember_aliases(source)
    remember_aliases(target)
    # Preserve all source identities, including suppression matches, on the target.
    for alias in BookingIdentityAlias.objects.filter(group=group, identity=source.identity):
        BookingIdentityAlias.objects.get_or_create(group=group, identity=target.identity, kind=alias.kind, value=alias.value)
    for other in RECORDS.values():
        for field in other._meta.fields:
            if field.is_relation and field.related_model == model:
                other.objects.filter(group=group, **{field.name: source}).update(**{field.name: target}, revision=F('revision') + 1)
    if kind == 'opportunities':
        from booking.models import BookingFollowUp, BookingCandidate
        BookingFollowUp.objects.filter(group=group, opportunity=source).update(opportunity=target)
        BookingCandidate.objects.filter(group=group, opportunity=source).update(opportunity=target)
    BookingEvidence.objects.filter(group=group, record_identity=source.identity).update(record_identity=target.identity)
    source.is_archived = True
    source.revision += 1
    source.save(update_fields=['is_archived', 'revision', 'updated_at'])
    target.revision += 1
    target.save(update_fields=['revision', 'updated_at'])
    audit(group, actor, 'booking.record.merged', source.identity, new=target.identity)
    return target
