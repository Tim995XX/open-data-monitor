"""
Scraper fuer govdata.de - mehrere Themen-Kategorien gleichzeitig.

Ruft die offizielle CKAN-API von govdata.de ab und speichert die
Datensaetze folgender Kategorien in einer gemeinsamen SQLite-Datenbank,
sauber nach Kategorie zuordenbar:

    soci  Bevoelkerung und Gesellschaft
    educ  Bildung, Kultur und Sport
    heal  Gesundheit
    just  Justiz, Rechtssystem und oeffentliche Sicherheit
    econ  Wirtschaft und Finanzen
    tech  Wissenschaft und Technologie

Bei jedem Lauf wird pro Kategorie zuerst nur die Liste (ID +
Aenderungsdatum) aller Live-Datensaetze geholt und mit der DB verglichen.
Nur neue oder geaenderte Datensaetze werden komplett nachgeladen. Datensaetze,
die auf govdata.de geloescht wurden, bleiben in der DB, bekommen aber ein
Datum in "removed_at".

Details zum Vorgehen: siehe govdata_kategorien_erklaerung.txt
"""

import sqlite3
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://www.govdata.de/ckan/api/3/action/"

GROUPS = [
    ("soci", "Bevoelkerung und Gesellschaft"),
    ("educ", "Bildung, Kultur und Sport"),
    ("heal", "Gesundheit"),
    ("just", "Justiz, Rechtssystem und oeffentliche Sicherheit"),
    ("econ", "Wirtschaft und Finanzen"),
    ("tech", "Wissenschaft und Technologie"),
]

ROWS_PER_PAGE = 100
ID_ROWS_PER_PAGE = 1000   # Seitengroesse fuer den reinen ID-Abgleich
ID_BATCH_SIZE = 50        # IDs pro gezielter Abfrage (URL-Laenge)
FULL_FETCH_SHARE = 0.3    # ab diesem Anteil geaenderter Datensaetze: ganze Kategorie neu laden
DB_PATH = "govdata_kategorien.db"
REQUEST_DELAY_SECONDS = 0.7
MAX_RETRIES = 3
USER_AGENT = "govdata-scraper-lernprojekt/1.0"

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def api_call(action: str, params: dict) -> dict:
    """Ruft einen CKAN-Action-Endpunkt auf und gibt das 'result'-Feld zurueck."""
    url = BASE_URL + action
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if not payload.get("success"):
                raise RuntimeError(f"API meldet success=false: {payload}")
            return payload["result"]
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            wait = 2 ** attempt
            print(f"    Fehler bei {action} (Versuch {attempt}/{MAX_RETRIES}): {exc} -> warte {wait}s")
            time.sleep(wait)

    raise RuntimeError(f"Abbruch nach {MAX_RETRIES} Versuchen fuer {action}: {last_error}")


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS categories (
            slug    TEXT PRIMARY KEY,
            title   TEXT
        );

        CREATE TABLE IF NOT EXISTS datasets (
            id                  TEXT PRIMARY KEY,
            name                TEXT,
            title               TEXT,
            notes               TEXT,
            organization_name   TEXT,
            organization_title  TEXT,
            license_id          TEXT,
            license_title       TEXT,
            metadata_created    TEXT,
            metadata_modified   TEXT,
            num_resources       INTEGER,
            tags                TEXT,
            dataset_url         TEXT,
            fetched_at          TEXT,
            removed_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS dataset_categories (
            package_id      TEXT,
            category_slug   TEXT,
            PRIMARY KEY (package_id, category_slug),
            FOREIGN KEY (package_id) REFERENCES datasets(id),
            FOREIGN KEY (category_slug) REFERENCES categories(slug)
        );

        CREATE TABLE IF NOT EXISTS resources (
            resource_id     TEXT PRIMARY KEY,
            package_id      TEXT,
            name            TEXT,
            format          TEXT,
            url             TEXT,
            created         TEXT,
            last_modified   TEXT,
            FOREIGN KEY (package_id) REFERENCES datasets(id)
        );
        """
    )

    # Aeltere DBs um die Spalte removed_at ergaenzen
    columns = {row[1] for row in conn.execute("PRAGMA table_info(datasets)")}
    if "removed_at" not in columns:
        conn.execute("ALTER TABLE datasets ADD COLUMN removed_at TEXT")

    # Indizes fuer die haeufigsten Abfragen (nach Kategorie, Ressourcen je Datensatz)
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_dataset_categories_slug
            ON dataset_categories (category_slug, package_id);
        CREATE INDEX IF NOT EXISTS idx_resources_package
            ON resources (package_id);
        """
    )

    for slug, title in GROUPS:
        conn.execute(
            "INSERT OR REPLACE INTO categories (slug, title) VALUES (?, ?)",
            (slug, title),
        )
    conn.commit()
    return conn


def store_package(conn: sqlite3.Connection, pkg: dict) -> None:
    org = pkg.get("organization") or {}
    tags = ", ".join(t["name"] for t in pkg.get("tags", []))
    dataset_url = f"https://www.govdata.de/web/guest/suchen/-/details/{pkg['name']}"

    conn.execute(
        """
        INSERT OR REPLACE INTO datasets
        (id, name, title, notes, organization_name, organization_title,
         license_id, license_title, metadata_created, metadata_modified,
         num_resources, tags, dataset_url, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pkg["id"], pkg.get("name"), pkg.get("title"), pkg.get("notes"),
            org.get("name"), org.get("title"),
            pkg.get("license_id"), pkg.get("license_title"),
            pkg.get("metadata_created"), pkg.get("metadata_modified"),
            pkg.get("num_resources"), tags, dataset_url,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    # Kategorien und Ressourcen spiegeln den aktuellen Stand wider:
    # alte Zuordnungen entfernen, damit z. B. verschobene Datensaetze
    # nicht mehr in ihrer frueheren Kategorie gezaehlt werden.
    conn.execute("DELETE FROM dataset_categories WHERE package_id = ?", (pkg["id"],))
    conn.execute("DELETE FROM resources WHERE package_id = ?", (pkg["id"],))

    # Datensatz kann mehreren govdata-Kategorien angehoeren (n:m) -
    # hier wird JEDE tatsaechlich vom Datensatz getragene Gruppe erfasst,
    # nicht nur die Kategorie, ueber die er gerade gefunden wurde.
    for g in pkg.get("groups", []):
        conn.execute(
            "INSERT OR IGNORE INTO dataset_categories (package_id, category_slug) VALUES (?, ?)",
            (pkg["id"], g["name"]),
        )
        conn.execute(
            "INSERT OR IGNORE INTO categories (slug, title) VALUES (?, ?)",
            (g["name"], g.get("title", g["name"])),
        )

    for res in pkg.get("resources", []):
        conn.execute(
            """
            INSERT OR REPLACE INTO resources
            (resource_id, package_id, name, format, url, created, last_modified)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                res.get("id"), pkg["id"], res.get("name"), res.get("format"),
                res.get("url"), res.get("created"), res.get("last_modified"),
            ),
        )


def fetch_category(conn: sqlite3.Connection, slug: str, title: str) -> int:
    """Holt alle Datensaetze einer Kategorie und speichert sie. Gibt die Anzahl zurueck."""
    start = 0
    total = None
    processed = 0

    while total is None or start < total:
        result = api_call(
            "package_search",
            {"fq": f"groups:{slug}", "rows": ROWS_PER_PAGE, "start": start, "sort": "id asc"},
        )

        if total is None:
            total = result["count"]
            print(f"  Gesamtzahl in '{title}': {total}")

        for pkg in result["results"]:
            store_package(conn, pkg)
            processed += 1
        conn.commit()

        print(f"    {processed}/{total} verarbeitet (start={start})")

        start += ROWS_PER_PAGE
        time.sleep(REQUEST_DELAY_SECONDS)

    return processed


def normalize_ts(ts: str | None) -> str:
    """Zeitstempel auf Sekunden kuerzen - die ID-Liste liefert ein anderes
    Format (Millisekunden + 'Z') als der komplette Datensatz (Mikrosekunden)."""
    return (ts or "")[:19]


def fetch_live_index(slug: str) -> dict[str, str]:
    """Holt ID und Aenderungsdatum aller Live-Datensaetze einer Kategorie."""
    live = {}
    start = 0
    total = None

    while total is None or start < total:
        result = api_call(
            "package_search",
            {
                "fq": f"groups:{slug}",
                "rows": ID_ROWS_PER_PAGE,
                "start": start,
                "fl": "id,metadata_modified",
                "sort": "id asc",
            },
        )
        total = result["count"]
        for pkg in result["results"]:
            live[pkg["id"]] = normalize_ts(pkg["metadata_modified"])
        start += ID_ROWS_PER_PAGE
        time.sleep(REQUEST_DELAY_SECONDS)

    return live


def search_by_ids(ids: list[str]) -> list[dict]:
    """Holt die kompletten Datensaetze zu den IDs, die es live noch gibt."""
    packages = []
    for i in range(0, len(ids), ID_BATCH_SIZE):
        batch = ids[i:i + ID_BATCH_SIZE]
        fq = "id:(" + " OR ".join(f'"{pid}"' for pid in batch) + ")"
        packages.extend(api_call("package_search", {"fq": fq, "rows": len(batch)})["results"])
        time.sleep(REQUEST_DELAY_SECONDS)
    return packages


def sync_category(conn: sqlite3.Connection, slug: str, title: str) -> tuple[int, int]:
    """Gleicht eine Kategorie mit govdata.de ab.
    Gibt (neu/geaendert gespeichert, als entfernt markiert) zurueck."""
    live = fetch_live_index(slug)
    stored = dict(conn.execute(
        """
        SELECT d.id, d.metadata_modified
        FROM dataset_categories dc
        JOIN datasets d ON d.id = dc.package_id
        WHERE dc.category_slug = ? AND d.removed_at IS NULL
        """,
        (slug,),
    ))

    changed = [pid for pid, ts in live.items() if normalize_ts(stored.get(pid)) != ts]
    gone = [pid for pid in stored if pid not in live]
    print(f"  live: {len(live)}, in DB: {len(stored)}, neu/geaendert: {len(changed)}, "
          f"nicht mehr in Kategorie: {len(gone)}")

    if len(changed) > FULL_FETCH_SHARE * len(live):
        updated = fetch_category(conn, slug, title)
    else:
        packages = search_by_ids(changed)
        for pkg in packages:
            store_package(conn, pkg)
        conn.commit()
        updated = len(packages)

    # Nicht mehr in der Kategorie: entweder verschoben (existiert noch ->
    # neu speichern, dabei werden die Kategorien aktualisiert) oder geloescht.
    still_live = search_by_ids(gone)
    for pkg in still_live:
        store_package(conn, pkg)
    still_live_ids = {pkg["id"] for pkg in still_live}
    removed = [pid for pid in gone if pid not in still_live_ids]
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "UPDATE datasets SET removed_at = ? WHERE id = ?",
        [(now, pid) for pid in removed],
    )
    conn.commit()

    print(f"  -> {updated} gespeichert, {len(still_live)} verschoben, {len(removed)} als entfernt markiert")
    return updated + len(still_live), len(removed)


def main() -> None:
    conn = init_db(DB_PATH)

    print(f"Starte Abgleich fuer {len(GROUPS)} Kategorien...")
    total_updated = 0
    total_removed = 0

    for i, (slug, title) in enumerate(GROUPS, start=1):
        print(f"[{i}/{len(GROUPS)}] Kategorie '{slug}' - {title}")
        updated, removed = sync_category(conn, slug, title)
        total_updated += updated
        total_removed += removed

    active = conn.execute("SELECT COUNT(*) FROM datasets WHERE removed_at IS NULL").fetchone()[0]
    marked = conn.execute("SELECT COUNT(*) FROM datasets WHERE removed_at IS NOT NULL").fetchone()[0]
    conn.close()

    print(
        f"Fertig. {total_updated} Datensaetze neu/aktualisiert, {total_removed} neu als entfernt markiert. "
        f"'{DB_PATH}' enthaelt {active} aktive und {marked} entfernte Datensaetze."
    )


if __name__ == "__main__":
    main()
