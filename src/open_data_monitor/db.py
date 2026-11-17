"""
Gemeinsame SQLite-Helfer.

Jeder Scraper hat sein eigenes Schema (das ist fachlich verschieden und
bleibt in seinem Modul). Gemeinsam sind nur: Verbindung oeffnen (inkl.
Anlegen des Datenverzeichnisses), Schema anwenden, Zeitstempel erzeugen.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def connect(path: str | Path, schema: str | None = None) -> sqlite3.Connection:
    """Oeffnet (bzw. erzeugt) die SQLite-DB und legt bei Bedarf das Schema an."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    if schema:
        conn.executescript(schema)
        conn.commit()
    return conn


def utc_now() -> str:
    """Aktueller Zeitpunkt in UTC als ISO-8601-Text (so wird er in allen DBs gespeichert)."""
    return datetime.now(timezone.utc).isoformat()
