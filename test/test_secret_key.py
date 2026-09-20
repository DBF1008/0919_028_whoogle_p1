import os

from app.utils.secret_key import MIN_KEY_LENGTH, load_or_create_secret_key


def test_generates_key_and_persists_atomically(tmp_path):
    key, warnings = load_or_create_secret_key(str(tmp_path))
    assert len(key) >= MIN_KEY_LENGTH
    assert warnings == []

    key_path = tmp_path / 'whoogle.key'
    assert key_path.exists()
    assert key_path.read_text(encoding='utf-8') == key
    # Atomic writes leave no temp files behind.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != 'whoogle.key']
    assert leftovers == []


def test_generated_key_is_reused_on_next_load(tmp_path):
    first, _ = load_or_create_secret_key(str(tmp_path))
    second, warnings = load_or_create_secret_key(str(tmp_path))
    assert first == second
    assert warnings == []


def test_env_key_takes_precedence(tmp_path, monkeypatch):
    env_key = 'x' * 40
    monkeypatch.setenv('WHOOGLE_SECRET_KEY', env_key)
    key, warnings = load_or_create_secret_key(str(tmp_path))
    assert key == env_key
    assert warnings == []
    # No file is created when the env key is valid.
    assert not (tmp_path / 'whoogle.key').exists()


def test_short_env_key_falls_back_to_file(tmp_path, monkeypatch):
    monkeypatch.setenv('WHOOGLE_SECRET_KEY', 'short')
    key, warnings = load_or_create_secret_key(str(tmp_path))
    assert len(key) >= MIN_KEY_LENGTH
    assert len(warnings) == 1
    assert 'too short' in warnings[0]


def test_truncated_key_file_is_quarantined_and_regenerated(tmp_path):
    key_path = tmp_path / 'whoogle.key'
    key_path.write_text('partial-key-fro', encoding='utf-8')

    key, warnings = load_or_create_secret_key(str(tmp_path))
    assert len(key) >= MIN_KEY_LENGTH
    assert warnings == ['Key file too short, regenerating']

    # A fresh valid key file replaces the corrupt one.
    assert key_path.read_text(encoding='utf-8') == key
    quarantine = tmp_path / 'quarantine'
    quarantined = list(quarantine.iterdir())
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding='utf-8') == 'partial-key-fro'

    # A subsequent load is warning-free and stable.
    again, again_warnings = load_or_create_secret_key(str(tmp_path))
    assert again == key
    assert again_warnings == []


def test_corrupt_bytes_in_key_file_are_recovered(tmp_path):
    key_path = tmp_path / 'whoogle.key'
    # Invalid UTF-8 simulates a torn write at the byte level.
    key_path.write_bytes(b'\xff\xfe\x00\x01' + os.urandom(16))

    key, warnings = load_or_create_secret_key(str(tmp_path))
    assert len(key) >= MIN_KEY_LENGTH
    assert key_path.read_text(encoding='utf-8') == key
    assert len(warnings) == 1
    assert 'Could not read key file' in warnings[0]


def test_unwritable_directory_returns_ephemeral_key(tmp_path, monkeypatch):
    config_dir = tmp_path / 'config'
    config_dir.mkdir()

    def deny_write(path, content, **kwargs):
        raise PermissionError('denied')

    monkeypatch.setattr('app.utils.secret_key.atomic_write', deny_write)

    key, warnings = load_or_create_secret_key(str(config_dir))
    assert len(key) >= MIN_KEY_LENGTH
    assert len(warnings) == 1
    assert 'Could not save key file' in warnings[0]

    # A second call also fails to persist but keeps returning usable keys.
    key_two, _ = load_or_create_secret_key(str(config_dir))
    assert len(key_two) >= MIN_KEY_LENGTH
