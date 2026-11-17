# EU-Parlament-Abstimmungsscraper

## 1. Quelle

Statt einzelne Seiten von europarl.europa.eu abzuklappern, wird ein fertiger
Bulk-Dump von [Parltrack](https://parltrack.org) genutzt:

```
https://parltrack.org/dumps/ep_votes.json.zst
```

Parltrack ist eine seit Jahren gepflegte offene Datenbank (ODbL-Lizenz), die
namentliche Plenarabstimmungen direkt aus den offiziellen
„proces-verbal“-XML-Dokumenten des EU-Parlaments aufbereitet. Jeder Datensatz
enthält unter `url` den Link auf das Original-Dokument bei europarl.europa.eu –
die Herkunft ist damit nachvollziehbar.

**Warum kein direkter Scraper gegen data.europarl.europa.eu?**
Die offizielle API liefert Abstimmungsergebnisse erst mit mehreren Wochen
Verzögerung, und es war beim Testen nicht zuverlässig zu ermitteln, ob/wie
Fraktions-Aufschlüsselungen dort direkt abrufbar sind. Der Parltrack-Dump
liefert dieselben Rohdaten (Original-Quelle EU-Parlament) in einer bereits
sauber strukturierten, kompakten Form.

> **Stand November 2026:** Der Dump wurde zuletzt am 28.03.2026 aktualisiert
> (neueste Abstimmung vom 26.03.2026). Ob Parltrack ihn weiter pflegt, ist
> offen – siehe „Grenzen“ in der README.

## 2. Ablauf (`eu_parlament.py`)

1. Download von `ep_votes.json.zst` (~11 MB, alle Abstimmungen seit 2004)
2. Entpacken mit dem Kommandozeilenwerkzeug `zstd`
3. Filtern auf Abstimmungen ab `MIN_DATE` (Standard: 2015-01-01, per `--ab` änderbar)
4. Speichern in SQLite (`eu_abstimmungen.db`)
5. Die großen Zwischendateien (`.zst` und entpacktes JSON, zusammen ca. 660 MB)
   liegen in einem temporären Verzeichnis, das danach automatisch gelöscht
   wird – nur die gefilterte SQLite-Datenbank bleibt.

## 3. Datenbank (2 Tabellen)

**`votes`** – `voteid` (Primärschlüssel), `ts` (Zeitstempel), `title`, `doc`
(Berichts-Nr., z. B. `A8-0347/2015`), `epref` (Dossier-Referenz, z. B.
`2015/0903(NLE)`), `source_url` (Original-EU-Dokument), `total_dafuer`,
`total_dagegen`, `total_enthaltung`

**`vote_fraktionen`** – `voteid`, `choice` (`dafuer`/`dagegen`/`enthaltung`),
`fraktion` (z. B. PPE, S&D, Renew, Verts/ALE, ECR, The Left, PfE, ESN, NI),
`anzahl` (wie viele Mitglieder dieser Fraktion so gestimmt haben)

Es werden **keine einzelnen Abgeordneten** gespeichert, nur die Anzahl je
Fraktion und Abstimmung (das war die gewünschte Detailtiefe).

## 4. Beispielabfragen

Wie hat eine Fraktion insgesamt seit 2015 abgestimmt:

```sql
SELECT choice, SUM(anzahl) FROM vote_fraktionen
WHERE fraktion = 'Verts/ALE'
GROUP BY choice;
```

Alle Abstimmungen zu einem bestimmten Dossier:

```sql
SELECT ts, title, total_dafuer, total_dagegen
FROM votes WHERE epref LIKE '%2015/0903%';
```

Fraktionsergebnis einer einzelnen Abstimmung:

```sql
SELECT choice, fraktion, anzahl FROM vote_fraktionen
WHERE voteid = '189210' ORDER BY choice, anzahl DESC;
```

## 5. Mengengerüst

| Kennzahl                                   | Lauf 05.08.2026 | Lauf 17.11.2026 |
|--------------------------------------------|----------------:|----------------:|
| Abstimmungen gesamt bei Parltrack (ab 2004)|          44.648 |          44.648 |
| davon ab 2015-01-01 übernommen             |          34.130 |          34.130 |
| Zeilen in `vote_fraktionen`                |         622.711 |         622.711 |
| Größe `eu_abstimmungen.db`                 |          ~47 MB |          ~47 MB |
| Laufzeit (Download + Verarbeitung)         |          ~33 s  |       ~30–55 s  |

## 6. Ausführen

```bash
sudo apt install zstd          # falls nicht vorhanden
python -m open_data_monitor eu
```

Ergebnis: `data/eu_abstimmungen.db`.
