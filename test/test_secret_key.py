import os

from app import app, get_secret_key

KEY_FILE = 'whoogle.key'


def _use_tmp_config(monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, 'CONFIG_PATH', str(tmp_path))
    monkeypatch.delenv('WHOOGLE_SECRET_KEY', raising=False)
    return str(tmp_path / KEY_FILE)


def test_env_key_takes_priority(monkeypatch, tmp_path):
    monkeypatch.setitem(app.config, 'CONFIG_PATH', str(tmp_path))
    env_key = 'x' * 64
    monkeypatch.setenv('WHOOGLE_SECRET_KEY', env_key)
    assert get_secret_key() == env_key
    # No key file should be created when the env var is used
    assert not os.path.exists(tmp_path / KEY_FILE)


def test_short_env_key_falls_back_to_file(monkeypatch, tmp_path):
    key_path = _use_tmp_config(monkeypatch, tmp_path)
    monkeypatch.setenv('WHOOGLE_SECRET_KEY', 'too-short')
    key = get_secret_key()
    assert key != 'too-short'
    assert len(key) >= 32


def test_existing_valid_key_file_is_reused(monkeypatch, tmp_path):
    key_path = _use_tmp_config(monkeypatch, tmp_path)
    with open(key_path, 'w', encoding='utf-8') as f:
        f.write('k' * 44)
    assert get_secret_key() == 'k' * 44


def test_corrupt_key_file_is_backed_up_and_regenerated(monkeypatch, tmp_path):
    key_path = _use_tmp_config(monkeypatch, tmp_path)
    # Simulate a partially written key file left behind by a crashed process
    with open(key_path, 'w', encoding='utf-8') as f:
        f.write('partial')

    key = get_secret_key()
    assert len(key) >= 32

    # The corrupt file is preserved as a backup, not silently destroyed
    with open(key_path + '.corrupt', 'r', encoding='utf-8') as f:
        assert f.read() == 'partial'

    # The regenerated key is persisted and stable across calls
    with open(key_path, 'r', encoding='utf-8') as f:
        assert f.read() == key
    assert get_secret_key() == key


def test_generated_key_is_persisted_atomically(monkeypatch, tmp_path):
    key_path = _use_tmp_config(monkeypatch, tmp_path)
    key = get_secret_key()
    assert len(key) >= 32
    with open(key_path, 'r', encoding='utf-8') as f:
        assert f.read() == key
    # No temp files left behind by the atomic write
    assert [f for f in os.listdir(tmp_path) if f.startswith('.tmp_')] == []


def test_unwritable_key_file_still_returns_key(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)

    def failing_atomic_write(path, content):
        raise IOError('read-only filesystem')

    monkeypatch.setattr('app.atomic_write', failing_atomic_write)
    key = get_secret_key()
    assert len(key) >= 32
