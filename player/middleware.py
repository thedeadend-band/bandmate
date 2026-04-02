import shutil
from pathlib import Path

from .models import SongWizard


class WizardCleanupMiddleware:
    """Cancel any in-progress SongWizard when the user navigates away from
    wizard pages. This covers normal link navigation, logout, etc."""

    WIZARD_PATH_PREFIXES = ('/songs/new/', '/api/songs/new/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return response

        path = request.path
        if any(path.startswith(p) for p in self.WIZARD_PATH_PREFIXES):
            return response

        # Non-wizard page: cancel any in-progress wizards for this user
        wizards = SongWizard.objects.filter(
            created_by=request.user,
        ).exclude(current_step__in=('complete', 'failed'))

        for wiz in wizards:
            if wiz.staging_dir and Path(wiz.staging_dir).exists():
                shutil.rmtree(wiz.staging_dir, ignore_errors=True)
            wiz.delete()

        return response
