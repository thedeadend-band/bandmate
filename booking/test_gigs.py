import uuid
from datetime import date, datetime, timezone
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import Client, TestCase
from django.urls import reverse
from player.models import Group, GroupMembership, GroupAccessAudit, Setlist, SetlistEntry
from calendar_integrations.models import BandmateLocalCalendar, LocalEvent, CalendarWriteOperation
from .models import (BookingSettings, BookingMembershipRole, BookingOpportunity, BookingGigPlan,
    BookingResource, BookingRehearsal, BookingContact, BookingSuppression, BookingCalendarPublication, BookingJob)
from .services.gigs import save_plan, save_resource, save_rehearsal, rehearsal_status
from .services.outreach import compose, save_template
from .outreach_forms import ComposeForm
from .presentation import milestones


class GigPreparationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Group.objects.create(name='Gig band A')
        cls.b = Group.objects.create(name='Gig band B')
        cls.manager = User.objects.create_user('gig-manager')
        cls.editor = User.objects.create_user('gig-editor')
        cls.viewer = User.objects.create_user('gig-viewer')
        cls.member = User.objects.create_user('gig-member')
        cls.staff = User.objects.create_user('gig-staff', is_staff=True)
        for group in (cls.a, cls.b):
            BookingSettings.objects.create(group=group, enabled=True)
            for user, role in ((cls.manager, 'booking_manager'), (cls.editor, 'booking_editor'),
                    (cls.viewer, 'booking_viewer'), (cls.member, None)):
                membership = GroupMembership.objects.create(group=group, user=user)
                if role:
                    BookingMembershipRole.objects.create(membership=membership, role=role)
        cls.gig = BookingOpportunity.objects.create(group=cls.a, name='Riverfront summer gig',
            event_year=2027, event_date=date(2027, 7, 10), stage='confirmed')
        cls.foreign_gig = BookingOpportunity.objects.create(group=cls.b, name='Private band B gig',
            event_year=2027, stage='confirmed')
        cls.setlist = Setlist.objects.create(group=cls.a, owner=cls.member, name='Band A set', date=date(2027, 6, 1))
        cls.foreign_setlist = Setlist.objects.create(group=cls.b, owner=cls.manager, name='Private band B set')
        cls.contact = BookingContact.objects.create(group=cls.a, name='Booker', email='booker@example.com')
        cls.resource = BookingResource.objects.create(group=cls.a, name='Public EPK', kind='epk',
            url='https://band.example/epk', notes='Private resource notes', approved_for_outreach=True)
        cls.foreign_resource = BookingResource.objects.create(group=cls.b, name='Private band B EPK',
            url='https://other.example/epk', approved_for_outreach=True)

    def setUp(self):
        self.login(self.manager)

    def login(self, user, group=None, client=None):
        client = client or self.client
        client.force_login(user)
        session = client.session
        session['active_group_id'] = (group or self.a).pk
        session.save()

    def plan(self):
        return save_plan(self.a, self.manager, self.gig.pk, {'revision': 0, 'setlist': self.setlist, 'notes': 'Private load-in notes'})

    def rehearsal_data(self, **changes):
        data = {'revision': 0, 'creation_key': uuid.uuid4(), 'date': date(2027, 7, 2),
            'focus': 'Practice transitions and encore', 'setlist': self.setlist}
        return data | changes

    def rehearsal(self):
        return save_rehearsal(self.a, self.manager, self.gig.pk, self.rehearsal_data())

    def overlay(self):
        return milestones(self.manager, self.a, datetime(2027, 7, 1, tzinfo=timezone.utc),
            datetime(2027, 8, 1, tzinfo=timezone.utc))[1]

    def test_gets_do_not_create_preparation_or_jobs(self):
        for name, args in [('gigs', []), ('gig', [self.gig.pk]), ('resources', []),
                ('resource_new', []), ('rehearsal_new', [self.gig.pk])]:
            self.assertEqual(self.client.get(reverse('booking:' + name, args=args)).status_code, 200)
        self.assertFalse(BookingGigPlan.objects.exists())
        self.assertFalse(BookingRehearsal.objects.exists())
        self.assertFalse(BookingJob.objects.exists())

    def test_role_matrix(self):
        for user, read_status, edit_status, manager_status in [(self.manager, 200, 200, 200),
                (self.editor, 200, 200, 404), (self.viewer, 200, 404, 404),
                (self.member, 404, 404, 404), (self.staff, 404, 404, 404)]:
            self.login(user)
            with self.subTest(user=user.username):
                for name, args in [('gigs', []), ('gig', [self.gig.pk]), ('resources', [])]:
                    self.assertEqual(self.client.get(reverse('booking:' + name, args=args)).status_code, read_status)
                self.assertEqual(self.client.get(reverse('booking:rehearsal_new', args=[self.gig.pk])).status_code, edit_status)
                self.assertEqual(self.client.get(reverse('booking:resource_edit', args=[self.resource.pk])).status_code, manager_status)

    def test_shared_user_cannot_address_other_band_ids(self):
        self.plan(); row = self.rehearsal()
        self.login(self.manager, self.b)
        for name, args in [('gig', [self.gig.pk]), ('rehearsal_new', [self.gig.pk]),
                ('rehearsal_edit', [self.gig.pk, row.pk]), ('resource_edit', [self.resource.pk])]:
            for method in (self.client.get, self.client.post):
                self.assertEqual(method(reverse('booking:' + name, args=args)).status_code, 404)
        self.assertEqual(self.client.post(reverse('booking:rehearsal_status', args=[row.pk]),
            {'revision': row.revision, 'status': 'complete'}).status_code, 404)

    def test_selectors_and_posted_relationships_are_band_scoped(self):
        page = self.client.get(reverse('booking:gig', args=[self.gig.pk]))
        self.assertNotContains(page, self.foreign_setlist.name)
        response = self.client.post(reverse('booking:gig', args=[self.gig.pk]),
            {'revision': 0, 'setlist': self.foreign_setlist.pk, 'notes': '', 'action': 'save'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(BookingGigPlan.objects.exists())
        with self.assertRaises(Http404):
            save_plan(self.a, self.manager, self.gig.pk, {'revision': 0, 'setlist': self.foreign_setlist})
        with self.assertRaises(Http404):
            save_rehearsal(self.a, self.manager, self.gig.pk, self.rehearsal_data(setlist=self.foreign_setlist))

    def test_model_relationships_reject_cross_band_data(self):
        for row in [BookingGigPlan(group=self.a, opportunity=self.foreign_gig),
                BookingGigPlan(group=self.a, opportunity=self.gig, setlist=self.foreign_setlist),
                BookingRehearsal(group=self.a, opportunity=self.foreign_gig, date=date(2027, 7, 2), focus='Test'),
                BookingRehearsal(group=self.a, opportunity=self.gig, setlist=self.foreign_setlist, date=date(2027, 7, 2), focus='Test')]:
            with self.assertRaises(ValidationError):
                row.save()

    def test_link_preserves_setlist_and_existing_owner_permissions(self):
        entry = SetlistEntry.objects.create(setlist=self.setlist, position=0, song_name='Existing song')
        self.plan()
        self.setlist.refresh_from_db()
        self.assertEqual(self.setlist.date, date(2027, 6, 1))
        self.assertEqual(self.setlist.owner, self.member)
        self.assertTrue(SetlistEntry.objects.filter(pk=entry.pk).exists())
        self.assertEqual(self.client.get(reverse('setlist_edit', args=[self.setlist.pk])).status_code, 404)

    def test_create_setlist_requires_consent_and_is_not_duplicated(self):
        url = reverse('booking:gig', args=[self.gig.pk])
        data = {'action': 'create_setlist', 'revision': 0, 'notes': 'Keep private'}
        self.assertEqual(self.client.post(url, data).status_code, 409)
        self.assertEqual(Setlist.objects.count(), 2)
        data['confirm_setlist'] = 'create_for_band'
        response = self.client.post(url, data)
        plan = BookingGigPlan.objects.get()
        self.assertRedirects(response, reverse('setlist_edit', args=[plan.setlist_id]), fetch_redirect_response=False)
        self.assertEqual(plan.setlist.owner, self.manager)
        self.assertEqual(plan.setlist.date, self.gig.event_date)
        self.assertFalse(plan.setlist.entries.exists())
        self.assertEqual(self.client.post(url, data).status_code, 409)
        self.assertEqual(Setlist.objects.count(), 3)
        self.assertEqual(GroupAccessAudit.objects.filter(action='booking.gig.setlist_created').count(), 1)

    def test_plan_revision_unlink_and_deletion_are_safe(self):
        plan = self.plan()
        with self.assertRaisesMessage(ValidationError, 'changed'):
            save_plan(self.a, self.manager, self.gig.pk, {'revision': 0, 'notes': 'Stale'})
        plan = save_plan(self.a, self.manager, self.gig.pk, {'revision': plan.revision, 'setlist': None, 'notes': 'Updated'})
        self.assertIsNone(plan.setlist_id)
        self.assertTrue(Setlist.objects.filter(pk=self.setlist.pk).exists())
        plan = save_plan(self.a, self.manager, self.gig.pk, {'revision': plan.revision, 'setlist': self.setlist})
        row = self.rehearsal()
        self.setlist.delete(); plan.refresh_from_db(); row.refresh_from_db()
        self.assertIsNone(plan.setlist_id)
        self.assertIsNone(row.setlist_id)
        self.assertEqual(self.client.get(reverse('booking:gig', args=[self.gig.pk])).status_code, 200)

    def test_opportunity_changes_do_not_mutate_linked_setlist(self):
        self.plan()
        self.gig.name, self.gig.event_date = 'Changed gig', date(2027, 8, 1)
        self.gig.save()
        self.setlist.refresh_from_db()
        self.assertEqual(self.setlist.name, 'Band A set')
        self.assertEqual(self.setlist.date, date(2027, 6, 1))

    def test_confirmed_active_unsuppressed_required(self):
        for changes in [{'stage': 'qualified'}, {'is_archived': True}]:
            BookingOpportunity.objects.filter(pk=self.gig.pk).update(**changes)
            with self.assertRaisesMessage(ValidationError, 'confirmed'):
                save_plan(self.a, self.manager, self.gig.pk, {'revision': 0})
            with self.assertRaises(ValidationError):
                save_rehearsal(self.a, self.manager, self.gig.pk, self.rehearsal_data())
            BookingOpportunity.objects.filter(pk=self.gig.pk).update(stage='confirmed', is_archived=False)
        BookingSuppression.objects.create(group=self.a, kind='identity', value=str(self.gig.identity), reason='Blocked')
        with self.assertRaises(ValidationError):
            self.plan()
        self.assertNotContains(self.client.get(reverse('booking:gigs')), self.gig.name)

    def test_disable_or_revocation_blocks_new_mutations_and_preserves_data(self):
        plan = self.plan(); row = self.rehearsal()
        BookingSettings.objects.filter(group=self.a).update(enabled=False)
        with self.assertRaises(Http404):
            save_plan(self.a, self.manager, self.gig.pk, {'revision': plan.revision})
        with self.assertRaises(Http404):
            rehearsal_status(self.a, self.manager, row.pk, row.revision, 'complete')
        self.assertEqual(self.client.get(reverse('booking:gig', args=[self.gig.pk])).status_code, 404)
        BookingSettings.objects.filter(group=self.a).update(enabled=True)
        BookingMembershipRole.objects.filter(membership__group=self.a, membership__user=self.manager).delete()
        with self.assertRaises(Http404):
            save_plan(self.a, self.manager, self.gig.pk, {'revision': plan.revision})
        plan.refresh_from_db(); self.assertEqual(plan.notes, 'Private load-in notes')

    def test_writes_require_csrf_and_viewers_cannot_post(self):
        row = self.rehearsal()
        paths = [reverse('booking:gig', args=[self.gig.pk]), reverse('booking:resource_new'),
            reverse('booking:rehearsal_new', args=[self.gig.pk]), reverse('booking:rehearsal_status', args=[row.pk])]
        csrf = Client(enforce_csrf_checks=True); self.login(self.manager, client=csrf)
        for path in paths:
            self.assertEqual(csrf.post(path, {}).status_code, 403)
        self.login(self.viewer)
        for path in paths:
            self.assertEqual(self.client.post(path, {}).status_code, 404)
        self.assertEqual(self.client.get(paths[-1]).status_code, 405)

    def test_rehearsal_duplicate_click_is_idempotent(self):
        data = self.rehearsal_data()
        row = save_rehearsal(self.a, self.editor, self.gig.pk, data)
        again = save_rehearsal(self.a, self.editor, self.gig.pk, data)
        self.assertEqual(row.pk, again.pk)
        with self.assertRaises(ValidationError):
            save_rehearsal(self.a, self.editor, self.gig.pk, data | {'focus': 'Changed'})
        self.assertEqual(BookingRehearsal.objects.count(), 1)
        self.assertEqual(GroupAccessAudit.objects.filter(action='booking.rehearsal.saved').count(), 1)

    def test_rehearsal_edit_keeps_its_selected_setlist_and_rejects_stale_edits(self):
        plan = self.plan(); row = self.rehearsal()
        save_plan(self.a, self.manager, self.gig.pk, {'revision': plan.revision})
        row.refresh_from_db(); self.assertEqual(row.setlist_id, self.setlist.pk)
        data = self.rehearsal_data(revision=row.revision, date=date(2027, 7, 3))
        updated = save_rehearsal(self.a, self.editor, self.gig.pk, data, row.pk)
        self.assertEqual(updated.date, date(2027, 7, 3))
        with self.assertRaises(ValidationError):
            save_rehearsal(self.a, self.editor, self.gig.pk, data, row.pk)

    def test_rehearsal_lifecycle_updates_overlay_without_calendar_writes(self):
        row = self.rehearsal()
        events = [e for e in self.overlay() if 'rehearsal' in e['id']]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['start'], '2027-07-02')
        self.assertEqual(events[0]['end'], '2027-07-03')
        self.assertEqual(events[0]['url'], reverse('booking:gig', args=[self.gig.pk]))
        self.assertNotIn(row.focus, str(events))
        row = rehearsal_status(self.a, self.editor, row.pk, row.revision, 'complete')
        self.assertFalse(any('rehearsal' in e['id'] for e in self.overlay()))
        with self.assertRaises(ValidationError):
            rehearsal_status(self.a, self.editor, row.pk, 1, 'planned')
        row = rehearsal_status(self.a, self.editor, row.pk, row.revision, 'planned')
        self.assertTrue(any('rehearsal' in e['id'] for e in self.overlay()))
        rehearsal_status(self.a, self.editor, row.pk, row.revision, 'cancelled')
        self.assertFalse(any('rehearsal' in e['id'] for e in self.overlay()))
        self.assertFalse(CalendarWriteOperation.objects.exists())
        self.assertFalse(BookingJob.objects.exists())

    def test_closed_gig_retains_preparation_and_allows_rehearsal_cleanup(self):
        self.plan(); row = self.rehearsal()
        BookingOpportunity.objects.filter(pk=self.gig.pk).update(stage='closed')
        self.assertFalse(any('rehearsal' in e['id'] for e in self.overlay()))
        response = self.client.get(reverse('booking:gig', args=[self.gig.pk]))
        self.assertContains(response, 'Private load-in notes')
        self.assertNotContains(response, 'Create setlist and choose songs')
        row = rehearsal_status(self.a, self.editor, row.pk, row.revision, 'cancelled')
        with self.assertRaises(ValidationError):
            rehearsal_status(self.a, self.editor, row.pk, row.revision, 'planned')

    def test_rehearsal_day_and_focus_are_validated_in_ui(self):
        response = self.client.post(reverse('booking:rehearsal_new', args=[self.gig.pk]),
            {'revision': 0, 'creation_key': uuid.uuid4(), 'date': '2027-02-30', 'focus': ''})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(BookingRehearsal.objects.exists())

    def test_resources_are_private_and_manager_maintained(self):
        self.assertNotContains(self.client.get(reverse('booking:resources')), self.foreign_resource.name)
        self.login(self.editor)
        self.assertEqual(self.client.post(reverse('booking:resource_edit', args=[self.resource.pk]), {}).status_code, 404)
        with self.assertRaises(Http404):
            save_resource(self.a, self.editor, self.resource.pk, {})

    def test_resource_url_validation_and_stale_revision(self):
        data = {'revision': 0, 'name': 'Stage plot', 'kind': 'stage_plot', 'url': 'https://band.example/stage',
            'notes': '', 'approved_for_outreach': False, 'is_archived': False}
        for url in ('javascript:alert(1)', 'file:///etc/passwd', 'https://user:password@example.com/epk'):
            response = self.client.post(reverse('booking:resource_new'), data | {'url': url})
            self.assertEqual(response.status_code, 400)
        row = save_resource(self.a, self.manager, None, data)
        with self.assertRaises(ValidationError):
            save_resource(self.a, self.manager, row.pk, data)
        row = save_resource(self.a, self.manager, row.pk, data | {'revision': row.revision, 'is_archived': True})
        self.assertTrue(row.is_archived)
        self.assertNotContains(self.client.get(reverse('booking:gig', args=[self.gig.pk])), row.url)

    def test_outreach_choices_only_offer_active_reviewed_epks(self):
        for changes in ({'is_archived': True}, {'approved_for_outreach': False}, {'kind': 'rider'}):
            BookingResource.objects.filter(pk=self.resource.pk).update(**changes)
            form = ComposeForm(group=self.a)
            self.assertFalse(form.fields['epk_resource'].queryset.exists())
            with self.assertRaises(ValidationError):
                compose(self.a, self.editor, self.gig, {'contact': self.contact, 'epk_resource': self.resource})
            BookingResource.objects.filter(pk=self.resource.pk).update(is_archived=False, approved_for_outreach=True, kind='epk')
        with self.assertRaises(ValidationError):
            compose(self.a, self.editor, self.gig, {'contact': self.contact, 'epk_resource': self.foreign_resource})
        form = ComposeForm({'contact': self.contact.pk, 'epk_resource': self.resource.pk,
            'epk_url': 'https://second.example/epk'}, group=self.a)
        self.assertFalse(form.is_valid())

    def test_epk_draft_copies_exact_url_and_provenance_without_private_notes(self):
        template = save_template(self.a, self.manager, {'name': 'EPK intro', 'category': 'venue',
            'subject': '{{band_name}}', 'body': 'Our EPK: {{epk_url}}'})
        draft = compose(self.a, self.editor, self.gig, {'contact': self.contact,
            'template': template, 'epk_resource': self.resource})
        self.assertEqual(draft.body, 'Our EPK: https://band.example/epk')
        self.assertEqual(draft.context['epk_resource_identity'], str(self.resource.identity))
        self.assertEqual(draft.context['epk_resource_revision'], self.resource.revision)
        self.assertNotIn(self.resource.notes, str(draft.context))
        BookingResource.objects.filter(pk=self.resource.pk).update(url='https://band.example/new', is_archived=True)
        draft.refresh_from_db(); self.assertEqual(draft.body, 'Our EPK: https://band.example/epk')
        self.assertFalse(BookingJob.objects.exists())

    def test_setlist_backlinks_respect_booking_role_and_feature(self):
        self.plan()
        url = reverse('setlist_player', args=[self.setlist.pk])
        gig_url = reverse('booking:gig', args=[self.gig.pk])
        self.assertContains(self.client.get(url), gig_url)
        self.login(self.member)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.gig.name)
        self.assertNotContains(response, 'Private load-in notes')
        self.login(self.manager)
        BookingSettings.objects.filter(group=self.a).update(enabled=False)
        self.assertNotContains(self.client.get(url), gig_url)

    def test_calendar_backlinks_require_booking_access_and_active_link(self):
        calendar = BandmateLocalCalendar.objects.create(group=self.a, name='Local gigs')
        event = LocalEvent.objects.create(calendar=calendar, title='Public calendar title',
            starts_at=datetime(2027, 7, 10, tzinfo=timezone.utc), ends_at=datetime(2027, 7, 11, tzinfo=timezone.utc))
        operation = CalendarWriteOperation.objects.create(group=self.a, local_calendar=calendar,
            key=uuid.uuid4(), action='create', status='succeeded', event_id=str(event.pk))
        link = BookingCalendarPublication.objects.create(group=self.a, opportunity=self.gig,
            local_calendar=calendar, target=f'local:{calendar.pk}', operation=operation, event_id=str(event.pk))
        url = reverse('calendar_events:detail', args=['local', event.pk])
        gig_url = reverse('booking:gig', args=[self.gig.pk])
        self.assertContains(self.client.get(url), gig_url)
        self.login(self.member)
        self.assertNotContains(self.client.get(url), self.gig.name)
        self.login(self.manager)
        link.linked = False; link.save()
        self.assertNotContains(self.client.get(url), gig_url)

    def test_rehearsal_overlays_are_hidden_from_non_booking_members(self):
        self.rehearsal()
        start, end = datetime(2027, 7, 1, tzinfo=timezone.utc), datetime(2027, 8, 1, tzinfo=timezone.utc)
        self.assertEqual(milestones(self.member, self.a, start, end), ([], []))
        self.assertFalse(any('rehearsal' in e['id'] for e in milestones(self.manager, self.b, start, end)[1]))
        BookingSuppression.objects.create(group=self.a, kind='identity', value=str(self.gig.identity), reason='Blocked')
        self.assertFalse(any('rehearsal' in e['id'] for e in self.overlay()))
