import logging
import shutil
from pathlib import Path

from django.shortcuts import redirect

from .models import SongWizard

logger = logging.getLogger(__name__)


class ActiveGroupMiddleware:
    EXEMPT_PREFIXES = (
        '/login/', '/logout/', '/groups/', '/static/', '/media/', '/admin/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.active_group = None
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return self.get_response(request)
        if any(request.path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
            return self.get_response(request)

        from .models import Group
        groups = Group.objects.all() if request.user.is_staff else Group.objects.filter(
            memberships__user=request.user)
        groups = groups.distinct().order_by('name')
        selected_id = request.session.get('active_group_id')
        active_group = groups.filter(pk=selected_id).first() if selected_id else None
        if not active_group and groups.count() == 1:
            active_group = groups.first()
            request.session['active_group_id'] = active_group.pk
        if not active_group:
            return redirect('group_select')
        request.active_group = active_group
        return self.get_response(request)


class WizardCleanupMiddleware:
    """Cancel any in-progress SongWizard when the user navigates away from
    wizard pages. This covers normal link navigation, logout, etc."""

    WIZARD_PATH_PREFIXES = ('/songs/new/', '/api/songs/new/', '/queue/', '/api/queue/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return response

        path = request.path
        if any(path.startswith(p) for p in self.WIZARD_PATH_PREFIXES):
            return response

        try:
            wizards = SongWizard.objects.filter(
                created_by=request.user,
            ).exclude(current_step__in=('complete', 'failed'))

            for wiz in wizards:
                if wiz.staging_dir and Path(wiz.staging_dir).exists():
                    shutil.rmtree(wiz.staging_dir, ignore_errors=True)
                wiz.delete()
        except Exception:
            logger.debug('WizardCleanupMiddleware: cleanup skipped due to error', exc_info=True)

        return response
