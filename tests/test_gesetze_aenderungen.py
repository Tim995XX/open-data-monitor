import csv

import pytest

from open_data_monitor import gesetze, gesetze_aenderungen as ga

WORK = "eli/bund/bgbl-1/2004/s1950"


def norm(eid, num, text, heading=None):
    return {"eid": eid, "num": num, "heading": heading, "text": text}


@pytest.fixture
def db_path(tmp_path):
    """DB mit zwei Fassungen desselben Gesetzes: § 1 geaendert, § 2 weggefallen, § 3 neu."""
    path = tmp_path / "g.db"
    conn = gesetze.init_db(path)
    info = {"abbreviation": "AufenthG", "name": "Aufenthaltsgesetz", "legislation_date": None,
            "date_published": None, "legal_force": "InForce", "xml_url": "/x"}
    gesetze.upsert_law(conn, WORK, info)
    gesetze.save_version(conn, WORK, {**info, "expression_eli": f"{WORK}/2026-01-01/1/deu"},
                         [norm("art-z1", "§ 1", "Die Frist betraegt drei Monate."),
                          norm("art-z2", "§ 2", "Alt.")], None)
    gesetze.save_version(conn, WORK, {**info, "expression_eli": f"{WORK}/2026-07-01/1/deu"},
                         [norm("art-z1", "§ 1", "Die Frist betraegt sechs Monate."),
                          norm("art-z3", "§ 3", "Neu.")], "zuletzt geändert durch Art. 2 G v. 1.7.2026")
    conn.commit()
    conn.close()
    return path


def test_compare_erkennt_alle_arten():
    old = {"a": (0, "§ 1", None, "h1", "x"), "b": (1, "§ 2", None, "h2", "y")}
    new = {"a": (0, "§ 1", None, "h1-neu", "x2"), "c": (1, "§ 3", None, "h3", "z")}
    kinds = {eid: kind for kind, eid, _, _ in ga.compare(old, new)}
    assert kinds == {"a": "geaendert", "b": "weggefallen", "c": "neu"}


def test_compare_ueberschrift_allein_ist_aenderung():
    old = {"a": (0, "§ 1", "Alt", "h", "x")}
    new = {"a": (0, "§ 1", "Neu", "h", "x")}
    assert [c[0] for c in ga.compare(old, new)] == ["geaendert"]


def test_compare_unveraendert_liefert_nichts():
    same = {"a": (0, "§ 1", None, "h", "x")}
    assert ga.compare(same, dict(same)) == []


def test_word_diff():
    assert ga.word_diff("die Frist betraegt drei Monate", "die Frist betraegt sechs Monate") == \
        "die Frist betraegt [-drei-] {+sechs+} Monate"


def test_word_diff_kuerzt_lange_gleiche_passagen():
    words = " ".join(f"w{i}" for i in range(20))
    assert ga.word_diff(words + " alt", words + " neu") == \
        "w0 w1 w2 w3 w4 ... w15 w16 w17 w18 w19 [-alt-] {+neu+}"


def test_overview_und_csv(db_path, tmp_path, capsys):
    out = tmp_path / "aenderungen.csv"
    ga.run(db_path, csv_path=str(out))
    text = capsys.readouterr().out
    assert "AufenthG" in text and "1 geaendert" in text
    rows = list(csv.reader(open(out, encoding="utf-8"), delimiter=";"))
    assert rows[0][:5] == ["abkuerzung", "name", "alte_fassung", "neue_fassung", "art"]
    assert sorted(r[4] for r in rows[1:]) == ["geaendert", "neu", "weggefallen"]


def test_show_law_wortgenau(db_path, capsys):
    ga.run(db_path, abbreviation="aufenthg")  # Gross-/Kleinschreibung egal
    text = capsys.readouterr().out
    assert "2026-01-01 -> 2026-07-01" in text
    assert "[-drei-] {+sechs+}" in text
    assert "+ NEU: § 3" in text and "- WEGGEFALLEN: § 2" in text


def test_show_law_unbekannt(db_path, capsys):
    ga.run(db_path, abbreviation="GibtEsNicht")
    assert "nicht in der DB" in capsys.readouterr().out
