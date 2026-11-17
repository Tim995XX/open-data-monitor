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
"""

import json
import sqlite3
import subprocess
import shutil
from pathlib import Path

import requests

DUMP_URL = "https://parltrack.org/dumps/ep_votes.json.zst"
MIN_DATE = "2015-01-01"
DB_PATH = "eu_abstimmungen.db"
WORK_DIR = Path(__file__).parent
COMPRESSED_PATH = WORK_DIR / "ep_votes.json.zst"
DECOMPRESSED_PATH = WORK_DIR / "ep_votes.json"

CHOICE_LABELS = {"+": "dafuer", "-": "dagegen", "0": "enthaltung"}


def download_dump() -> None:
    print(f"Lade Abstimmungs-Dump von {DUMP_URL} ...")
    response = requests.get(DUMP_URL, timeout=120)
    response.raise_for_status()
    COMPRESSED_PATH.write_bytes(response.content)
    print(f"  {len(response.content) / 1_000_000:.1f} MB heruntergeladen.")


def decompress_dump() -> None:
    if not shutil.which("zstd"):
        raise RuntimeError(
            "Das Kommandozeilenwerkzeug 'zstd' wird benoetigt, ist aber "
            "nicht installiert (z. B. 'apt install zstd')."
        )
    print("Entpacke Dump ...")
    subprocess.run(
        ["zstd", "-d", "-f", str(COMPRESSED_PATH), "-o", str(DECOMPRESSED_PATH)],
        check=True,
        capture_output=True,
    )


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
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
    )
    conn.commit()
    return conn


def store_vote(conn: sqlite3.Connection, rec: dict) -> None:
    voteid = str(rec["voteid"])
    votes = rec.get("votes", {})
    totals = {k: (votes.get(k) or {}).get("total", 0) for k in ("+", "-", "0")}

    epref = rec.get("epref")
    if isinstance(epref, list):
        epref = ", ".join(epref)

    conn.execute(
        """
        INSERT OR REPLACE INTO votes
        (voteid, ts, title, doc, epref, source_url,
         total_dafuer, total_dagegen, total_enthaltung)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            voteid, rec.get("ts"), rec.get("title"),
            rec.get("doc"), epref, rec.get("url"),
            totals["+"], totals["-"], totals["0"],
        ),
    )

    for choice, block in votes.items():
        if not isinstance(block, dict):
            continue
        label = CHOICE_LABELS.get(choice, choice)
        for fraktion, mitglieder in block.get("groups", {}).items():
            conn.execute(
                """
                INSERT OR REPLACE INTO vote_fraktionen
                (voteid, choice, fraktion, anzahl)
                VALUES (?, ?, ?, ?)
                """,
                (voteid, label, fraktion, len(mitglieder)),
            )


def main() -> None:
    download_dump()
    decompress_dump()

    print("Lese und filtere Abstimmungen (ab", MIN_DATE, ") ...")
    with open(DECOMPRESSED_PATH, "r", encoding="utf-8") as f:
        all_votes = json.load(f)

    relevant = [r for r in all_votes if (r.get("ts") or "") >= MIN_DATE]
    print(f"  {len(relevant)} von {len(all_votes)} Abstimmungen ab {MIN_DATE}.")

    conn = init_db(DB_PATH)
    for i, rec in enumerate(relevant, start=1):
        store_vote(conn, rec)
        if i % 5000 == 0:
            conn.commit()
            print(f"  {i}/{len(relevant)} gespeichert")
    conn.commit()
    conn.close()

    # Aufraeumen: die grossen Zwischendateien werden nicht dauerhaft gebraucht
    COMPRESSED_PATH.unlink(missing_ok=True)
    DECOMPRESSED_PATH.unlink(missing_ok=True)

    print(f"Fertig. {len(relevant)} Abstimmungen in '{DB_PATH}' gespeichert.")


if __name__ == "__main__":
    main()
