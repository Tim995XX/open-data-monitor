"""
Auswertung: welche Gesetze wurden wie geaendert?

Vergleicht aufeinanderfolgende Fassungen aus gesetze.db (befuellt von
gesetze_scraper.py) Paragraph fuer Paragraph.

Aufruf:
    python gesetze_aenderungen.py                # Uebersicht aller geaenderten Gesetze
    python gesetze_aenderungen.py AufenthG       # Wortgenaue Aenderungen eines Gesetzes
    python gesetze_aenderungen.py --csv out.csv  # Uebersicht als CSV (eine Zeile je Paragraph-Aenderung)
"""

import argparse
import csv
import difflib
import sqlite3

DB_PATH = "gesetze.db"


def load_norms(conn: sqlite3.Connection, expression_eli: str) -> dict[str, tuple]:
    """eid -> (position, num, heading, text_hash, text)"""
    rows = conn.execute(
        """
        SELECT n.eid, n.position, n.num, n.heading, n.text_hash, t.text
        FROM norms n JOIN norm_texts t ON t.hash = n.text_hash
        WHERE n.expression_eli = ?
        """,
        (expression_eli,),
    )
    return {eid: rest for eid, *rest in rows}


def compare(old: dict, new: dict) -> list[tuple[str, str, tuple | None, tuple | None]]:
    """Liefert (art, eid, alt, neu) mit art in neu / weggefallen / geaendert, in Gesetzesreihenfolge."""
    changes = []
    for eid in old.keys() | new.keys():
        o, n = old.get(eid), new.get(eid)
        if o is None:
            changes.append(("neu", eid, o, n))
        elif n is None:
            changes.append(("weggefallen", eid, o, n))
        elif o[3] != n[3] or o[2] != n[2]:
            changes.append(("geaendert", eid, o, n))
    return sorted(changes, key=lambda c: (c[3] or c[2])[0])


def version_pairs(conn: sqlite3.Connection, abbreviation: str | None = None):
    """Alle Paare (Gesetz, alte Fassung, neue Fassung) aufeinanderfolgender Fassungen."""
    sql = """
        SELECT l.work_eli, l.abbreviation, l.name, v.expression_eli, v.version_date, v.last_amended_by
        FROM versions v JOIN laws l USING (work_eli)
    """
    params = ()
    if abbreviation:
        sql += " WHERE l.abbreviation = ? COLLATE NOCASE"
        params = (abbreviation,)
    sql += " ORDER BY l.work_eli, v.version_date, v.fetched_at"

    previous = None
    for row in conn.execute(sql, params).fetchall():
        if previous and previous[0] == row[0]:
            yield previous, row
        previous = row


def word_diff(old: str, new: str) -> str:
    """Markiert Aenderungen wortweise: [-geloescht-] {+eingefuegt+}"""
    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            words = a[i1:i2]
            # Lange unveraenderte Passagen kuerzen
            out.append(" ".join(words) if len(words) <= 12 else
                       " ".join(words[:5]) + " ... " + " ".join(words[-5:]))
        if op in ("delete", "replace"):
            out.append("[-" + " ".join(a[i1:i2]) + "-]")
        if op in ("insert", "replace"):
            out.append("{+" + " ".join(b[j1:j2]) + "+}")
    return " ".join(out)


def label(norm: tuple) -> str:
    return " ".join(p for p in (norm[1], norm[2]) if p)


def show_law(conn: sqlite3.Connection, abbreviation: str) -> None:
    versions = conn.execute(
        """
        SELECT v.version_date, v.last_amended_by FROM versions v JOIN laws l USING (work_eli)
        WHERE l.abbreviation = ? COLLATE NOCASE ORDER BY v.version_date
        """,
        (abbreviation,),
    ).fetchall()
    if not versions:
        print(f"'{abbreviation}' nicht in der DB.")
        return
    print(f"{abbreviation}: {len(versions)} Fassung(en) erfasst: " + ", ".join(v[0] for v in versions))

    pairs = list(version_pairs(conn, abbreviation))
    if not pairs:
        print("Noch keine Aenderung erfasst - dafuer braucht es mindestens zwei Laeufe mit verschiedenen Fassungen.")
        return

    for old_v, new_v in pairs:
        print(f"\n=== Fassung {old_v[4]} -> {new_v[4]}")
        if new_v[5]:
            print(f"    ({new_v[5]})")
        for kind, eid, o, n in compare(load_norms(conn, old_v[3]), load_norms(conn, new_v[3])):
            if kind == "neu":
                print(f"\n+ NEU: {label(n)}\n  " + n[4][:500].replace("\n", "\n  "))
            elif kind == "weggefallen":
                print(f"\n- WEGGEFALLEN: {label(o)}")
            else:
                print(f"\n~ GEAENDERT: {label(n)}")
                if o[2] != n[2]:
                    print(f"  Ueberschrift: [-{o[2]}-] {{+{n[2]}+}}")
                old_lines, new_lines = o[4].split("\n"), n[4].split("\n")
                sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
                for op, i1, i2, j1, j2 in sm.get_opcodes():
                    if op == "equal":
                        continue
                    print("  " + word_diff("\n".join(old_lines[i1:i2]), "\n".join(new_lines[j1:j2])))


def overview(conn: sqlite3.Connection, csv_path: str | None) -> None:
    rows = []
    summary = []
    for old_v, new_v in version_pairs(conn):
        changes = compare(load_norms(conn, old_v[3]), load_norms(conn, new_v[3]))
        counts = {k: sum(1 for c in changes if c[0] == k) for k in ("geaendert", "neu", "weggefallen")}
        summary.append((new_v[4], new_v[1], old_v[4], counts, new_v[5]))
        for kind, eid, o, n in changes:
            norm = n or o
            rows.append([new_v[1], new_v[2], old_v[4], new_v[4], kind, norm[1], norm[2], new_v[5]])

    if not summary:
        n_versions = conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
        print(f"{n_versions} Fassungen in der DB, aber noch keine Aenderung erfasst. "
              f"Aenderungen werden sichtbar, sobald ein spaeterer Lauf neue Fassungen findet.")
    for new_date, abbr, old_date, c, amended_by in sorted(summary, reverse=True):
        print(f"{new_date}  {abbr:<20} seit {old_date}: {c['geaendert']:>3} geaendert, "
              f"{c['neu']:>3} neu, {c['weggefallen']:>3} weggefallen   {amended_by or ''}")

    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["abkuerzung", "name", "alte_fassung", "neue_fassung", "art",
                             "paragraph", "ueberschrift", "zuletzt_geaendert_durch"])
            writer.writerows(rows)
        print(f"{len(rows)} Paragraph-Aenderungen nach '{csv_path}' geschrieben.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("abkuerzung", nargs="?", help="Gesetzesabkuerzung, z. B. AufenthG")
    parser.add_argument("--csv", help="Uebersicht zusaetzlich als CSV speichern")
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    if args.abkuerzung:
        show_law(conn, args.abkuerzung)
    else:
        overview(conn, args.csv)
    conn.close()


if __name__ == "__main__":
    main()
