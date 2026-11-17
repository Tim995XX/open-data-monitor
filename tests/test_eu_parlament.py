import pytest

from open_data_monitor import eu_parlament

# Gekuerzter Datensatz im Aufbau des Parltrack-Dumps
VOTE = {
    "voteid": 189210,
    "ts": "2015-03-11T12:30:00",
    "title": "Bericht ueber ...",
    "doc": "A8-0347/2015",
    "epref": ["2015/0903(NLE)", "2015/0904(NLE)"],
    "url": "https://www.europarl.europa.eu/doceo/PV-8-2015-03-11-RCV.xml",
    "votes": {
        "+": {"total": 3, "groups": {"PPE": [1, 2], "S&D": [3]}},
        "-": {"total": 1, "groups": {"ECR": [4]}},
        "0": {"total": 0, "groups": {}},
        "kommentar": "kein dict - wird ignoriert",
    },
}


@pytest.fixture
def conn(tmp_path):
    c = eu_parlament.init_db(tmp_path / "eu.db")
    yield c
    c.close()


def test_vote_row():
    assert eu_parlament.vote_row(VOTE) == (
        "189210", "2015-03-11T12:30:00", "Bericht ueber ...", "A8-0347/2015",
        "2015/0903(NLE), 2015/0904(NLE)", VOTE["url"], 3, 1, 0,
    )


def test_vote_row_fehlende_bloecke_zaehlen_null():
    row = eu_parlament.vote_row({"voteid": 1, "votes": {"+": {"total": 5}}, "epref": "x"})
    assert row[4] == "x"
    assert row[6:] == (5, 0, 0)


def test_fraktion_rows():
    assert sorted(eu_parlament.fraktion_rows(VOTE)) == [
        ("189210", "dafuer", "PPE", 2),
        ("189210", "dafuer", "S&D", 1),
        ("189210", "dagegen", "ECR", 1),
    ]


def test_filter_votes():
    votes = [{"ts": "2014-12-31T23:59"}, {"ts": "2015-01-01T00:00"}, {"ts": None}, {}]
    assert eu_parlament.filter_votes(votes, "2015-01-01") == [{"ts": "2015-01-01T00:00"}]


def test_store_votes_ist_wiederholbar(conn):
    eu_parlament.store_votes(conn, [VOTE])
    eu_parlament.store_votes(conn, [VOTE])  # zweiter Lauf: keine Duplikate
    assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM vote_fraktionen").fetchone()[0] == 3
    assert conn.execute(
        "SELECT SUM(anzahl) FROM vote_fraktionen WHERE choice='dafuer'").fetchone()[0] == 3
