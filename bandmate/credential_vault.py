"""Authenticated, tenant/purpose-bound encryption; no global provider fallback."""
import json
from pathlib import Path
from django.conf import settings


class VaultUnavailable(Exception):
    pass


def _cipher():
    from cryptography.fernet import Fernet, MultiFernet
    try:
        path = Path(settings.BANDMATE_SECRET_KEY_FILE)
        if path.stat().st_mode & 0o077:
            raise ValueError('Key file must not be group/world accessible')
        keys = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        if not keys:
            raise ValueError('Empty key ring')
        return MultiFernet([Fernet(key.encode()) for key in keys])
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise VaultUnavailable('The credential vault is not configured or accessible.') from None


def seal(group_id, purpose, value):
    payload = json.dumps({'group': group_id, 'purpose': purpose, 'value': value}).encode()
    return _cipher().encrypt(payload).decode()


def unseal(group_id, purpose, ciphertext):
    from cryptography.fernet import InvalidToken
    try:
        payload = json.loads(_cipher().decrypt(ciphertext.encode()))
        if payload['group'] != group_id or payload['purpose'] != purpose:
            raise ValueError('Credential binding mismatch')
        return payload['value']
    except (InvalidToken, ValueError, KeyError, TypeError, AttributeError):
        raise VaultUnavailable('The credential could not be decrypted for this band and purpose.') from None


def rotate(ciphertext):
    from cryptography.fernet import InvalidToken
    try:
        return _cipher().rotate(ciphertext.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        raise VaultUnavailable('Credential rotation failed.') from None
