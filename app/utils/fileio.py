"""Thread-safe and crash-safe filesystem helpers.

This module centralizes the file operations that need protection against
concurrent access:

* :func:`file_lock` - a reentrant per-path thread lock combined with an
  ``flock(2)`` advisory lock for cross-process mutual exclusion.
* :func:`atomic_write` / :func:`atomic_write_json` - write to a temporary file
  and atomically rename it into place, so readers never observe a partially
  written file even if the process crashes mid-write.
* :func:`read_json` - defensive JSON reads with size limits and explicit
  "torn file" detection.
* :func:`safe_remove` - idempotent file removal.
* :func:`quarantine_file` - move unreadable/corrupt files aside instead of
  deleting them outright, allowing the application to recover automatically
  while preserving the file for inspection.
* :func:`cleanup_sessions` - locked cleanup of the on-disk session directory.
"""

import errno
import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager

try:  # POSIX systems provide cross-process advisory locks via fcntl
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

logger = logging.getLogger(__name__)

# Files matching these suffixes are left untouched during session cleanup.
# They are either our own synchronization files or leftovers from a crash
# that happened before an atomic rename could complete.
IGNORED_FILE_SUFFIXES = ('.lock', '.tmp')

# In-process locks guarding each lock file. ``flock`` only synchronizes
# separate processes; threads inside a single process share the open file
# descriptor and also need mutual exclusion.
_thread_locks = {}
_thread_locks_guard = threading.Lock()


def _get_thread_lock(lock_path):
    with _thread_locks_guard:
        lock = _thread_locks.get(lock_path)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[lock_path] = lock
        return lock


class FileLockTimeout(OSError):
    """Raised when an advisory file lock cannot be acquired in time."""


@contextmanager
def file_lock(lock_path, timeout=10.0, poll_interval=0.05):
    """Acquire an exclusive, advisory lock scoped to ``lock_path``.

    The lock is mutual-exclusive across both threads of the current process
    (via a :class:`threading.Lock`) and other processes (via ``flock``). The
    underlying lock file is created if necessary.

    Args:
        lock_path: Path of the lock file to hold while the lock is active.
        timeout: Maximum seconds to wait for the lock before raising
            :class:`FileLockTimeout`.
        poll_interval: Seconds to sleep between lock acquisition attempts.

    Raises:
        FileLockTimeout: If the lock cannot be acquired within ``timeout``.
    """
    lock_dir = os.path.dirname(lock_path)
    if lock_dir and not os.path.exists(lock_dir):
        os.makedirs(lock_dir, exist_ok=True)

    thread_lock = _get_thread_lock(lock_path)
    deadline = time.monotonic() + timeout

    while not thread_lock.acquire(timeout=poll_interval):
        if time.monotonic() >= deadline:
            raise FileLockTimeout(
                errno.ETIMEDOUT,
                f"Timed out waiting for lock: {lock_path}",
            )

    lock_file = None
    acquired = False
    try:
        if fcntl is not None:
            # Use a dedicated file descriptor so flock() is paired with a
            # matching close(), even if ``lock_path`` happens to be opened
            # elsewhere at the same time.
            lock_file = open(lock_path, 'a+', encoding='utf-8')
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.monotonic() >= deadline:
                        raise FileLockTimeout(
                            errno.ETIMEDOUT,
                            f"Timed out waiting for lock: {lock_path}",
                        ) from exc
                    time.sleep(poll_interval)
        yield
    finally:
        if lock_file is not None:
            try:
                if acquired:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()
        thread_lock.release()


def atomic_write(path, content, mode='w', encoding='utf-8'):
    """Write ``content`` to ``path`` atomically.

    Data is first written to a temporary file in the same directory, flushed
    and fsynced, then moved into place with :func:`os.replace`. Concurrent
    readers therefore either see the old file or the complete new file, never
    a partially written one.

    Args:
        path: Destination file path.
        content: Data to write (``str`` for text mode, ``bytes`` otherwise).
        mode: File open mode forwarded to the temp file (default text mode).
        encoding: Text encoding (ignored for binary modes).
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        suffix='.tmp', prefix=f'.{os.path.basename(path)}.', dir=directory)
    try:
        if 'b' in mode:
            with os.fdopen(fd, mode) as tmp_file:
                tmp_file.write(content)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
        else:
            with os.fdopen(fd, mode, encoding=encoding) as tmp_file:
                tmp_file.write(content)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass


def atomic_write_json(path, data, encoding='utf-8'):
    """Serialize ``data`` to JSON and write it atomically to ``path``."""
    atomic_write(path, json.dumps(data), encoding=encoding)


def read_text(path, max_size=None, encoding='utf-8'):
    """Read a text file defensively.

    Args:
        path: File to read.
        max_size: Optional maximum file size in bytes; larger files raise
            :class:`ValueError`.
        encoding: Text encoding.

    Returns:
        str: The stripped contents of the file.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file exceeds ``max_size`` bytes.
        OSError: On other read failures.
    """
    if max_size is not None and os.path.getsize(path) > max_size:
        raise ValueError(f"File exceeds maximum size of {max_size} bytes: {path}")
    with open(path, 'r', encoding=encoding) as file_handle:
        return file_handle.read().strip()


def read_json(path, max_size=None, encoding='utf-8'):
    """Read and parse a JSON file defensively.

    Args:
        path: JSON file to read.
        max_size: Optional maximum file size in bytes.

    Returns:
        The parsed JSON content.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file exceeds ``max_size`` bytes.
        json.JSONDecodeError: If the file contains truncated/invalid JSON.
    """
    if max_size is not None and os.path.getsize(path) > max_size:
        raise ValueError(f"File exceeds maximum size of {max_size} bytes: {path}")
    with open(path, 'r', encoding=encoding) as file_handle:
        return json.load(file_handle)


def safe_remove(path):
    """Remove a file without raising when it is already gone.

    Returns:
        bool: True if the file was removed, False if it did not exist.
    """
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.exception("Failed to remove file: %s", path)
        return False


def quarantine_file(path, quarantine_dir=None):
    """Move a corrupt/unreadable file aside for later inspection.

    The file is renamed into ``quarantine_dir`` (a ``quarantine``
    subdirectory next to the file by default) using a timestamped name. This
    lets the application recover automatically on the next run while
    preserving evidence of the failure.

    Returns:
        str: The new path, or None if the source file had already vanished.
    """
    if quarantine_dir is None:
        quarantine_dir = os.path.join(os.path.dirname(path), 'quarantine')

    try:
        os.makedirs(quarantine_dir, exist_ok=True)
        timestamp = time.strftime('%Y%m%d%H%M%S')
        base_name = os.path.basename(path)
        destination = os.path.join(quarantine_dir, f'{base_name}.{timestamp}')
        counter = 1
        while os.path.exists(destination):
            destination = os.path.join(
                quarantine_dir, f'{base_name}.{timestamp}.{counter}')
            counter += 1
        os.replace(path, destination)
        logger.warning("Quarantined corrupt file %s -> %s", path, destination)
        return destination
    except FileNotFoundError:
        return None
    except OSError:
        logger.exception("Failed to quarantine file: %s", path)
        return None


def cleanup_sessions(session_dir, max_size, lock_timeout=10.0):
    """Remove invalid session files from ``session_dir`` under a global lock.

    A session file is considered valid when it parses as a JSON object
    containing a ``valid`` key. Files that fail to parse or do not contain the
    key are removed. Lock/synchronization files, temp files, hidden files and
    files larger than ``max_size`` are skipped, matching the historical
    behavior while making the operation safe under concurrency.

    Args:
        session_dir: Directory containing on-disk session files.
        max_size: Maximum accepted session file size in bytes.
        lock_timeout: Seconds to wait for the cleanup lock.

    Returns:
        list[str]: Paths of the session files that were removed.
    """
    removed = []
    os.makedirs(session_dir, exist_ok=True)
    lock_path = os.path.join(session_dir, '.cleanup.lock')

    try:
        lock_context = file_lock(lock_path, timeout=lock_timeout)
    except FileLockTimeout:
        # Another request/process is already performing cleanup; skip it.
        logger.warning("Skipping session cleanup, lock unavailable")
        return removed

    with lock_context:
        for entry in os.listdir(session_dir):
            file_path = os.path.join(session_dir, entry)

            if not os.path.isfile(file_path) or entry.startswith('.'):
                continue
            if entry.endswith(IGNORED_FILE_SUFFIXES):
                continue

            try:
                size = os.path.getsize(file_path)
            except FileNotFoundError:
                # The file was removed between listdir() and getsize().
                continue

            if size > max_size:
                continue

            try:
                with open(file_path, 'r', encoding='utf-8') as session_file:
                    data = json.load(session_file)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # A truncated or malformed session (possibly from an older,
                # non-atomic writer) is invalid and safe to remove.
                if safe_remove(file_path):
                    removed.append(file_path)
                continue
            except FileNotFoundError:
                continue
            except OSError:
                logger.exception("Unable to read session file: %s", file_path)
                continue

            if isinstance(data, dict) and 'valid' in data:
                continue

            if safe_remove(file_path):
                removed.append(file_path)

    return removed
