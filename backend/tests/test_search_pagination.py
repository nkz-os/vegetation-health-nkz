"""search_scenes follows the STAC `next` link and retries throttling."""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.services import copernicus_client as cc

GEOM = {"type": "Polygon", "coordinates": [[[-1.6, 42.8], [-1.5, 42.8], [-1.5, 42.9], [-1.6, 42.9], [-1.6, 42.8]]]}


def _feature(i):
    return {
        "id": f"scene-{i}",
        "properties": {"datetime": f"2026-05-{(i % 28) + 1:02d}T10:50:00Z", "eo:cloud_cover": 5},
        "geometry": GEOM,
        "assets": {},
        "links": [],
    }


def _page(ids, next_body=None):
    links = []
    if next_body is not None:
        links.append({"rel": "next", "method": "POST", "body": next_body})
    return {"features": [_feature(i) for i in ids], "links": links}


def _resp(payload=None, status=200, headers=None):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = payload
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(f"{status} error", response=r)
    else:
        r.raise_for_status.return_value = None
    return r


def _search(**kw):
    client = cc.CopernicusDataSpaceClient()
    return client.search_scenes(
        intersects=GEOM, start_date=date(2026, 5, 1), end_date=date(2026, 11, 1), **kw
    )


def test_follows_next_link_post_body():
    nxt = {"collections": ["sentinel-2-l2a"], "token": "next:abc", "limit": 100}
    with patch.object(cc.requests, "post", side_effect=[
        _resp(_page(range(20), nxt)), _resp(_page(range(20, 39))),
    ]) as post:
        scenes = _search()
    assert len(scenes) == 39
    assert post.call_count == 2
    assert post.call_args_list[1].kwargs["json"] == nxt
    assert post.call_args_list[1].kwargs["json"]["token"] == "next:abc"


def test_error_on_second_page_raises():
    nxt = {"token": "t"}
    with patch.object(cc.requests, "post", side_effect=[
        _resp(_page(range(20), nxt)), _resp(None, status=400),
    ]):
        with pytest.raises(Exception, match="Scene search failed"):
            _search()


def test_truncation_raises():
    nxt = {"token": "t"}
    with patch.object(cc.requests, "post", side_effect=[
        _resp(_page([i], nxt)) for i in range(5)
    ]):
        with pytest.raises(Exception, match="Scene search truncated after 3 pages"):
            _search(max_pages=3)


def test_max_pages_defaults_to_env_read_at_use_time(monkeypatch):
    monkeypatch.setenv("VEG_SEARCH_MAX_PAGES", "2")
    nxt = {"token": "t"}
    with patch.object(cc.requests, "post", side_effect=[
        _resp(_page([i], nxt)) for i in range(5)
    ]):
        with pytest.raises(Exception, match="truncated after 2 pages"):
            _search()


def test_single_page_unchanged():
    with patch.object(cc.requests, "post", return_value=_resp(_page(range(3)))) as post:
        scenes = _search()
    assert [s["id"] for s in scenes] == ["scene-0", "scene-1", "scene-2"]
    assert set(scenes[0]) == {"id", "sensing_date", "datetime", "cloud_cover", "geometry", "assets", "links"}
    assert post.call_count == 1


def test_duplicates_by_id_dropped():
    nxt = {"token": "t"}
    with patch.object(cc.requests, "post", side_effect=[
        _resp(_page([1, 2], nxt)), _resp(_page([2, 3])),
    ]):
        scenes = _search()
    assert [s["id"] for s in scenes] == ["scene-1", "scene-2", "scene-3"]


def test_429_retried_then_succeeds():
    with patch.object(cc.time, "sleep") as sleep, patch.object(cc.requests, "post", side_effect=[
        _resp(None, status=429), _resp(_page(range(2))),
    ]):
        scenes = _search()
    assert len(scenes) == 2
    sleep.assert_called_once_with(5)


def test_429_respects_retry_after():
    with patch.object(cc.time, "sleep") as sleep, patch.object(cc.requests, "post", side_effect=[
        _resp(None, status=429, headers={"Retry-After": "12"}), _resp(_page(range(2))),
    ]):
        _search()
    sleep.assert_called_once_with(12)


def test_429_exhausted_raises():
    with patch.object(cc.time, "sleep") as sleep, patch.object(
        cc.requests, "post", return_value=_resp(None, status=429)
    ) as post:
        with pytest.raises(Exception, match="Scene search failed"):
            _search()
    assert post.call_count == 4
    assert [c.args[0] for c in sleep.call_args_list] == [5, 10, 20]
