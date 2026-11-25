from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .models import BookingOpportunity, BookingGigPlan, BookingRehearsal, BookingResource, BookingMembershipRole
from .permissions import require_booking
from .gig_forms import GigPlanForm, RehearsalForm, ResourceForm
from .services import gigs
from .services.records import EDIT_ROLES, SuppressionIndex


@login_required
@require_GET
def directory(request):
    require_booking(request.user, request.active_group)
    index = SuppressionIndex(request.active_group)
    rows = BookingOpportunity.objects.filter(group=request.active_group, is_archived=False,
        stage='confirmed').select_related('bookinggigplan__setlist').order_by('event_date', 'name')
    return render(request, 'booking/gigs.html', {'nav_active': 'booking',
        'rows': [row for row in rows if not index.blocked(row)]})


@login_required
@require_http_methods(['GET', 'POST'])
def detail(request, opportunity_id):
    role = require_booking(request.user, request.active_group)
    opportunity = get_object_or_404(BookingOpportunity, group=request.active_group, pk=opportunity_id)
    plan = BookingGigPlan.objects.filter(group=request.active_group, opportunity=opportunity).select_related('setlist').first()
    initial = {'revision': plan.revision if plan else 0, 'setlist': plan.setlist_id if plan else None,
        'notes': plan.notes if plan else ''}
    form = GigPlanForm(request.POST if request.method == 'POST' else None, group=request.active_group, initial=initial)
    status = 200
    if request.method == 'POST':
        require_booking(request.user, request.active_group, EDIT_ROLES)
        if form.is_valid():
            try:
                action = request.POST.get('action')
                if action not in ('save', 'create_setlist'):
                    raise ValidationError('Choose a gig preparation action.')
                if action == 'create_setlist' and request.POST.get('confirm_setlist') != 'create_for_band':
                    raise ValidationError('Confirm creation of a setlist visible to band members.')
                saved = gigs.save_plan(request.active_group, request.user, opportunity.pk, form.cleaned_data,
                    create_setlist=action == 'create_setlist')
                if action == 'create_setlist':
                    return redirect('setlist_edit', setlist_id=saved.setlist_id)
                return redirect('booking:gig', opportunity_id=opportunity.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
                status = 409
        else:
            status = 400
    can_prepare = gigs.ready(opportunity)
    return render(request, 'booking/gig.html', {'nav_active': 'booking', 'opportunity': opportunity,
        'plan': plan, 'form': form, 'can_prepare': can_prepare, 'can_edit': role in EDIT_ROLES,
        'can_edit_setlist': plan and plan.setlist and (plan.setlist.owner_id == request.user.pk or request.user.is_staff),
        'rehearsals': BookingRehearsal.objects.filter(group=request.active_group, opportunity=opportunity).select_related('setlist').order_by('date', 'pk'),
        'resources': BookingResource.objects.filter(group=request.active_group, is_archived=False).order_by('kind', 'name')}, status=status)


@login_required
@require_GET
def resources(request):
    role = require_booking(request.user, request.active_group)
    return render(request, 'booking/resources.html', {'nav_active': 'booking',
        'is_manager': role == BookingMembershipRole.Role.MANAGER,
        'rows': BookingResource.objects.filter(group=request.active_group).order_by('is_archived', 'kind', 'name')})


@login_required
@require_http_methods(['GET', 'POST'])
def resource_edit(request, resource_id=None):
    require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])
    row = get_object_or_404(BookingResource, group=request.active_group, pk=resource_id) if resource_id else BookingResource(group=request.active_group)
    form = ResourceForm(request.POST if request.method == 'POST' else None, instance=row,
        initial={'revision': row.revision if row.pk else 0})
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                gigs.save_resource(request.active_group, request.user, resource_id, form.cleaned_data)
                return redirect('booking:resources')
            except ValidationError as exc:
                form.add_error(None, exc)
                status = 409
        else:
            status = 400
    return render(request, 'booking/outreach_form.html', {'nav_active': 'booking', 'form': form,
        'title': 'Edit resource' if resource_id else 'Add resource link', 'button': 'Save reviewed resource'}, status=status)


@login_required
@require_http_methods(['GET', 'POST'])
def rehearsal_edit(request, opportunity_id, rehearsal_id=None):
    require_booking(request.user, request.active_group, EDIT_ROLES)
    opportunity = get_object_or_404(BookingOpportunity, group=request.active_group, pk=opportunity_id)
    row = get_object_or_404(BookingRehearsal, group=request.active_group, opportunity=opportunity, pk=rehearsal_id) if rehearsal_id else None
    plan = BookingGigPlan.objects.filter(group=request.active_group, opportunity=opportunity).first()
    initial = {'setlist': plan.setlist_id if plan else None}
    if row:
        initial.update(revision=row.revision, creation_key=row.creation_key, date=row.date, focus=row.focus, setlist=row.setlist_id)
    form = RehearsalForm(request.POST if request.method == 'POST' else None, group=request.active_group, initial=initial)
    status = 200
    if request.method == 'POST':
        if form.is_valid():
            try:
                gigs.save_rehearsal(request.active_group, request.user, opportunity.pk, form.cleaned_data, rehearsal_id)
                return redirect('booking:gig', opportunity_id=opportunity.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
                status = 409
        else:
            status = 400
    return render(request, 'booking/outreach_form.html', {'nav_active': 'booking', 'form': form,
        'title': ('Edit rehearsal · ' if row else 'Plan rehearsal · ') + opportunity.name,
        'button': 'Save rehearsal'}, status=status)


@login_required
@require_POST
def rehearsal_status(request, rehearsal_id):
    require_booking(request.user, request.active_group, EDIT_ROLES)
    try:
        row = gigs.rehearsal_status(request.active_group, request.user, rehearsal_id,
            int(request.POST.get('revision', '')), request.POST.get('status', ''))
    except (ValidationError, ValueError) as exc:
        return HttpResponseBadRequest(' '.join(exc.messages) if isinstance(exc, ValidationError) else 'Invalid revision.')
    return redirect('booking:gig', opportunity_id=row.opportunity_id)
