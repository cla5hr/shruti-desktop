import io

import pytest

from shruti_core.storage import LocalFSStorage


def test_save_open_roundtrip(tmp_path):
    s = LocalFSStorage(tmp_path)
    written = s.save("m1/original.mp3", io.BytesIO(b"hello audio"))
    assert written == 11
    assert s.exists("m1/original.mp3")
    assert s.size("m1/original.mp3") == 11
    with s.open("m1/original.mp3") as f:
        assert f.read() == b"hello audio"


def test_no_partial_files_on_save(tmp_path):
    s = LocalFSStorage(tmp_path)
    s.save("m1/a.bin", io.BytesIO(b"x" * 100))
    leftovers = [p.name for p in (tmp_path / "m1").iterdir()]
    assert leftovers == ["a.bin"]  # no .part files under the final key


def test_delete_is_idempotent(tmp_path):
    s = LocalFSStorage(tmp_path)
    s.save("m1/a.bin", io.BytesIO(b"x"))
    s.delete("m1/a.bin")
    s.delete("m1/a.bin")  # no error
    assert not s.exists("m1/a.bin")


def test_key_escaping_root_is_rejected(tmp_path):
    s = LocalFSStorage(tmp_path)
    with pytest.raises(ValueError):
        s.path("../outside.txt")


def test_iter_keys(tmp_path):
    s = LocalFSStorage(tmp_path)
    s.save("m1/a.bin", io.BytesIO(b"x"))
    s.save("m2/b.bin", io.BytesIO(b"y"))
    assert sorted(s.iter_keys()) == ["m1/a.bin", "m2/b.bin"]
    assert list(s.iter_keys("m1")) == ["m1/a.bin"]
