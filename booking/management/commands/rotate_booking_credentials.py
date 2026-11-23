from django.core.management.base import BaseCommand, CommandError
from bandmate.credential_vault import rotate, VaultUnavailable
from booking.models import BookingOpenAICredential, BookingGmailConnection, GmailOAuthState


class Command(BaseCommand):
    help = 'Re-encrypt Booking credentials with the first configured vault key.'

    def handle(self, *args, **options):
        count = 0
        try:
            for model, field in [(BookingOpenAICredential, 'key_ciphertext'),
                                 (BookingGmailConnection, 'refresh_ciphertext'), (GmailOAuthState, 'verifier_ciphertext')]:
                for row in model.objects.exclude(**{field: ''}).iterator():
                    original = getattr(row, field)
                    replacement = rotate(original)
                    count += model.objects.filter(pk=row.pk, **{field: original}).update(**{field: replacement})
        except VaultUnavailable:
            raise CommandError('Rotation stopped. Retain all key versions, repair vault configuration and retry.') from None
        self.stdout.write(f'Encrypted Booking records rotated: {count}. Retain prior keys until backup recovery is verified.')
