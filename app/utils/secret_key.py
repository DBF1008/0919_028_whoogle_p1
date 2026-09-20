"""Secret key loading with atomic persistence and corruption recovery.

The Flask ``SECRET_KEY`` signs session cookies, and a crash while writing the
persisted key would previously leave a truncated key file behind. Every
subsequent startup would read the truncated value, and the encrypted search
links tied to the previously stored key would stop working. This module
writes the key atomically and quarantines any unreadable/short key file
before regenerating a new one, so a torn file is recovered from
automatically.
"""

import os
from base64 import b64encode

from app.utils.fileio import atomic_write, quarantine_file, read_text

# Flask requires at least 32 bytes of entropy for a secure secret key.
MIN_KEY_LENGTH = 32


def load_or_create_secret_key(config_path, env_var='WHOOGLE_SECRET_KEY'):
    """Return a valid secret key, persisting it when freshly generated.

    Priority order:

    1. The ``env_var`` environment variable (when at least 32 characters).
    2. An existing, valid key file at ``<config_path>/whoogle.key``.
    3. A newly generated key, written to the key file atomically.

    Corrupt or too-short key files are quarantined before regenerating.

    Args:
        config_path: Directory in which ``whoogle.key`` lives.
        env_var: Environment variable to consult first.

    Returns:
        tuple[str, list[str]]: The secret key and a list of human-readable
        warning messages (empty when everything is healthy).
    """
    warnings = []

    env_key = os.getenv(env_var, '').strip()
    if env_key:
        if len(env_key) >= MIN_KEY_LENGTH:
            return env_key, warnings
        warnings.append(
            f"{env_var} too short ({len(env_key)} chars, need "
            f"{MIN_KEY_LENGTH}+). Using file/generated key instead.")

    key_path = os.path.join(config_path, 'whoogle.key')

    if os.path.exists(key_path):
        try:
            file_key = read_text(key_path)
            if len(file_key) >= MIN_KEY_LENGTH:
                return file_key, warnings
            warnings.append('Key file too short, regenerating')
            quarantine_file(
                key_path,
                quarantine_dir=os.path.join(config_path, 'quarantine'))
        except (PermissionError, OSError, UnicodeDecodeError) as exc:
            warnings.append(f'Could not read key file: {exc}')
            try:
                quarantine_file(
                    key_path,
                    quarantine_dir=os.path.join(config_path, 'quarantine'))
            except OSError:
                pass

    # 32 random bytes, base64 encoded -> a 44-character ASCII secret.
    new_key = b64encode(os.urandom(32)).decode('ascii')
    try:
        atomic_write(key_path, new_key)
    except OSError as exc:
        warnings.append(
            f'Could not save key file: {exc}. Key will not persist across '
            'restarts.')

    return new_key, warnings
