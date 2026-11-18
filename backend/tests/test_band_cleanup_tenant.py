"""Band-copy cleanup: per-tenant counter, correct prefix, never delete on a missing counter."""
from unittest.mock import MagicMock, patch

import pytest

from app.tasks import download_tasks as dt
from app.tasks import processing_tasks as pt


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self
        return queue

    def execute(self):
        for name, args, kwargs in self.ops:
            getattr(self.redis, name)(*args, **kwargs)
        self.ops = []


class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.hashes = {}
        self.ttl = {}

    def incrby(self, key, n):
        self.kv[key] = int(self.kv.get(key, 0)) + n
        return self.kv[key]

    def decr(self, key):
        return self.incrby(key, -1)

    def exists(self, key):
        return int(key in self.kv or key in self.hashes)

    def expire(self, key, seconds):
        self.ttl[key] = seconds
        return True

    def hset(self, key, mapping=None, **kw):
        self.hashes.setdefault(key, {}).update(mapping or {})

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.hashes.pop(k, None)

    def pipeline(self):
        return FakePipeline(self)


@pytest.fixture
def env():
    r = FakeRedis()
    storage = MagicMock()
    storage.delete_prefix.return_value = 3
    with patch("redis.from_url", return_value=r), \
         patch.object(pt, "_get_redis", return_value=r), \
         patch.object(pt, "create_storage_service", return_value=storage):
        yield r, storage


def _calls(storage):
    return [c.args for c in storage.delete_prefix.call_args_list]


def test_keys_and_prefix_helpers():
    assert dt.band_cleanup_key("acme", "S1") == "vegetation:bandclean:acme:S1"
    assert dt.band_copy_prefix("vegetation-prime/", "S1") == "vegetation-prime/scenes/S1/"
    assert dt.band_copy_prefix("vegetation-prime", "S1") == "vegetation-prime/scenes/S1/"
    assert dt.band_copy_prefix("", "S1") == "scenes/S1/"
    assert dt.band_copy_prefix("vegetation-prime/", "") == "vegetation-prime/scenes/"


def test_two_parcels_same_scene_deletes_after_last(env):
    r, storage = env
    dt._set_band_cleanup_counter("S1", "acme", "bucket-acme", "vegetation-prime/", 5)
    dt._set_band_cleanup_counter("S1", "acme", "bucket-acme", "vegetation-prime/", 5)
    assert r.kv["vegetation:bandclean:acme:S1"] == 10
    assert r.ttl["vegetation:bandclean:acme:S1"] == 259200
    for _ in range(9):
        assert pt._decrement_and_cleanup_bands("acme", "S1") is False
    storage.delete_prefix.assert_not_called()
    assert pt._decrement_and_cleanup_bands("acme", "S1") is True
    assert _calls(storage) == [("vegetation-prime/scenes/S1/", "bucket-acme")]
    assert not r.kv and not r.hashes


def test_tenants_do_not_share_counter(env):
    r, storage = env
    dt._set_band_cleanup_counter("S1", "acme", "bucket-acme", "vegetation-prime/", 1)
    dt._set_band_cleanup_counter("S1", "tenant-a", "bucket-a", "vegetation-prime/", 2)
    assert pt._decrement_and_cleanup_bands("acme", "S1") is True
    assert _calls(storage) == [("vegetation-prime/scenes/S1/", "bucket-acme")]
    assert r.kv["vegetation:bandclean:tenant-a:S1"] == 2
    assert pt._decrement_and_cleanup_bands("tenant-a", "S1") is False
    assert len(_calls(storage)) == 1
    assert pt._decrement_and_cleanup_bands("tenant-a", "S1") is True
    assert _calls(storage)[1] == ("vegetation-prime/scenes/S1/", "bucket-a")


@pytest.mark.parametrize("stored", ["vegetation-prime/", "vegetation-prime"])
def test_prefix_with_and_without_slash(env, stored):
    r, storage = env
    dt._set_band_cleanup_counter("S1", "acme", "bucket-acme", stored, 1)
    assert pt._decrement_and_cleanup_bands("acme", "S1") is True
    assert _calls(storage) == [("vegetation-prime/scenes/S1/", "bucket-acme")]


def test_missing_counter_never_deletes_bucket(env, tmp_path, caplog):
    r, storage = env
    d = tmp_path / "scene-dir"
    d.mkdir()
    with caplog.at_level("WARNING"):
        assert pt._decrement_and_cleanup_bands("acme", "S1", local_processing_dir=d) is False
    storage.delete_prefix.assert_not_called()
    assert "counter missing" in caplog.text
    assert "vegetation:bandclean:acme:S1" not in r.kv   # DECR must not have created -1
    assert not d.exists()                               # local dir is still freed
