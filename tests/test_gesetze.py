import pytest

from open_data_monitor import gesetze
from conftest import FakeResponse

NS_NEU = "http://rechtsinformationen.bund.de/schema/norm/0.1"
NS_ALT = "http://Inhaltsdaten.LegalDocML.de/1.8.2/"


def beispiel_xml(ns: str = NS_NEU) -> bytes:
    """Gekuerztes Gesetz im LegalDocML-Aufbau: zwei Paragraphen, einer davon
    mit einem zitierten (verschachtelten) Artikel, plus Stand-Vermerk."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akn:akomaNtoso xmlns:akn="{ns}">
  <akn:act>
    <akn:meta><akn:proprietary>
      <standangabe>Zuletzt  geändert durch Art. 1 G v. 1.1.2026</standangabe>
      <hinweis>zuletzt geändert durch Art. 1 G v. 1.1.2026 I Nr. 5</hinweis>
    </akn:proprietary></akn:meta>
    <akn:body>
      <akn:article eId="art-z1">
        <akn:num>§ 1</akn:num>
        <akn:heading>Allgemeines</akn:heading>
        <akn:paragraph><akn:content><akn:p>(1) Erster   Absatz.</akn:p></akn:content></akn:paragraph>
        <akn:paragraph><akn:content><akn:p>(2) Zweiter <akn:ref>Absatz</akn:ref>.</akn:p></akn:content></akn:paragraph>
      </akn:article>
      <akn:article eId="art-z2">
        <akn:num>§ 2</akn:num>
        <akn:paragraph><akn:content><akn:p>Aenderungsbefehl:</akn:p>
          <akn:quotedStructure><akn:article eId="zitat"><akn:num>§ 99</akn:num></akn:article></akn:quotedStructure>
        </akn:content></akn:paragraph>
      </akn:article>
    </akn:body>
  </akn:act>
</akn:akomaNtoso>""".encode("utf-8")


LIST_MEMBER = {
    "item": {
        "legislationIdentifier": "eli/bund/bgbl-1/2004/s1950/2026-07-29/1/deu",
        "abbreviation": "AufenthG",
        "name": "Aufenthaltsgesetz",
        "legislationLegalForce": "InForce",
        "exampleOfWork": {
            "legislationIdentifier": "eli/bund/bgbl-1/2004/s1950",
            "legislationDate": "2004-07-30",
            "datePublished": "2004-08-05",
        },
        "encoding": [
            {"encodingFormat": "text/html", "contentUrl": "/v1/x.html"},
            {"encodingFormat": "application/xml", "contentUrl": "/v1/x.xml"},
        ],
    }
}


@pytest.fixture
def conn(tmp_path):
    c = gesetze.init_db(tmp_path / "gesetze.db")
    yield c
    c.close()


def info(expression="eli/bund/bgbl-1/2004/s1950/2026-07-29/1/deu", **extra):
    base = {"expression_eli": expression, "abbreviation": "AufenthG", "name": "Aufenthaltsgesetz",
            "legislation_date": "2004-07-30", "date_published": "2004-08-05",
            "legal_force": "InForce", "xml_url": "/v1/x.xml"}
    base.update(extra)
    return base


def test_parse_list_item():
    work_eli, data = gesetze.parse_list_item(LIST_MEMBER)
    assert work_eli == "eli/bund/bgbl-1/2004/s1950"
    assert data == info()


def test_parse_list_item_ohne_xml():
    member = {"item": {**LIST_MEMBER["item"], "encoding": []}}
    assert gesetze.parse_list_item(member)[1]["xml_url"] is None


def test_version_date_from_eli():
    assert gesetze.version_date_from_eli("eli/bund/bgbl-1/2004/s1950/2026-07-29/1/deu") == "2026-07-29"


@pytest.mark.parametrize("ns", [NS_NEU, NS_ALT])
def test_parse_norms_beide_namespaces(ns):
    norms, amended = gesetze.parse_norms(beispiel_xml(ns))
    assert [n["eid"] for n in norms] == ["art-z1", "art-z2"]  # zitierter Artikel nicht einzeln
    assert norms[0]["num"] == "§ 1"
    assert norms[0]["heading"] == "Allgemeines"
    assert norms[0]["text"] == "(1) Erster Absatz.\n(2) Zweiter Absatz ."
    assert norms[1]["heading"] is None
    assert "§ 99" in norms[1]["text"]  # gehoert zum aeusseren Paragraphen
    assert amended == "zuletzt geändert durch Art. 1 G v. 1.1.2026 I Nr. 5"


def test_save_version_dedupliziert_texte(conn):
    norms, amended = gesetze.parse_norms(beispiel_xml())
    gesetze.upsert_law(conn, "eli/bund/bgbl-1/2004/s1950", info())
    gesetze.save_version(conn, "eli/bund/bgbl-1/2004/s1950", info(), norms, amended)
    # Zweite Fassung mit identischen Paragraphen: Texte werden nicht doppelt gespeichert
    neu = info("eli/bund/bgbl-1/2004/s1950/2026-10-01/1/deu")
    gesetze.save_version(conn, "eli/bund/bgbl-1/2004/s1950", neu, norms, amended)

    assert conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM norms").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM norm_texts").fetchone()[0] == 2
    assert conn.execute("SELECT version_date, num_norms FROM versions ORDER BY 1").fetchall() == [
        ("2026-07-29", 2), ("2026-10-01", 2)]


def test_upsert_law_hebt_entfernt_auf_und_behaelt_first_seen(conn):
    gesetze.upsert_law(conn, "w", info())
    first = conn.execute("SELECT first_seen FROM laws").fetchone()[0]
    conn.execute("UPDATE laws SET removed_at = 'x'")
    gesetze.upsert_law(conn, "w", info(abbreviation="NeuAbk"))
    assert conn.execute("SELECT abbreviation, removed_at, first_seen FROM laws").fetchone() == ("NeuAbk", None, first)


def test_mark_removed_regeln(conn):
    for w in ("a", "b", "c"):
        gesetze.upsert_law(conn, w, info())
    assert gesetze.mark_removed(conn, {"a": {}}, partial_run=True) == 0          # Probelauf
    assert gesetze.mark_removed(conn, {"a": {}}, partial_run=False) == 0         # Liste unplausibel kurz
    assert gesetze.mark_removed(conn, {w: {} for w in "abcdefghij"}, partial_run=False) == 0
    live = {w: {} for w in "abdefghijk"}  # 'c' fehlt, Liste ist vollstaendig genug
    assert gesetze.mark_removed(conn, live, partial_run=False) == 1
    assert conn.execute("SELECT work_eli FROM laws WHERE removed_at IS NOT NULL").fetchall() == [("c",)]


def test_run_ende_zu_ende_ohne_netzwerk(tmp_path, monkeypatch, no_sleep):
    """Liste + XML werden aus festen Antworten bedient; zweiter Lauf findet nichts Neues."""
    def fake_get(session, path, params=None):
        if path == "/v1/legislation":
            return FakeResponse(json_data={"totalItems": 1, "member": [LIST_MEMBER]})
        assert path == "/v1/x.xml"
        return FakeResponse(content=beispiel_xml())

    monkeypatch.setattr(gesetze, "http_get", fake_get)
    db_path = tmp_path / "g.db"
    gesetze.run(db_path, session=None)
    gesetze.run(db_path, session=None)

    c = gesetze.init_db(db_path)
    assert c.execute("SELECT COUNT(*) FROM laws").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM norms").fetchone()[0] == 2
    c.close()
