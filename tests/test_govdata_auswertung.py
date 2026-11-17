import csv

from open_data_monitor import govdata, govdata_auswertung
from test_govdata import make_pkg


def test_year_of():
    assert govdata_auswertung.year_of("2024-03-05T08:00:00") == "2024"
    assert govdata_auswertung.year_of("unbekannt") is None
    assert govdata_auswertung.year_of(None) is None


def test_zaehlung_je_jahr_ohne_entfernte(tmp_path, capsys):
    db_path = tmp_path / "g.db"
    conn = govdata.init_db(db_path)
    govdata.store_package(conn, make_pkg("a", groups=("heal",), metadata_created="2023-01-01T00:00:00"))
    govdata.store_package(conn, make_pkg("b", groups=("heal", "soci"), metadata_created="2024-06-01T00:00:00"))
    govdata.store_package(conn, make_pkg("c", groups=("heal",), metadata_created="2024-07-01T00:00:00"))
    conn.execute("UPDATE datasets SET removed_at = 'x' WHERE id = 'c'")
    conn.commit()

    counts = govdata_auswertung.count_by_year(conn)
    conn.close()
    assert counts["2023"] == {"heal": 1}
    assert counts["2024"] == {"heal": 1, "soci": 1}  # 'c' ist entfernt und zaehlt nicht

    out_csv = tmp_path / "aus.csv"
    govdata_auswertung.run(db_path, out_csv)
    rows = list(csv.reader(open(out_csv, encoding="utf-8")))
    header = rows[0]
    assert header[0] == "jahr" and header[-1] == "summe"
    row_2024 = dict(zip(header, rows[2]))
    assert row_2024["heal"] == "1" and row_2024["soci"] == "1" and row_2024["summe"] == "2"
    assert "Prozentualer Anteil" in capsys.readouterr().out
