import json
import os
import threading
import time

import pytest

from app.utils.fileio import (
    IGNORED_FILE_SUFFIXES,
    FileLockTimeout,
    atomic_write,
    atomic_write_json,
    cleanup_sessions,
    file_lock,
    quarantine_file,
    read_json,
    read_text,
    safe_remove,
)


def _valid_session(path, name='sess_valid'):
    file_path = os.path.join(path, name)
    with open(file_path, 'w', encoding='utf-8') as handle:
        json.dump({'valid': True, 'uuid': 'abc'}, handle)
    return file_path


def _invalid_session(path, name='sess_invalid'):
    file_path = os.path.join(path, name)
    with open(file_path, 'w', encoding='utf-8') as handle:
        json.dump({'uuid': 'abc'}, handle)
    return file_path


def test_atomic_write_creates_file_and_no_temp_leftover(tmp_path):
    target = tmp_path / 'data.txt'
    atomic_write(str(target), 'hello world')
    assert target.read_text(encoding='utf-8') == 'hello world'
    assert [p.name for p in tmp_path.iterdir()] == ['data.txt']


def test_atomic_write_binary(tmp_path):
    target = tmp_path / 'blob.bin'
    payload = bytes(range(256))
    atomic_write(str(target), payload, mode='wb')
    assert target.read_bytes() == payload


def test_atomic_write_replaces_old_content_completely(tmp_path):
    target = tmp_path / 'data.txt'
    target.write_text('old content that is longer', encoding='utf-8')
    atomic_write(str(target), 'new')
    # A truncate+rewrite bug would leave trailing bytes behind
    assert target.read_text(encoding='utf-8') == 'new'


def test_atomic_write_cleans_temp_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / 'data.txt'

    def boom(*args, **kwargs):
        raise RuntimeError('disk full')

    monkeypatch.setattr('app.utils.fileio.os.replace', boom)
    with pytest.raises(RuntimeError):
        atomic_write(str(target), 'content')
    leftovers = [p.name for p in tmp_path.iterdir()]
    assert leftovers == []


def test_atomic_write_json_roundtrip(tmp_path):
    target = tmp_path / 'conf.json'
    payload = {'valid': True, 'nested': [1, 2, 3]}
    atomic_write_json(str(target), payload)
    assert read_json(str(target)) == payload


def test_read_text_strips_whitespace(tmp_path):
    target = tmp_path / 'k.txt'
    target.write_text('  secret  \n', encoding='utf-8')
    assert read_text(str(target)) == 'secret'


def test_read_text_enforces_max_size(tmp_path):
    target = tmp_path / 'big.txt'
    target.write_text('x' * 10, encoding='utf-8')
    with pytest.raises(ValueError):
        read_text(str(target), max_size=5)


def test_read_json_invalid_raises(tmp_path):
    target = tmp_path / 'broken.json'
    target.write_text('{"valid": true,', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        read_json(str(target))


def test_file_lock_serializes_threads(tmp_path):
    lock_path = str(tmp_path / 'op.lock')
    in_critical = {'count': 0}
    overlaps = []

    def worker():
        with file_lock(lock_path):
            overlaps.append(in_critical['count'])
            in_critical['count'] += 1
            time.sleep(0.05)
            in_critical['count'] -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert overlaps == [0, 0, 0, 0]


def test_file_lock_reentrant_timeout_from_second_thread(tmp_path):
    lock_path = str(tmp_path / 'op.lock')
    entered = threading.Event()

    def holder():
        with file_lock(lock_path):
            entered.set()
            time.sleep(0.3)

    thread = threading.Thread(target=holder)
    thread.start()
    entered.wait(1)
    with pytest.raises(FileLockTimeout):
        with file_lock(lock_path, timeout=0.1):
            pass
    thread.join()


def test_safe_remove_missing_file_returns_false(tmp_path):
    assert safe_remove(str(tmp_path / 'gone')) is False


def test_safe_remove_existing_file(tmp_path):
    target = tmp_path / 'x'
    target.write_text('x', encoding='utf-8')
    assert safe_remove(str(target)) is True
    assert not target.exists()


def test_quarantine_file_moves_aside(tmp_path):
    target = tmp_path / 'whoogle.key'
    target.write_text('partial', encoding='utf-8')
    new_path = quarantine_file(str(target), str(tmp_path / 'quarantine'))
    assert not target.exists()
    assert new_path is not None
    assert os.path.exists(new_path)
    with open(new_path, 'r', encoding='utf-8') as handle:
        assert handle.read() == 'partial'


def test_quarantine_missing_file_returns_none(tmp_path):
    assert quarantine_file(str(tmp_path / 'nope')) is None


def test_cleanup_removes_invalid_and_keeps_valid(tmp_path):
    valid = _valid_session(tmp_path)
    invalid = _invalid_session(tmp_path)
    removed = cleanup_sessions(str(tmp_path), max_size=4000)
    assert os.path.exists(valid)
    assert not os.path.exists(invalid)
    assert removed == [invalid]


def test_cleanup_removes_half_written_json(tmp_path):
    torn = tmp_path / 'sess_torn'
    torn.write_text('{"valid": true, "uuid": "ab', encoding='utf-8')
    removed = cleanup_sessions(str(tmp_path), max_size=4000)
    assert not torn.exists()
    assert removed == [str(torn)]


def test_cleanup_skips_oversized_files(tmp_path):
    big = _valid_session(tmp_path, name='sess_big')
    with open(big, 'w', encoding='utf-8') as handle:
        json.dump({'valid': True, 'padding': 'x' * 100}, handle)
    assert os.path.getsize(big) > 50
    cleanup_sessions(str(tmp_path), max_size=50)
    assert os.path.exists(big)


def test_cleanup_ignores_lock_and_temp_files(tmp_path):
    lock_file = tmp_path / '.cleanup.lock'
    lock_file.write_text('', encoding='utf-8')
    tmp_file = tmp_path / 'sess_new.tmp'
    tmp_file.write_text('{"valid":', encoding='utf-8')
    assert cleanup_sessions(str(tmp_path), max_size=4000) == []
    assert lock_file.exists()
    assert tmp_file.exists()


def test_cleanup_skips_directories_and_hidden_files(tmp_path):
    os.makedirs(str(tmp_path / 'subdir'))
    hidden = tmp_path / '.unrelated'
    hidden.write_text('garbage', encoding='utf-8')
    assert cleanup_sessions(str(tmp_path), max_size=4000) == []
    assert os.path.isdir(str(tmp_path / 'subdir'))
    assert hidden.exists()


def test_cleanup_is_safe_against_concurrent_deletes(tmp_path, monkeypatch):
    # Simulate another request removing each file between os.listdir() and
    # getsize(); no exception should escape and nothing is reported removed.
    _invalid_session(tmp_path)

    def vanished(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr('app.utils.fileio.os.path.getsize', vanished)
    assert cleanup_sessions(str(tmp_path), max_size=4000) == []


def test_cleanup_concurrent_calls_do_not_raise(tmp_path):
    _valid_session(tmp_path, 'sess_valid')
    _invalid_session(tmp_path, 'sess_invalid')
    errors = []

    def runner():
        try:
            for _ in range(5):
                cleanup_sessions(str(tmp_path), 4000, 2)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=runner) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert os.path.exists(os.path.join(str(tmp_path), 'sess_valid'))
    assert not os.path.exists(os.path.join(str(tmp_path), 'sess_invalid'))
    assert IGNORED_FILE_SUFFIXES
