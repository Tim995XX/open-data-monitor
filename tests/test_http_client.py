import pytest
import requests

from open_data_monitor.http_client import DEFAULT_USER_AGENT, get_with_retry, make_session
from conftest import FakeResponse, FakeSession


def test_erfolg_nach_fehlversuchen_mit_backoff():
    waits = []
    session = FakeSession(
        requests.ConnectionError("weg"),
        FakeResponse(status=503),
        FakeResponse(json_data={"ok": True}),
    )
    result = get_with_retry(session, "https://x", max_retries=3, sleep=waits.append,
                            parse=lambda r: r.json())
    assert result == {"ok": True}
    assert waits == [2, 4]  # exponentielles Backoff


def test_abbruch_nach_max_versuchen():
    session = FakeSession(*[requests.Timeout("zu langsam")] * 3)
    with pytest.raises(RuntimeError, match="Abbruch nach 3 Versuchen"):
        get_with_retry(session, "https://x", max_retries=3, sleep=lambda s: None)
    assert len(session.calls) == 3


def test_kaputtes_json_wird_wiederholt():
    session = FakeSession(FakeResponse(json_data=None), FakeResponse(json_data=[1]))
    assert get_with_retry(session, "https://x", sleep=lambda s: None,
                          parse=lambda r: r.json()) == [1]


def test_andere_fehler_aus_parse_werden_nicht_wiederholt():
    def parse(response):
        raise RuntimeError("fachlicher Fehler")

    session = FakeSession(FakeResponse(json_data={}))
    with pytest.raises(RuntimeError, match="fachlicher Fehler"):
        get_with_retry(session, "https://x", parse=parse, sleep=lambda s: None)


def test_user_agent_standard_und_ueberschreibbar(monkeypatch):
    monkeypatch.delenv("OPEN_DATA_MONITOR_USER_AGENT", raising=False)
    assert make_session().headers["User-Agent"] == DEFAULT_USER_AGENT
    monkeypatch.setenv("OPEN_DATA_MONITOR_USER_AGENT", "test-agent/1.0")
    assert make_session().headers["User-Agent"] == "test-agent/1.0"
    assert "@" not in DEFAULT_USER_AGENT  # keine persoenliche Adresse im Code
