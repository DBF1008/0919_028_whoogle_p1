import json
import os
import threading

from app import app
from app.routes import session_required


def _run_cleanup():
    """Runs the session_required cleanup logic inside a request context."""
    with app.test_request_context('/'):
        @session_required
        def dummy():
            return 'ok'
        return dummy()


def _write_session(session_dir, name, content):
    path = os.path.join(session_dir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return path


def test_cleanup_removes_invalid_sessions(tmp_path, monkeypatch):
    session_dir = str(tmp_path)
    monkeypatch.setitem(app.config, 'SESSION_FILE_DIR', session_dir)

    valid = _write_session(session_dir, 'valid.session',
                           json.dumps({'valid': 1}))
    invalid = _write_session(session_dir, 'invalid.session',
                             json.dumps({'expired': True}))
    oversized = _write_session(session_dir, 'big.session',
                               json.dumps({'expired': True})
                               + ' ' * (app.config['MAX_SESSION_SIZE'] + 1))
    partial = _write_session(session_dir, 'partial.session', '{"valid": tru')

    assert _run_cleanup() == 'ok'

    # Valid, oversized, and partially written files are left alone
    assert os.path.exists(valid)
    assert os.path.exists(oversized)
    assert os.path.exists(partial)
    # Invalid session file is removed
    assert not os.path.exists(invalid)


def test_cleanup_tolerates_concurrent_removal(tmp_path, monkeypatch):
    session_dir = str(tmp_path)
    monkeypatch.setitem(app.config, 'SESSION_FILE_DIR', session_dir)
    _write_session(session_dir, 'invalid.session', json.dumps({'old': 1}))

    real_remove = os.remove

    def racing_remove(path):
        # Simulate another request deleting the file first
        real_remove(path)
        raise FileNotFoundError(path)

    monkeypatch.setattr(os, 'remove', racing_remove)
    assert _run_cleanup() == 'ok'


def test_cleanup_tolerates_file_vanishing_before_stat(tmp_path, monkeypatch):
    session_dir = str(tmp_path)
    monkeypatch.setitem(app.config, 'SESSION_FILE_DIR', session_dir)
    _write_session(session_dir, 'ghost.session', json.dumps({'old': 1}))

    real_getsize = os.path.getsize

    def racing_getsize(path):
        if path.endswith('ghost.session'):
            raise FileNotFoundError(path)
        return real_getsize(path)

    monkeypatch.setattr(os.path, 'getsize', racing_getsize)
    assert _run_cleanup() == 'ok'


def test_cleanup_concurrent_requests(tmp_path, monkeypatch):
    session_dir = str(tmp_path)
    monkeypatch.setitem(app.config, 'SESSION_FILE_DIR', session_dir)
    for i in range(20):
        _write_session(session_dir, f'invalid-{i}.session',
                       json.dumps({'old': i}))
    _write_session(session_dir, 'valid.session', json.dumps({'valid': 1}))

    errors = []

    def worker():
        try:
            for _ in range(5):
                _run_cleanup()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert os.path.exists(os.path.join(session_dir, 'valid.session'))
    remaining = [f for f in os.listdir(session_dir)
                 if f.startswith('invalid-')]
    assert remaining == []
