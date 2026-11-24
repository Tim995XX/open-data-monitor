# open-data-monitor

Beobachtet drei offene Datenquellen der öffentlichen Hand, speichert ihren Stand
in SQLite und macht Änderungen im Zeitverlauf auswertbar:

| Quelle | Was wird erfasst | Modul |
|--------|------------------|-------|
| [govdata.de](https://www.govdata.de) (CKAN-API) | Metadaten der Datensätze aus 6 Themen-Kategorien (Gesundheit, Justiz, Wirtschaft …) inkl. Download-Links | `govdata.py` |
| [rechtsinformationen.bund.de](https://docs.rechtsinformationen.bund.de/) | Bundesgesetze und -verordnungen, Paragraph für Paragraph je Fassung | `gesetze.py` |
| [Parltrack](https://parltrack.org) (Daten des EU-Parlaments) | Namentliche Plenarabstimmungen seit 2015, aufgeschlüsselt nach Fraktion | `eu_parlament.py` |

Entstanden ist das Projekt aus drei einzelnen Skripten, die hier zu einem
Paket mit gemeinsamer Kommandozeile, gemeinsamen Bausteinen und Tests
zusammengeführt wurden. Die Git-Historie zeigt diesen Weg Schritt für Schritt.

## Gemeinsames Muster

Alle drei Scraper folgen demselben Ablauf:

```
abrufen  →  normalisieren  →  in SQLite speichern  →  Änderungen im Zeitverlauf auswerten
```

| Schritt | govdata | gesetze | eu_parlament |
|---------|---------|---------|--------------|
| **abrufen** | CKAN `package_search`, erst nur IDs + Änderungsdatum, dann gezielt die geänderten Datensätze | Gesetzesliste (JSON), dann je neuer Fassung das XML | ein komprimierter Bulk-Dump (~11 MB) statt tausender Einzelseiten |
| **normalisieren** | `dataset_row()`: Organisation, Lizenz, Tags, Link; n:m-Zuordnung zu Kategorien | `parse_list_item()`, `parse_norms()`: XML → Paragraphen mit stabiler `eId`, Nummer, Überschrift, Text | `vote_row()`, `fraktion_rows()`: Summen je Wahl, Anzahl Stimmen je Fraktion |
| **speichern** | 4 Tabellen, gelöschte Datensätze bleiben mit `removed_at` erhalten | jede Fassung als neue Zeile; Paragraphentexte per SHA-256 dedupliziert | 2 Tabellen, Wiederholung ohne Duplikate (`INSERT OR REPLACE`) |
| **auswerten** | `govdata-auswertung`: Veröffentlichungen je Jahr und Kategorie | `gesetze-aenderungen`: was hat sich zwischen zwei Fassungen geändert, wortgenau | per SQL (Beispiele in [docs/eu_parlament.md](docs/eu_parlament.md)); Historie über `ts` |

## Architektur

```
src/open_data_monitor/
├── cli.py                  Kommandozeile (argparse), ein Befehl je Scraper/Auswertung
├── http_client.py          gemeinsam: Session mit User-Agent, Wiederholung mit Backoff
├── db.py                   gemeinsam: SQLite öffnen + Schema anlegen, UTC-Zeitstempel
├── govdata.py              Scraper govdata.de
├── govdata_auswertung.py   Auswertung: Datensätze je Jahr und Kategorie
├── gesetze.py              Scraper Bundesgesetze
├── gesetze_aenderungen.py  Auswertung: Änderungen zwischen Fassungen
└── eu_parlament.py         Scraper EU-Parlament (Parltrack-Dump)
tests/                      pytest, ohne Netzwerkzugriff
docs/                       ausführliche Erklärungen je Quelle
```

Bewusst **gemeinsam** ist nur, was in den Originalskripten tatsächlich doppelt
vorkam: die Wiederholungsschleife für HTTP-Anfragen (2, 4, 8 … Sekunden
Wartezeit), das Öffnen der Datenbank und der Zeitstempel. Schemata und
Änderungserkennung sind je Quelle fachlich verschieden und bleiben deshalb in
ihrem Modul – eine gemeinsame Basisklasse hätte hier mehr verdeckt als
vereinfacht.

Weitere Entscheidungen:

- **Offizielle APIs statt HTML-Parsing.** Alle Quellen liefern JSON oder XML.
- **Höflich abfragen:** feste Pausen zwischen Anfragen (govdata 0,7 s,
  Gesetze 0,2 s bei einem erlaubten Limit von 600/min), eigener User-Agent,
  inkrementelle Abgleiche statt Komplett-Downloads.
- **Nichts wird stillschweigend gelöscht:** Verschwindet ein Datensatz oder
  Gesetz bei der Quelle, wird nur `removed_at` gesetzt. Beim Gesetze-Scraper
  passiert das nur, wenn die Liste plausibel vollständig ist – ein API-Aussetzer
  markiert also nicht tausende Gesetze als entfernt.
- **Reine Funktionen für die Normalisierung**, damit sie ohne Netzwerk testbar
  sind; die Netzwerkschicht wird in den Tests durch Fälschungen ersetzt.
- **`logging` für Laufprotokolle, `print` für Auswertungstabellen** – Letztere
  sind das eigentliche Ergebnis des Befehls.

Details je Quelle: [docs/govdata.md](docs/govdata.md),
[docs/gesetze.md](docs/gesetze.md), [docs/eu_parlament.md](docs/eu_parlament.md).

## Installation

Voraussetzungen: Python ≥ 3.10, für den EU-Scraper zusätzlich das
Kommandozeilenwerkzeug `zstd` (`sudo apt install zstd`).

```bash
git clone <repo-url> open-data-monitor
cd open-data-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"      # oder: pip install -r requirements-dev.txt
mkdir -p data                # Ziel für Datenbanken und Cron-Logs
```

## Nutzung

Alle Datenbanken landen standardmäßig in `./data/` (anpassbar mit
`--data-dir` oder je Befehl mit `--db`).

```bash
# Scraper
python -m open_data_monitor govdata                   # alle 6 Kategorien abgleichen
python -m open_data_monitor govdata --kategorie heal  # Probelauf mit einer Kategorie
python -m open_data_monitor gesetze                   # alle Bundesgesetze abgleichen
python -m open_data_monitor gesetze --max-laws 20     # Probelauf mit 20 Gesetzen
python -m open_data_monitor eu                        # EU-Abstimmungen ab 2015
python -m open_data_monitor eu --ab 2024-07-16        # ab einem anderen Datum
python -m open_data_monitor alle                      # alle drei nacheinander

# Auswertungen
python -m open_data_monitor govdata-auswertung              # Datensätze je Jahr und Kategorie (+ CSV)
python -m open_data_monitor gesetze-aenderungen             # welche Gesetze wurden wie stark geändert
python -m open_data_monitor gesetze-aenderungen AufenthG    # wortgenaue Änderungen: [-alt-] {+neu+}
python -m open_data_monitor gesetze-aenderungen --csv aenderungen.csv
```

Nach `pip install -e .` steht derselbe Befehl auch als `open-data-monitor …`
zur Verfügung.

Optional kann der User-Agent (z. B. mit einer Kontaktadresse für die
Betreiber der Quellen) über die Umgebungsvariable
`OPEN_DATA_MONITOR_USER_AGENT` gesetzt werden.

### Tests

```bash
pytest
```

Die Tests greifen nicht auf das Netzwerk zu: HTTP-Antworten sind feste
Beispieldaten, Datenbanken entstehen in temporären Verzeichnissen.

## Regelmäßig ausführen (cron)

Änderungen werden nur sichtbar, wenn regelmäßig abgeglichen wird – vor allem
beim Gesetze-Scraper, weil die Quelle keine alten Fassungen liefert.

```cron
# täglich 06:30 Gesetze, sonntags 03:00 govdata, monatlich EU
30 6 * * *  cd /pfad/zu/open-data-monitor && flock -n data/gesetze.lock .venv/bin/python -m open_data_monitor gesetze >> data/gesetze.log 2>&1
0 3 * * 0   cd /pfad/zu/open-data-monitor && flock -n data/govdata.lock .venv/bin/python -m open_data_monitor govdata >> data/govdata.log 2>&1
0 4 1 * *   cd /pfad/zu/open-data-monitor && .venv/bin/python -m open_data_monitor eu >> data/eu.log 2>&1
```

`flock -n` verhindert, dass sich zwei Läufe überschneiden.

## Grenzen

- **Gesetze:** Die API (Testphase) liefert nur die aktuelle Fassung; die
  Historie beginnt mit dem ersten eigenen Lauf. Anlagen/Tabellen außerhalb von
  Paragraphen und Landesrecht werden nicht erfasst. Die API ist noch nicht
  stabil – im Herbst 2026 hat sich z. B. der XML-Namespace geändert.
- **EU-Parlament:** abhängig von Parltrack als Drittquelle. Der Dump wurde
  zuletzt im März 2026 aktualisiert; neuere Abstimmungen fehlen, solange er
  nicht fortgeschrieben wird. Das entpackte JSON (~650 MB) wird komplett in den
  Speicher geladen; der Abgleich ersetzt den Bestand, statt Änderungen
  einzeln zu protokollieren. Benötigt das externe Werkzeug `zstd`.
- **govdata:** erfasst Metadaten und Links, nicht die Inhalte der Dateien. Die
  Jahresauswertung zeigt Veröffentlichungsaktivität von Behörden, keine
  politischen Entscheidungen. Ein erster Volllauf dauert 15–25 Minuten.
- Keine Parallelisierung – bewusst, um die Quellen nicht zu belasten.

## Hinweis

Dieses Projekt wurde mithilfe von Claude (Anthropic) erstellt.

## Lizenz

[MIT](LICENSE). Die abgerufenen Daten unterliegen den Lizenzen der jeweiligen
Quellen (u. a. Datenlizenz Deutschland, ODbL bei Parltrack).
