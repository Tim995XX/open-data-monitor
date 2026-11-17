"""Gemeinsame Test-Hilfen. Kein Test greift auf das Netzwerk zu."""

import pytest
import requests


class FakeResponse:
    """Minimaler Ersatz fuer requests.Response."""

    def __init__(self, json_data=None, content: bytes = b"", status: int = 200):
        self._json = json_data
        self.content = content
        self.status_code = status

    def json(self):
        if self._json is None:
            raise ValueError("kein JSON")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Liefert der Reihe nach die vorbereiteten Antworten (oder wirft Ausnahmen)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def no_sleep(monkeypatch):
    """Pausen zwischen Anfragen in Tests abschalten."""
    monkeypatch.setattr("time.sleep", lambda seconds: None)
