"""Open-Meteo URL / models configuration in WeatherService."""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.weather_service import WeatherService

ENV = ("OPENMETEO_API_URL", "OPENMETEO_ARCHIVE_URL", "OPENMETEO_MODELS", "OPENMETEO_ARCHIVE_MODELS")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)


def _svc():
    svc = WeatherService()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"daily": {"time": []}})
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    svc._client = client
    return svc, client


def _call(svc, days_ago):
    end = date.today() - timedelta(days=days_ago)
    asyncio.run(svc.get_historical_weather(40.0, -1.0, end - timedelta(days=3), end))


def test_defaults_and_no_models_param():
    svc, client = _svc()
    _call(svc, 0)
    url = client.get.call_args.args[0]
    assert url == "https://api.open-meteo.com/v1/forecast"
    assert "models" not in client.get.call_args.kwargs["params"]
    _call(svc, 30)
    assert client.get.call_args.args[0] == "https://archive-api.open-meteo.com/v1/archive"
    assert "models" not in client.get.call_args.kwargs["params"]


def test_custom_urls_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("OPENMETEO_API_URL", "http://om.local:8080/v1/")
    monkeypatch.setenv("OPENMETEO_ARCHIVE_URL", "http://om.local:8080/v1//")
    svc, client = _svc()
    _call(svc, 0)
    assert client.get.call_args.args[0] == "http://om.local:8080/v1/forecast"
    _call(svc, 30)
    assert client.get.call_args.args[0] == "http://om.local:8080/v1/archive"


def test_models_sent_only_when_set(monkeypatch):
    monkeypatch.setenv("OPENMETEO_MODELS", "ecmwf_ifs025, icon_seamless")
    monkeypatch.setenv("OPENMETEO_ARCHIVE_MODELS", "era5_seamless")
    svc, client = _svc()
    _call(svc, 0)
    assert client.get.call_args.kwargs["params"]["models"] == "ecmwf_ifs025,icon_seamless"
    _call(svc, 30)
    assert client.get.call_args.kwargs["params"]["models"] == "era5_seamless"


def test_blank_models_not_sent(monkeypatch):
    monkeypatch.setenv("OPENMETEO_MODELS", " , ")
    svc, client = _svc()
    _call(svc, 0)
    assert "models" not in client.get.call_args.kwargs["params"]
