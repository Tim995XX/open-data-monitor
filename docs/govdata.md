# govdata.de-Scraper „mehrere Kategorien“

Dieses Dokument erklärt, wie Datensätze aus sechs Themen-Kategorien von
govdata.de abgefragt und sauber nach Kategorie sortierbar in SQLite abgelegt
werden – und wie spätere Läufe nur noch Änderungen nachladen.

## 1. Warum keine frei erfundenen Kategorien?

Der ursprüngliche Wunsch war eine Sortierung nach z. B. „Gesetzesänderungen“,
„Informationen“, „Belehrungen“. govdata.de kennt aber nur 13 feste
Themen-Kategorien (Slugs), die per API-Abfrage

```
GET https://www.govdata.de/ckan/api/3/action/group_list?all_fields=true
```

ermittelt wurden. Das ist ein EU-weit einheitliches Vokabular (DCAT-AP „Data
Theme“), das auch andere europäische Open-Data-Portale verwenden. Es sortiert
nach **Sachthemen**, nicht nach Dokumenttyp – „Belehrungen“ oder
„Informationsblätter“ existieren dort nicht als Kategorie.

Ausgewählt wurden 6 der 13 Kategorien:

| Slug | Kategorie |
|------|-----------|
| soci | Bevölkerung und Gesellschaft |
| educ | Bildung, Kultur und Sport |
| heal | Gesundheit |
| just | Justiz, Rechtssystem und öffentliche Sicherheit |
| econ | Wirtschaft und Finanzen |
| tech | Wissenschaft und Technologie |

`just` deckt inhaltlich am ehesten die ursprünglich gewünschte Idee von
„Gesetzesänderungen“ ab (Rechtssystem, Gesetzestexte, Sicherheitsstatistiken).

## 2. Wie werden mehrere Kategorien abgefragt?

Die Liste `GROUPS` in `govdata.py` wird nacheinander abgearbeitet; je Kategorie
ruft `sync_category()` die CKAN-Aktion `package_search` auf:

```
GET .../package_search?fq=groups:<slug>&rows=100&start=<n>
```

Es werden ausschließlich JSON-Antworten der offiziellen CKAN-API verarbeitet,
kein HTML-Parsing.

## 3. Das Problem: Ein Datensatz kann mehreren Kategorien angehören

govdata-Datensätze sind nicht 1:1 einer Kategorie zugeordnet – ein Datensatz zu
„Schulen und Schüler“ kann z. B. gleichzeitig unter `educ` **und** `tech`
einsortiert sein.

Würde man eine Tabelle pro Kategorie anlegen oder Zeilen pro Kategorie
duplizieren, hätte man denselben Datensatz mehrfach unabhängig in der
Datenbank – Änderungen müssten an mehreren Stellen gepflegt werden, und die
Gesamtzahl eindeutiger Datensätze wäre nicht mehr feststellbar.

**Lösung:** eine n:m-Beziehung (viele Datensätze ↔ viele Kategorien) über eine
Verknüpfungstabelle – das Standardmuster für solche Fälle in relationalen
Datenbanken.

## 4. Datenbank-Struktur (4 Tabellen)

**`categories`** – `slug` (Primärschlüssel), `title`.
Nachschlagetabelle für alle vorkommenden Kategorie-Slugs. Wird mit den 6
gewählten Kategorien befüllt und während des Laufs um Zusatz-Kategorien
ergänzt, die ein Datensatz zusätzlich trägt (z. B. `envi`, obwohl nicht
explizit gewählt).

**`datasets`** – `id` (Primärschlüssel), `title`, `notes`, `organization_*`,
`license_*`, `metadata_created/modified`, `num_resources`, `tags`,
`dataset_url`, `fetched_at`, `removed_at`.
Jeder Datensatz kommt hier **genau einmal** vor, unabhängig davon, wie vielen
Kategorien er angehört. `removed_at` ist leer, solange der Datensatz live auf
govdata.de ist, und enthält ein Datum, sobald er dort gelöscht wurde – er bleibt
zur Nachvollziehbarkeit in der DB. Für aktuelle Zahlen daher
`WHERE removed_at IS NULL` ergänzen.

**`dataset_categories`** – `package_id`, `category_slug` (zusammengesetzter
Primärschlüssel). Das Herzstück der Sortierung: Für jede tatsächliche
Kategorie-Zugehörigkeit eines Datensatzes (laut `groups` im API-Ergebnis,
**nicht** nur die Kategorie, über die er gerade gefunden wurde) entsteht eine
Zeile. Ein Datensatz in 2 Kategorien erzeugt 2 Zeilen.

**`resources`** – Download-Links der einzelnen Dateien je Datensatz.

## 5. Änderungserkennung (seit 27.09.2026)

Pro Kategorie wird zuerst nur die Liste aus ID + Änderungsdatum geholt (1000
je Anfrage) und mit der DB verglichen (`diff_index()`):

- **neu/geändert** – ID unbekannt oder `metadata_modified` weicht ab. Die
  Zeitstempel werden vorher auf Sekunden gekürzt, weil die ID-Liste ein anderes
  Format liefert als der vollständige Datensatz.
- Sind mehr als 30 % geändert, wird die ganze Kategorie seitenweise neu
  geladen, sonst nur die betroffenen IDs (je 50 pro Anfrage).
- **nicht mehr in der Kategorie** – entweder in eine andere Kategorie
  verschoben (existiert noch → neu speichern, Zuordnungen werden ersetzt) oder
  gelöscht (→ `removed_at` setzen).

## 6. Beispielabfragen

Alle Datensätze einer bestimmten Kategorie:

```sql
SELECT d.title, d.organization_title
FROM datasets d
JOIN dataset_categories dc ON dc.package_id = d.id
WHERE dc.category_slug = 'just' AND d.removed_at IS NULL;
```

Anzahl Datensätze je Kategorie:

```sql
SELECT c.title, COUNT(*) AS anzahl
FROM dataset_categories dc
JOIN categories c ON c.slug = dc.category_slug
GROUP BY c.title
ORDER BY anzahl DESC;
```

Datensätze, die in mehreren Kategorien vorkommen:

```sql
SELECT package_id, COUNT(*) AS anzahl_kategorien
FROM dataset_categories
GROUP BY package_id
HAVING anzahl_kategorien > 1;
```

## 7. Auswertung im Zeitverlauf

```bash
python -m open_data_monitor govdata-auswertung
```

zählt je Jahr (`metadata_created`) und Kategorie, wie viele heute noch aktive
Datensätze veröffentlicht wurden, zeigt absolute Zahlen und Anteile und
schreibt `data/auswertung_zeitverlauf.csv`.

**Wichtig:** Das zeigt Veröffentlichungsaktivität der Behörden auf dem
Datenportal – keine politischen Entscheidungen oder Gesetzesbeschlüsse.

## 8. Laufzeit und Mengengerüst

Anzahl Treffer je Kategorie (Stand Testabfrage, September 2026):

| Slug  | Treffer |
|-------|--------:|
| soci  | 23.196 |
| educ  | 15.380 |
| heal  |  3.967 |
| just  |  3.548 |
| econ  | 24.713 |
| tech  | 22.654 |
| **Summe** | **93.458** (Kategorie-Treffer, nicht eindeutige Datensätze) |

Bei 93.458 Treffern / 100 pro Seite ≈ 935 Anfragen, mit 0,7 Sekunden Pause je
Anfrage allein ca. 11 Minuten Wartezeit; realistisch 15–25 Minuten für den
ersten vollen Lauf. Folgeläufe brauchen nur die ID-Listen (~100 Anfragen) plus
die geänderten Datensätze.

## 9. Ausführen

```bash
python -m open_data_monitor govdata                   # alle 6 Kategorien
python -m open_data_monitor govdata --kategorie heal  # Probelauf mit einer Kategorie
```

Ergebnis: `data/govdata_kategorien.db` mit den vier oben beschriebenen Tabellen.
