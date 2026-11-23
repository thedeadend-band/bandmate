from django.core.management.base import BaseCommand, CommandError
from bandmate.credential_vault import rotate, VaultUnavailable
from calendar_integrations.models import CalendarGoogleAccount, GoogleOAuthState


class Command(BaseCommand):
    help = 'Re-encrypt Calendar credentials with the first key in the configured key ring.'

    def handle(self, *args, **options):
        count = 0
        try:
            for model, field in [(CalendarGoogleAccount, 'refresh_ciphertext'), (GoogleOAuthState, 'verifier_ciphertext')]:
                for row in model.objects.exclude(**{field: ''}).iterator():
                    original = getattr(row, field)
                    replacement = rotate(original)
                    count += model.objects.filter(pk=row.pk, **{field: original}).update(**{field: replacement})
        except VaultUnavailable:
            raise CommandError('Rotation stopped. Retain all key versions and fix vault configuration before retrying.') from None
        self.stdout.write(f'Encrypted Calendar records rotated: {count}. Retain prior keys until backup recovery is verified.')
