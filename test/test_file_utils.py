import json
import os
import threading

import pytest

from app.utils.file_utils import atomic_write, file_lock, safe_read_json


def test_atomic_write_creates_file(tmp_path):
    target = tmp_path / 'out.txt'
    atomic_write(str(target), 'hello')
    assert target.read_text() == 'hello'


def test_atomic_write_overwrites(tmp_path):
    target = tmp_path / 'out.txt'
    target.write_text('old')
    atomic_write(str(target), 'new')
    assert target.read_text() == 'new'


def test_atomic_write_cleans_up_temp_on_failure(tmp_path, monkeypatch):
    target = tmp_path / 'out.txt'

    def failing_replace(src, dst):
        raise OSError('simulated crash during replace')

    monkeypatch.setattr(os, 'replace', failing_replace)
    with pytest.raises(OSError):
        atomic_write(str(target), 'data')

    # No leftover temp files and no target file after a failed write
    leftovers = [f for f in os.listdir(tmp_path) if f.startswith('.tmp_')]
    assert leftovers == []
    assert not target.exists()


def test_atomic_write_concurrent_writers(tmp_path):
    target = tmp_path / 'shared.txt'
    errors = []

    def writer(value):
        try:
            for _ in range(50):
                atomic_write(str(target), value)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(f'writer-{i}',))
               for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    # The file must always contain one complete write, never a partial one
    content = target.read_text()
    assert content.startswith('writer-')


def test_safe_read_json_valid(tmp_path):
    target = tmp_path / 'session.json'
    target.write_text(json.dumps({'valid': 1}))
    assert safe_read_json(str(target)) == {'valid': 1}


def test_safe_read_json_partial_write(tmp_path):
    # Simulates a file read while another process is mid-write
    target = tmp_path / 'session.json'
    target.write_text('{"valid": tru')
    assert safe_read_json(str(target)) is None


def test_safe_read_json_missing_file(tmp_path):
    assert safe_read_json(str(tmp_path / 'nope.json')) is None


def test_file_lock_serializes_access(tmp_path):
    counter = {'value': 0}

    def increment():
        for _ in range(100):
            with file_lock(str(tmp_path / 'counter')):
                current = counter['value']
                counter['value'] = current + 1

    threads = [threading.Thread(target=increment) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert counter['value'] == 400
