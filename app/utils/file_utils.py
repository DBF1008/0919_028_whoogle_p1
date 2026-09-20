import json
import os
import tempfile
import threading
from contextlib import contextmanager

# Per-path locks shared process-wide, so that concurrent requests operating
# on the same file (session files, key files, ...) are serialized.
_locks = {}
_locks_guard = threading.Lock()


def _get_lock(path):
    abs_path = os.path.abspath(path)
    with _locks_guard:
        lock = _locks.get(abs_path)
        if lock is None:
            lock = threading.Lock()
            _locks[abs_path] = lock
    return lock


@contextmanager
def file_lock(path):
    """Serializes access to a file path across threads."""
    lock = _get_lock(path)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def atomic_write(path, content, encoding='utf-8'):
    """Writes content to a file atomically.

    The content is first written to a temporary file in the same directory
    and then moved over the target with os.replace, so a crash mid-write
    can never leave a partially written file behind.
    """
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix='.tmp_')
    try:
        with os.fdopen(fd, 'w', encoding=encoding) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def safe_read_json(path):
    """Reads and parses a JSON file under a per-path lock.

    Returns None if the file is missing, unreadable, or contains invalid
    or partially written JSON, instead of raising.
    """
    with file_lock(path):
        try:
            with open(path, 'r', encoding='utf-8') as json_file:
                return json.load(json_file)
        except (OSError, ValueError):
            return None
