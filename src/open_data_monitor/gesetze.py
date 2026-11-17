"""
Scraper fuer Bundesgesetze und -verordnungen von rechtsinformationen.bund.de.

Nutzt die (noch in der Testphase befindliche) API des Rechtsinformationsportals
des Bundes. Die API liefert pro Gesetz nur die AKTUELLE Fassung - fruehere
Fassungen gibt es dort (noch) nicht. Deshalb baut dieses Modul die Historie
selbst auf: Bei jedem Lauf wird die Liste aller Gesetze geholt, und jede
Fassung, die noch nicht in der DB ist, wird als XML geladen und Paragraph fuer
Paragraph gespeichert. Aeltere Fassungen bleiben erhalten, so dass man spaeter
vergleichen kann, welche Paragraphen sich wie geaendert haben
(siehe gesetze_aenderungen.py).

Die Historie beginnt also mit dem ersten Lauf. Je regelmaessiger der Abgleich
laeuft (z. B. taeglich oder woechentlich), desto feiner die Aenderungsschritte.

Details: siehe docs/gesetze.md
"""

import hashlib
import logging
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from . import db
from .http_client import get_with_retry

log = logging.getLogger(__name__)

BASE_URL = "https://testphase.rechtsinformationen.bund.de"
PAGE_SIZE = 300                 # Maximum der API
DB_FILENAME = "gesetze.db"
REQUEST_DELAY_SECONDS = 0.2     # API erlaubt 600 Anfragen/Minute, wir bleiben bei max. 300
MAX_RETRIES = 4
MIN_LIVE_SHARE = 0.9            # Liefert die Liste weniger als 90 % der bekannten Gesetze,
                                # wird nichts als entfernt markiert (Schutz vor API-Aussetzern)

AKN = "{http://Inhaltsdaten.LegalDocML.de/1.8.2/}"

SCHEMA = """
-- Ein Gesetz bzw. eine Verordnung ("Werk"), unabhaengig von der Fassung
CREATE TABLE IF NOT EXISTS laws (
    work_eli            TEXT PRIMARY KEY,   -- z. B. eli/bund/bgbl-1/2004/s1950
    abbreviation        TEXT,               -- z. B. AufenthG
    name                TEXT,
    legislation_date    TEXT,               -- Ausfertigung der Stammfassung
    date_published      TEXT,               -- Verkuendung der Stammfassung
    first_seen          TEXT,
    removed_at          TEXT
);

-- Eine Fassung eines Gesetzes. Neue Zeile = Gesetz wurde geaendert.
CREATE TABLE IF NOT EXISTS versions (
    expression_eli      TEXT PRIMARY KEY,   -- z. B. eli/bund/bgbl-1/2004/s1950/2026-07-29/1/deu
    work_eli            TEXT,
    version_date        TEXT,               -- Datum der Fassung (aus der ELI)
    legal_force         TEXT,               -- InForce / NotInForce
    last_amended_by     TEXT,               -- "zuletzt geaendert durch Art. 1 G v. ..."
    num_norms           INTEGER,
    xml_url             TEXT,
    fetched_at          TEXT,
    FOREIGN KEY (work_eli) REFERENCES laws(work_eli)
);

-- Paragraphen/Artikel je Fassung. Der Text liegt dedupliziert in
-- norm_texts, damit unveraenderte Paragraphen nicht bei jeder neuen
-- Fassung erneut Platz belegen.
CREATE TABLE IF NOT EXISTS norms (
    expression_eli      TEXT,
    eid                 TEXT,               -- stabile Kennung im Dokument, z. B. art-z1
    position            INTEGER,            -- Reihenfolge im Gesetz
    num                 TEXT,               -- z. B. "§ 1" oder "Art 3"
    heading             TEXT,
    text_hash           TEXT,
    PRIMARY KEY (expression_eli, eid),
    FOREIGN KEY (expression_eli) REFERENCES versions(expression_eli),
    FOREIGN KEY (text_hash) REFERENCES norm_texts(hash)
);

CREATE TABLE IF NOT EXISTS norm_texts (
    hash    TEXT PRIMARY KEY,
    text    TEXT
);

CREATE INDEX IF NOT EXISTS idx_versions_work ON versions (work_eli, version_date);
"""


def http_get(session: requests.Session, path: str, params: dict | None = None) -> requests.Response:
    """GET mit Wiederholung. Die API antwortet bei Ueberlast mit 503."""
    return get_with_retry(session, BASE_URL + path, params, timeout=60, max_retries=MAX_RETRIES)


def init_db(path: str | Path) -> sqlite3.Connection:
    return db.connect(path, SCHEMA)


def parse_list_item(member: dict) -> tuple[str, dict]:
    """Normalisiert einen Eintrag der Gesetzesliste zu (work_eli, Fassungs-Infos)."""
    item = member["item"]
    work = item["exampleOfWork"]
    xml_url = next(
        (e["contentUrl"] for e in item.get("encoding", [])
         if e.get("encodingFormat") == "application/xml"),
        None,
    )
    return work["legislationIdentifier"], {
        "expression_eli": item["legislationIdentifier"],
        "abbreviation": item.get("abbreviation"),
        "name": item.get("name"),
        "legislation_date": work.get("legislationDate"),
        "date_published": work.get("datePublished"),
        "legal_force": item.get("legislationLegalForce"),
        "xml_url": xml_url,
    }


def fetch_live_index(session: requests.Session, max_laws: int | None) -> dict[str, dict]:
    """Holt die Liste aller Gesetze mit ihrer aktuellen Fassung, Schluessel = work_eli."""
    live = {}
    page = 0
    total = None

    while total is None or page * PAGE_SIZE < total:
        data = http_get(session, "/v1/legislation", {"size": PAGE_SIZE, "pageIndex": page}).json()
        total = data["totalItems"]

        for member in data["member"]:
            work_eli, info = parse_list_item(member)
            live[work_eli] = info
            if max_laws and len(live) >= max_laws:
                return live

        log.info("  Liste: %d/%d", len(live), total)
        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    if len(live) < total:
        log.warning("  API meldet %d Gesetze, erhalten wurden %d "
                    "(doppelte Eintraege oder sich verschiebende Seiten)", total, len(live))
    return live


def version_date_from_eli(expression_eli: str) -> str:
    """eli/bund/bgbl-1/2004/s1950/2026-07-29/1/deu -> 2026-07-29"""
    return expression_eli.split("/")[-3]


def _clean_text(element: ET.Element) -> str:
    """Gesamten Text eines Elements, Leerraum zu einzelnen Leerzeichen zusammengefasst."""
    return " ".join(" ".join(element.itertext()).split())


def parse_norms(xml_bytes: bytes) -> tuple[list[dict], str | None]:
    """Zerlegt das LegalDocML-XML in Paragraphen/Artikel.
    Gibt (Liste der Normen, 'zuletzt geaendert durch'-Vermerk) zurueck."""
    root = ET.fromstring(xml_bytes)

    # Artikel, die in anderen Artikeln stecken (z. B. zitierte Aenderungsbefehle),
    # gehoeren zu ihrem aeusseren Artikel und werden nicht einzeln gezaehlt.
    articles = list(root.iter(AKN + "article"))
    nested = {
        inner for art in articles for inner in art.iter(AKN + "article") if inner is not art
    }

    norms = []
    for art in articles:
        if art in nested:
            continue
        num = art.find(AKN + "num")
        heading = art.find(AKN + "heading")
        # Ein Absatz pro Zeile, damit Diffs spaeter lesbar sind
        lines = [
            _clean_text(child)
            for child in art
            if child.tag not in (AKN + "num", AKN + "heading")
        ]
        norms.append({
            "eid": art.get("eId"),
            "num": _clean_text(num) if num is not None else None,
            "heading": _clean_text(heading) if heading is not None else None,
            "text": "\n".join(line for line in lines if line),
        })

    match = re.search(r"zuletzt geändert durch[^<]*", xml_bytes.decode("utf-8", errors="replace"))
    last_amended_by = " ".join(match.group(0).split()) if match else None
    return norms, last_amended_by


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_version(conn: sqlite3.Connection, work_eli: str, info: dict,
                 norms: list[dict], last_amended_by: str | None) -> int:
    """Speichert eine bereits zerlegte Fassung mit allen Paragraphen."""
    conn.execute(
        """
        INSERT OR REPLACE INTO versions
        (expression_eli, work_eli, version_date, legal_force, last_amended_by,
         num_norms, xml_url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            info["expression_eli"], work_eli, version_date_from_eli(info["expression_eli"]),
            info["legal_force"], last_amended_by, len(norms), info["xml_url"], db.utc_now(),
        ),
    )
    conn.execute("DELETE FROM norms WHERE expression_eli = ?", (info["expression_eli"],))

    for position, norm in enumerate(norms):
        h = text_hash(norm["text"])
        conn.execute(
            "INSERT OR IGNORE INTO norm_texts (hash, text) VALUES (?, ?)",
            (h, norm["text"]),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO norms
            (expression_eli, eid, position, num, heading, text_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (info["expression_eli"], norm["eid"], position, norm["num"], norm["heading"], h),
        )
    return len(norms)


def store_version(conn: sqlite3.Connection, session: requests.Session, work_eli: str, info: dict) -> int:
    """Laedt das XML einer Fassung und speichert sie mit allen Paragraphen."""
    xml_bytes = http_get(session, info["xml_url"]).content
    norms, last_amended_by = parse_norms(xml_bytes)
    return save_version(conn, work_eli, info, norms, last_amended_by)


def upsert_law(conn: sqlite3.Connection, work_eli: str, info: dict) -> None:
    conn.execute(
        """
        INSERT INTO laws (work_eli, abbreviation, name, legislation_date, date_published, first_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (work_eli) DO UPDATE SET
            abbreviation = excluded.abbreviation,
            name = excluded.name,
            removed_at = NULL
        """,
        (work_eli, info["abbreviation"], info["name"],
         info["legislation_date"], info["date_published"], db.utc_now()),
    )


def mark_removed(conn: sqlite3.Connection, live: dict, partial_run: bool) -> int:
    """Gesetze, die nicht mehr gelistet sind, als entfernt markieren -
    aber nur bei einem vollstaendigen Lauf und plausibler Listenlaenge."""
    active = conn.execute("SELECT work_eli FROM laws WHERE removed_at IS NULL").fetchall()
    if partial_run:
        log.info("  Probelauf: Entfernt-Markierung uebersprungen")
        return 0
    if len(live) < MIN_LIVE_SHARE * len(active):
        log.warning("  nur %d von %d bekannten Gesetzen gelistet - "
                    "Entfernt-Markierung uebersprungen", len(live), len(active))
        return 0
    now = db.utc_now()
    gone = [(now, w) for (w,) in active if w not in live]
    conn.executemany("UPDATE laws SET removed_at = ? WHERE work_eli = ?", gone)
    conn.commit()
    return len(gone)


def run(db_path: str | Path, session: requests.Session, max_laws: int | None = None) -> None:
    """Kompletter Abgleich. max_laws begrenzt auf die ersten N Gesetze (Probelauf)."""
    conn = init_db(db_path)

    log.info("Hole Liste aller Gesetze...")
    live = fetch_live_index(session, max_laws)
    known_versions = {row[0] for row in conn.execute("SELECT expression_eli FROM versions")}
    todo = [(w, info) for w, info in live.items() if info["expression_eli"] not in known_versions]
    log.info("  %d Gesetze live, davon %d mit neuer Fassung", len(live), len(todo))

    new_laws = 0
    failed = 0
    for i, (work_eli, info) in enumerate(todo, start=1):
        is_new = conn.execute("SELECT 1 FROM laws WHERE work_eli = ?", (work_eli,)).fetchone() is None
        upsert_law(conn, work_eli, info)
        if not info["xml_url"]:
            log.warning("  [%d/%d] %s: kein XML verfuegbar, uebersprungen", i, len(todo), info["abbreviation"])
            failed += 1
            continue
        try:
            n = store_version(conn, session, work_eli, info)
        except (RuntimeError, ET.ParseError) as exc:
            conn.rollback()
            log.error("  [%d/%d] %s: FEHLER %s", i, len(todo), info["abbreviation"], exc)
            failed += 1
            continue
        conn.commit()
        new_laws += is_new
        label = "neu" if is_new else "NEUE FASSUNG"
        log.info("  [%d/%d] %s (%s, Fassung %s, %d Normen)", i, len(todo),
                 info["abbreviation"] or work_eli, label,
                 version_date_from_eli(info["expression_eli"]), n)
        time.sleep(REQUEST_DELAY_SECONDS)

    removed = mark_removed(conn, live, partial_run=bool(max_laws))

    n_laws = conn.execute("SELECT COUNT(*) FROM laws WHERE removed_at IS NULL").fetchone()[0]
    n_versions = conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
    conn.close()

    log.info(
        "Fertig. %d Gesetze neu, %d neue Fassungen, %d als entfernt markiert, %d Fehler. "
        "'%s' enthaelt %d aktive Gesetze in %d Fassungen.",
        new_laws, len(todo) - new_laws - failed, removed, failed, db_path, n_laws, n_versions,
    )
