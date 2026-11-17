# Gesetze-Scraper

Ziel: auswerten, welche Bundesgesetze und -verordnungen wie geändert wurden.

## Quelle

rechtsinformationen.bund.de (Rechtsinformationsportal des Bundes, Testphase)

- API ohne Schlüssel: <https://testphase.rechtsinformationen.bund.de/v1/legislation>
- Doku: <https://docs.rechtsinformationen.bund.de/>
- Limit: 600 Anfragen/Minute pro IP (der Scraper nutzt max. ~300)
- Stand 2026-09-28: ca. 5.500 Gesetze und Verordnungen des Bundes
- Texte als XML im Format LegalDocML.de (Paragraphen mit stabiler `eId`)

> **Wichtige Einschränkung:** Die API liefert nur die *aktuelle* Fassung jedes
> Gesetzes. Frühere Fassungen sind laut Doku „geplant“, aber noch nicht
> abrufbar. Die im XML enthaltenen Lebenszyklus-Ereignisse haben nur
> Platzhalter-Daten (1001-01-01). Brauchbar ist nur der Vermerk
> „zuletzt geändert durch Art. X G v. …“.
>
> Die Änderungshistorie beginnt deshalb mit dem ersten Lauf. Der Abgleich
> sollte regelmäßig laufen (z. B. täglich per cron).

> **Hinweis (November 2026):** Das Portal hat den XML-Namespace der
> Gesetzestexte geändert (`LegalDocML.de/1.8.2` → `rechtsinformationen.bund.de/schema/norm/0.1`).
> Der Parser liest den Namespace deshalb aus dem Dokument selbst, statt ihn
> fest vorzugeben.

## Ablauf (`gesetze.py`)

1. Liste aller Gesetze holen (~19 Seiten à 300 Einträge). Jeder Eintrag hat
   eine ELI mit Fassungsdatum, z. B.

   ```
   eli/bund/bgbl-1/2004/s1950/2026-07-29/1/deu
   \_______ Werk ________/ \_ Fassung _/
   ```

2. Fassungen, die noch nicht in der DB sind, als XML laden und in Paragraphen
   zerlegen. Erster Lauf: alle (~30–45 min), danach nur Neues.
3. Nicht mehr gelistete Gesetze bekommen ein Datum in `laws.removed_at` – aber
   nur, wenn die Liste plausibel vollständig war (mind. 90 % der bekannten
   Gesetze) und es kein Probelauf mit `--max-laws` ist.

## Datenbank (`gesetze.db`)

| Tabelle      | Inhalt |
|--------------|--------|
| `laws`       | ein Eintrag je Gesetz (Werk) |
| `versions`   | ein Eintrag je Fassung – mehrere Zeilen je Gesetz = Änderungen |
| `norms`      | Paragraphen/Artikel je Fassung (Nummer, Überschrift, Text-Hash) |
| `norm_texts` | Paragraphentexte, dedupliziert über den SHA-256-Hash – unveränderte Paragraphen belegen bei neuen Fassungen keinen weiteren Platz |

## Auswertung (`gesetze_aenderungen.py`)

```bash
python -m open_data_monitor gesetze-aenderungen              # Übersicht: welches Gesetz, wann, wie viele
                                                             # Paragraphen geändert/neu/weggefallen
python -m open_data_monitor gesetze-aenderungen AufenthG     # wortgenaue Änderungen: [-alt-] {+neu+}
python -m open_data_monitor gesetze-aenderungen --csv x.csv  # jede Paragraph-Änderung als CSV-Zeile
```

Nicht erfasst: Anlagen/Tabellen außerhalb von Paragraphen, Landesrecht,
Gesetzgebungsverfahren (dafür: DIP-API des Bundestags, siehe unten).

## Ausbau: DIP-API des Bundestags

Liefert Gesetzgebungsvorgänge (Entwurf, Drucksachen, Beschlüsse, Verkündung)
auch historisch – also *warum* und *durch welches Verfahren* ein Gesetz
geändert wurde. Verknüpfbar über die BGBl-Fundstelle (Filter
`f.verkuendung_fundstelle`).

- <https://search.dip.bundestag.de/api/v1>, Doku: `.../api/v1/openapi.yaml`
- braucht einen API-Key: öffentlicher Key auf
  <https://dip.bundestag.de/über-dip/hilfe/api> (wechselt regelmäßig) oder
  eigener Key auf Anfrage bei der Parlamentsdokumentation des Bundestags
- max. 25 gleichzeitige Anfragen
