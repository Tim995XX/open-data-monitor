"""
Auswertung: Wieviele Datensaetze wurden je Kategorie in welchem Jahr
auf govdata.de veroeffentlicht (metadata_created)?

WICHTIG: Das zeigt Veroeffentlichungs-/Aktualisierungsaktivitaet der
Behoerden auf dem Datenportal - keine politischen Entscheidungen oder
Gesetzesbeschluesse. govdata.de enthaelt keine Beschlussdaten.

Die Tabellen gehen bewusst per print() auf die Standardausgabe: Sie sind
das Ergebnis des Befehls, kein Protokoll (dafuer ist logging da).
"""

import csv
import re
import sqlite3
from collections import defaultdict
from pathlib import Path


def year_of(date_str: str | None) -> str | None:
    if not date_str:
        return None
    match = re.match(r"(\d{4})-\d{2}-\d{2}", date_str)
    return match.group(1) if match else None


def count_by_year(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Jahr -> Kategorie-Slug -> Anzahl aktiver Datensaetze (nach metadata_created)."""
    rows = conn.execute(
        """
        SELECT dc.category_slug, d.metadata_created
        FROM dataset_categories dc
        JOIN datasets d ON d.id = dc.package_id
        WHERE d.removed_at IS NULL  -- auf govdata.de geloeschte Datensaetze nicht mitzaehlen
        """
    ).fetchall()

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for slug, created in rows:
        year = year_of(created)
        if year:
            counts[year][slug] += 1
    return counts


def run(db_path: str | Path, out_csv: str | Path) -> None:
    conn = sqlite3.connect(db_path)

    categories = [row[0] for row in conn.execute("SELECT slug FROM categories ORDER BY slug")]
    titles = dict(conn.execute("SELECT slug, title FROM categories"))
    counts = count_by_year(conn)
    conn.close()

    years = sorted(counts.keys())

    # Konsolen-Tabelle: absolute Zahlen je Jahr und Kategorie
    print("Absolute Anzahl veroeffentlichter Datensaetze je Jahr und Kategorie:\n")
    header = ["Jahr"] + categories + ["Summe"]
    print("\t".join(header))
    for year in years:
        row_counts = [counts[year].get(c, 0) for c in categories]
        print("\t".join([year] + [str(v) for v in row_counts] + [str(sum(row_counts))]))

    # Prozentualer Anteil je Jahr (welche Kategorie dominierte)
    print("\nProzentualer Anteil je Kategorie am jeweiligen Jahr:\n")
    print("\t".join(header))
    for year in years:
        row_counts = [counts[year].get(c, 0) for c in categories]
        total = sum(row_counts) or 1
        shares = [f"{v / total * 100:.1f}%" for v in row_counts]
        print("\t".join([year] + shares + [str(total)]))

    # CSV-Export fuer Weiterverarbeitung / Visualisierung
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["jahr"] + categories + ["summe"])
        for year in years:
            row_counts = [counts[year].get(c, 0) for c in categories]
            writer.writerow([year] + row_counts + [sum(row_counts)])

    print(f"\nCSV geschrieben: {out_csv}")
    print("Kategorie-Titel zur Referenz:")
    for slug in categories:
        print(f"  {slug}: {titles[slug]}")
