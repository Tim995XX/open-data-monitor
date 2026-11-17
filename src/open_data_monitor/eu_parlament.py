"""
Scraper fuer Abstimmungen des Europaeischen Parlaments (Plenum).

Quelle: Parltrack (https://parltrack.org), eine offene Datenbank, die
namentliche Abstimmungen des EU-Parlaments direkt aus den offiziellen
"proces-verbal"-XML-Dokumenten von europarl.europa.eu aufbereitet
(jeder Datensatz enthaelt die Original-URL des EU-Parlaments als Beleg).

Statt zehntausende Einzelseiten der EU-Parlaments-Website abzufragen,
wird ein einziger, komprimierter Bulk-Dump aller Abstimmungen seit 2004
heruntergeladen (~11 MB), entpackt und gefiltert. Das ist deutlich
schneller und schonender als klassisches Seite-fuer-Seite-Scraping und
liefert dieselben Daten.

Gespeichert werden nur Abstimmungen ab MIN_DATE, aufgeschluesselt nach
Fraktion (Partei-Zugehoerigkeit), nicht nach einzelnen Abgeordneten.

Details: siehe docs/eu_parlament.md
"""

import json
import logging
import shutil
import sqlite3
import subprocess
from pathlib import Path

import requests

from . import db
from .http_client import get_with_retry

log = logging.getLogger(__name__)

DUMP_URL = "https://parltrack.org/dumps/ep_votes.json.zst"
MIN_DATE = "2015-01-01"
DB_FILENAME = "eu_abstimmungen.db"

CHOICE_LABELS = {"+": "dafuer", "-": "dagegen", "0": "enthaltung"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS votes (
    voteid          TEXT PRIMARY KEY,
    ts              TEXT,
    title           TEXT,
    doc             TEXT,
    epref           TEXT,
    source_url      TEXT,
    total_dafuer    INTEGER,
    total_dagegen   INTEGER,
    total_enthaltung INTEGER
);

CREATE TABLE IF NOT EXISTS vote_fraktionen (
    voteid      INTEGER,
    choice      TEXT,
    fraktion    TEXT,
    anzahl      INTEGER,
    PRIMARY KEY (voteid, choice, fraktion),
    FOREIGN KEY (voteid) REFERENCES votes(voteid)
);
"""


def download_dump(session: requests.Session, target: Path) -> None:
    log.info("Lade Abstimmungs-Dump von %s ...", DUMP_URL)
    response = get_with_retry(session, DUMP_URL, timeout=120, max_retries=3)
    target.write_bytes(response.content)
    log.info("  %.1f MB heruntergeladen.", len(response.content) / 1_000_000)


def decompress_dump(source: Path, target: Path) -> None:
    if not shutil.which("zstd"):
        raise RuntimeError(
            "Das Kommandozeilenwerkzeug 'zstd' wird benoetigt, ist aber "
            "nicht installiert (z. B. 'apt install zstd')."
        )
    log.info("Entpacke Dump ...")
    subprocess.run(
        ["zstd", "-d", "-f", str(source), "-o", str(target)],
        check=True,
        capture_output=True,
    )


def filter_votes(all_votes: list[dict], min_date: str = MIN_DATE) -> list[dict]:
    """Nur Abstimmungen ab min_date (ISO-Datum, Textvergleich reicht)."""
    return [r for r in all_votes if (r.get("ts") or "") >= min_date]


def init_db(path: str | Path) -> sqlite3.Connection:
    return db.connect(path, SCHEMA)


def vote_row(rec: dict) -> tuple:
    """Normalisiert eine Parltrack-Abstimmung zu einer Zeile der Tabelle votes."""
    votes = rec.get("votes", {})
    totals = {k: (votes.get(k) or {}).get("total", 0) for k in ("+", "-", "0")}

    epref = rec.get("epref")
    if isinstance(epref, list):
        epref = ", ".join(epref)

    return (
        str(rec["voteid"]), rec.get("ts"), rec.get("title"),
        rec.get("doc"), epref, rec.get("url"),
        totals["+"], totals["-"], totals["0"],
    )


def fraktion_rows(rec: dict) -> list[tuple]:
    """(voteid, Wahl, Fraktion, Anzahl Mitglieder) je Fraktion und Wahl."""
    voteid = str(rec["voteid"])
    rows = []
    for choice, block in rec.get("votes", {}).items():
        if not isinstance(block, dict):
            continue
        label = CHOICE_LABELS.get(choice, choice)
        for fraktion, mitglieder in block.get("groups", {}).items():
            rows.append((voteid, label, fraktion, len(mitglieder)))
    return rows


def store_vote(conn: sqlite3.Connection, rec: dict) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO votes
        (voteid, ts, title, doc, epref, source_url,
         total_dafuer, total_dagegen, total_enthaltung)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        vote_row(rec),
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO vote_fraktionen
        (voteid, choice, fraktion, anzahl)
        VALUES (?, ?, ?, ?)
        """,
        fraktion_rows(rec),
    )


def store_votes(conn: sqlite3.Connection, records: list[dict]) -> None:
    for i, rec in enumerate(records, start=1):
        store_vote(conn, rec)
        if i % 5000 == 0:
            conn.commit()
            log.info("  %d/%d gespeichert", i, len(records))
    conn.commit()


def run(db_path: str | Path, session: requests.Session, work_dir: Path,
        min_date: str = MIN_DATE) -> None:
    """Dump laden, entpacken, filtern, speichern.

    work_dir nimmt die grossen Zwischendateien auf (.zst und entpacktes JSON,
    zusammen mehrere hundert MB). Die CLI uebergibt ein temporaeres
    Verzeichnis, das danach automatisch geloescht wird.
    """
    compressed = work_dir / "ep_votes.json.zst"
    decompressed = work_dir / "ep_votes.json"

    download_dump(session, compressed)
    decompress_dump(compressed, decompressed)

    log.info("Lese und filtere Abstimmungen (ab %s) ...", min_date)
    with open(decompressed, "r", encoding="utf-8") as f:
        all_votes = json.load(f)

    relevant = filter_votes(all_votes, min_date)
    log.info("  %d von %d Abstimmungen ab %s.", len(relevant), len(all_votes), min_date)
    del all_votes  # Speicher freigeben, das entpackte JSON ist gross

    conn = init_db(db_path)
    store_votes(conn, relevant)
    conn.close()

    log.info("Fertig. %d Abstimmungen in '%s' gespeichert.", len(relevant), db_path)
