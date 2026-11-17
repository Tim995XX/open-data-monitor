from pathlib import Path

import pytest

from open_data_monitor import cli, eu_parlament, gesetze, govdata


def test_db_pfad_aus_data_dir_oder_db(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(govdata, "run", lambda db_path, session, slugs=None: calls.append((db_path, slugs)))

    assert cli.main(["--data-dir", str(tmp_path), "govdata", "--kategorie", "heal"]) == 0
    assert cli.main(["govdata", "--db", str(tmp_path / "x.db")]) == 0
    assert calls == [(tmp_path / "govdata_kategorien.db", ["heal"]), (tmp_path / "x.db", None)]


def test_unbekannte_kategorie_wird_abgelehnt():
    with pytest.raises(SystemExit):
        cli.main(["govdata", "--kategorie", "gibtsnicht"])


def test_eu_nutzt_temporaeres_arbeitsverzeichnis(monkeypatch, tmp_path):
    seen = {}

    def fake_run(db_path, session, work_dir, min_date):
        seen.update(db=db_path, work=work_dir, min_date=min_date, existed=work_dir.is_dir())

    monkeypatch.setattr(eu_parlament, "run", fake_run)
    cli.main(["--data-dir", str(tmp_path), "eu", "--ab", "2024-01-01"])
    assert seen["db"] == tmp_path / "eu_abstimmungen.db"
    assert seen["min_date"] == "2024-01-01"
    assert seen["existed"] and not Path(seen["work"]).exists()  # danach aufgeraeumt


def test_alle_laeuft_weiter_wenn_eine_quelle_ausfaellt(monkeypatch, tmp_path):
    ran = []

    def kaputt(*args, **kwargs):
        ran.append("govdata")
        raise RuntimeError("Quelle nicht erreichbar")

    monkeypatch.setattr(govdata, "run", kaputt)
    monkeypatch.setattr(gesetze, "run", lambda *a, **k: ran.append("gesetze"))
    monkeypatch.setattr(eu_parlament, "run", lambda *a, **k: ran.append("eu"))

    assert cli.main(["--data-dir", str(tmp_path), "alle"]) == 1
    assert ran == ["govdata", "gesetze", "eu"]
