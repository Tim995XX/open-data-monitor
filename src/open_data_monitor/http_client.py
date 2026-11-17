"""
Gemeinsamer HTTP-Baustein fuer alle Scraper.

Vorher hatte jeder Scraper seine eigene Session und seine eigene
Wiederholungsschleife (govdata: api_call, gesetze: http_get) - beide mit
demselben Muster: bis zu N Versuche, nach jedem Fehler 2, 4, 8 ... Sekunden
warten (exponentielles Backoff). Diese Schleife steht jetzt nur noch hier.
"""

import logging
import os
import time
from typing import Callable, TypeVar

import requests

log = logging.getLogger(__name__)

T = TypeVar("T")

# Kein persoenlicher Kontakt im Code. Wer den Quellen eine Kontaktmoeglichkeit
# mitgeben moechte, setzt die Umgebungsvariable OPEN_DATA_MONITOR_USER_AGENT.
DEFAULT_USER_AGENT = "open-data-monitor/1.0 (Lernprojekt, Open-Data-Abgleich)"


def make_session(user_agent: str | None = None) -> requests.Session:
    """Erzeugt eine Session mit eigenem User-Agent (wiederverwendete Verbindungen)."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent
        or os.environ.get("OPEN_DATA_MONITOR_USER_AGENT")
        or DEFAULT_USER_AGENT,
    })
    return session


def get_with_retry(
    session: requests.Session,
    url: str,
    params: dict | None = None,
    *,
    timeout: float = 30,
    max_retries: int = 3,
    parse: Callable[[requests.Response], T] = lambda response: response,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """GET mit Wiederholung und exponentiellem Backoff.

    parse wird noch innerhalb der Wiederholungsschleife aufgerufen. Wirft es
    einen ValueError (z. B. kaputtes JSON), wird der Abruf ebenfalls
    wiederholt. Andere Fehler aus parse werden sofort weitergereicht.
    sleep ist austauschbar, damit Tests nicht wirklich warten muessen.
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return parse(response)
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            wait = 2 ** attempt
            log.warning("Fehler bei %s (Versuch %d/%d): %s -> warte %ds",
                        url, attempt, max_retries, exc, wait)
            sleep(wait)

    raise RuntimeError(f"Abbruch nach {max_retries} Versuchen fuer {url}: {last_error}")
