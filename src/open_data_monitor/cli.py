"""
Kommandozeile: ein Einstiegspunkt fuer alle Scraper und Auswertungen.

    python -m open_data_monitor govdata                  # govdata.de abgleichen
    python -m open_data_monitor govdata --kategorie heal # nur eine Kategorie (Probelauf)
    python -m open_data_monitor govdata-auswertung       # Datensaetze je Jahr und Kategorie
    python -m open_data_monitor gesetze                  # Bundesgesetze abgleichen
    python -m open_data_monitor gesetze --max-laws 20    # Probelauf mit wenigen Gesetzen
    python -m open_data_monitor gesetze-aenderungen [ABK] [--csv DATEI]
    python -m open_data_monitor eu                       # EU-Parlament-Abstimmungen
    python -m open_data_monitor alle                     # alle drei Scraper nacheinander

Alle Datenbanken liegen standardmaessig in ./data (relativ zum Arbeits-
verzeichnis, also dem Projektordner); aenderbar mit --data-dir oder je
Befehl mit --db.
"""

import argparse
import logging
import sys
import tempfile
from pathlib import Path

from . import eu_parlament, gesetze, gesetze_aenderungen, govdata, govdata_auswertung
from .http_client import make_session

log = logging.getLogger("open_data_monitor")

DEFAULT_DATA_DIR = Path("data")


def _db(args: argparse.Namespace, filename: str) -> Path:
    return Path(args.db) if args.db else Path(args.data_dir) / filename


def cmd_govdata(args: argparse.Namespace) -> None:
    govdata.run(_db(args, govdata.DB_FILENAME), make_session(), slugs=args.kategorie)


def cmd_govdata_auswertung(args: argparse.Namespace) -> None:
    out_csv = args.csv or Path(args.data_dir) / "auswertung_zeitverlauf.csv"
    govdata_auswertung.run(_db(args, govdata.DB_FILENAME), out_csv)


def cmd_gesetze(args: argparse.Namespace) -> None:
    gesetze.run(_db(args, gesetze.DB_FILENAME), make_session(), max_laws=args.max_laws)


def cmd_gesetze_aenderungen(args: argparse.Namespace) -> None:
    gesetze_aenderungen.run(_db(args, gesetze.DB_FILENAME), args.abkuerzung, args.csv)


def run_eu(db_path: Path, min_date: str = eu_parlament.MIN_DATE) -> None:
    # Zwischendateien (mehrere hundert MB) landen in einem temporaeren
    # Verzeichnis, das nach dem Lauf automatisch geloescht wird.
    with tempfile.TemporaryDirectory(prefix="open-data-monitor-eu-") as tmp:
        eu_parlament.run(db_path, make_session(), Path(tmp), min_date=min_date)


def cmd_eu(args: argparse.Namespace) -> None:
    run_eu(_db(args, eu_parlament.DB_FILENAME), args.ab)


def cmd_alle(args: argparse.Namespace) -> int:
    """Alle Scraper nacheinander. Faellt einer aus, laufen die anderen trotzdem."""
    data_dir = Path(args.data_dir)
    jobs = [
        ("govdata", lambda: govdata.run(data_dir / govdata.DB_FILENAME, make_session())),
        ("gesetze", lambda: gesetze.run(data_dir / gesetze.DB_FILENAME, make_session())),
        ("eu", lambda: run_eu(data_dir / eu_parlament.DB_FILENAME)),
    ]
    failed = []
    for name, job in jobs:
        log.info("===== %s =====", name)
        try:
            job()
        except Exception:  # bewusst breit: ein Ausfall soll die anderen Quellen nicht stoppen
            log.exception("Scraper '%s' abgebrochen", name)
            failed.append(name)
    if failed:
        log.error("Fehlgeschlagen: %s", ", ".join(failed))
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open_data_monitor",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                        help="Verzeichnis fuer die SQLite-DBs (Standard: ./data)")
    parser.add_argument("-v", "--verbose", action="store_true", help="ausfuehrlichere Ausgabe")
    sub = parser.add_subparsers(dest="befehl", required=True)

    def add(name: str, func, help_text: str, with_db: bool = True) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        if with_db:
            p.add_argument("--db", help="Pfad zur SQLite-DB (ueberschreibt --data-dir)")
        p.set_defaults(func=func)
        return p

    p = add("govdata", cmd_govdata, "govdata.de-Kategorien abgleichen")
    p.add_argument("--kategorie", action="append", choices=[s for s, _ in govdata.GROUPS],
                   help="nur diese Kategorie(n) abgleichen, mehrfach angebbar")

    p = add("govdata-auswertung", cmd_govdata_auswertung,
            "veroeffentlichte Datensaetze je Jahr und Kategorie")
    p.add_argument("--csv", help="Ziel-CSV (Standard: DATA_DIR/auswertung_zeitverlauf.csv)")

    p = add("gesetze", cmd_gesetze, "Bundesgesetze von rechtsinformationen.bund.de abgleichen")
    p.add_argument("--max-laws", type=int, help="nur die ersten N Gesetze abgleichen (Probelauf)")

    p = add("gesetze-aenderungen", cmd_gesetze_aenderungen,
            "Aenderungen zwischen gespeicherten Gesetzesfassungen anzeigen")
    p.add_argument("abkuerzung", nargs="?", help="Gesetzesabkuerzung, z. B. AufenthG")
    p.add_argument("--csv", help="Uebersicht zusaetzlich als CSV speichern")

    p = add("eu", cmd_eu, "Abstimmungen des EU-Parlaments (Parltrack-Dump) laden")
    p.add_argument("--ab", default=eu_parlament.MIN_DATE,
                   help=f"nur Abstimmungen ab diesem Datum (Standard: {eu_parlament.MIN_DATE})")

    add("alle", cmd_alle, "alle drei Scraper nacheinander ausfuehren", with_db=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # urllib3 meldet sonst jede einzelne Verbindung
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
