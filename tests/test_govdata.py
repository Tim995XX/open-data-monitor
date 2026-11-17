import sqlite3

import pytest

from open_data_monitor import govdata


def make_pkg(pid="p1", modified="2026-09-01T10:00:00.123456", groups=("heal",), resources=1, **extra):
    """Ein CKAN-Datensatz, wie ihn package_search liefert (gekuerzt)."""
    pkg = {
        "id": pid,
        "name": f"datensatz-{pid}",
        "title": f"Titel {pid}",
        "notes": "Beschreibung",
        "organization": {"name": "rki", "title": "Robert Koch-Institut"},
        "license_id": "dl-by-de/2.0",
        "license_title": "Datenlizenz Deutschland",
        "metadata_created": "2024-03-05T08:00:00",
        "metadata_modified": modified,
        "num_resources": resources,
        "tags": [{"name": "corona"}, {"name": "impfung"}],
        "groups": [{"name": g, "title": f"Gruppe {g}"} for g in groups],
        "resources": [
            {"id": f"{pid}-r{i}", "name": f"Datei {i}", "format": "CSV",
             "url": f"https://example.org/{pid}/{i}.csv", "created": "2024", "last_modified": None}
            for i in range(resources)
        ],
    }
    pkg.update(extra)
    return pkg


class FakeClient:
    """Ersetzt GovdataClient: beantwortet package_search aus einem festen Bestand."""

    def __init__(self, packages: list[dict]):
        self.packages = {p["id"]: p for p in packages}
        self.calls = []

    def call(self, action, params):
        assert action == "package_search"
        self.calls.append(params)
        fq = params["fq"]
        if fq.startswith("groups:"):
            slug = fq.split(":", 1)[1]
            hits = sorted((p for p in self.packages.values()
                           if any(g["name"] == slug for g in p["groups"])), key=lambda p: p["id"])
        else:  # id:("a" OR "b")
            wanted = {part.strip('"') for part in fq[4:-1].split(" OR ")}
            hits = [p for pid, p in self.packages.items() if pid in wanted]
        start = params.get("start", 0)
        page = hits[start:start + params["rows"]]
        if params.get("fl") == "id,metadata_modified":
            # Die ID-Liste liefert Millisekunden + "Z" - anderes Format als der volle Datensatz
            page = [{"id": p["id"], "metadata_modified": p["metadata_modified"][:23] + "Z"} for p in page]
        return {"count": len(hits), "results": page}

    def pause(self):
        pass


@pytest.fixture
def conn(tmp_path):
    c = govdata.init_db(tmp_path / "govdata.db")
    yield c
    c.close()


def test_normalize_ts_gleicht_formate_an():
    assert govdata.normalize_ts("2026-09-01T10:00:00.123Z") == govdata.normalize_ts("2026-09-01T10:00:00.123456")
    assert govdata.normalize_ts(None) == ""


def test_dataset_row_normalisiert_felder():
    row = govdata.dataset_row(make_pkg(), "JETZT")
    assert row[0] == "p1"
    assert row[4:6] == ("rki", "Robert Koch-Institut")
    assert row[11] == "corona, impfung"
    assert row[12] == "https://www.govdata.de/web/guest/suchen/-/details/datensatz-p1"
    assert row[13] == "JETZT"


def test_dataset_row_ohne_organisation():
    row = govdata.dataset_row(make_pkg(organization=None, tags=[]), "JETZT")
    assert row[4:6] == (None, None)
    assert row[11] == ""


def test_init_db_legt_kategorien_an_und_ist_wiederholbar(tmp_path):
    path = tmp_path / "sub" / "g.db"  # Verzeichnis existiert noch nicht
    govdata.init_db(path).close()
    c = govdata.init_db(path)
    assert c.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == len(govdata.GROUPS)
    c.close()


def test_init_db_ergaenzt_removed_at_in_alter_db(tmp_path):
    path = tmp_path / "alt.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE datasets (id TEXT PRIMARY KEY, title TEXT)")
    old.commit()
    old.close()
    c = govdata.init_db(path)
    columns = {row[1] for row in c.execute("PRAGMA table_info(datasets)")}
    assert "removed_at" in columns
    c.close()


def test_store_package_nm_zuordnung_und_ressourcen(conn):
    govdata.store_package(conn, make_pkg(groups=("heal", "envi"), resources=2))
    assert sorted(r[0] for r in conn.execute("SELECT category_slug FROM dataset_categories")) == ["envi", "heal"]
    # Nicht gewaehlte Zusatzkategorie wird in categories nachgetragen
    assert conn.execute("SELECT title FROM categories WHERE slug='envi'").fetchone() == ("Gruppe envi",)
    assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 2


def test_store_package_ersetzt_alte_zuordnungen(conn):
    govdata.store_package(conn, make_pkg(groups=("heal", "soci"), resources=3))
    govdata.store_package(conn, make_pkg(groups=("educ",), resources=1))
    assert [r[0] for r in conn.execute("SELECT category_slug FROM dataset_categories")] == ["educ"]
    assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 1


def test_diff_index():
    live = {"a": "2026-01-01T00:00:00", "b": "2026-02-01T00:00:00", "neu": "2026-03-01T00:00:00"}
    stored = {"a": "2026-01-01T00:00:00.999", "b": "2025-12-31T00:00:00", "weg": "2020-01-01T00:00:00"}
    changed, gone = govdata.diff_index(live, stored)
    assert sorted(changed) == ["b", "neu"]
    assert gone == ["weg"]


def test_id_query():
    assert govdata.id_query(["a", "b"]) == 'id:("a" OR "b")'


def test_sync_erster_lauf_laedt_ganze_kategorie(conn):
    client = FakeClient([make_pkg(f"p{i}") for i in range(3)])
    assert govdata.sync_category(conn, client, "heal", "Gesundheit") == (3, 0)
    assert conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0] == 3
    # Erster Lauf: alles neu (>30 %) -> komplette Kategorie seitenweise
    assert any("start" in c and "fl" not in c for c in client.calls)


def test_sync_zweiter_lauf_holt_nur_geaenderte(conn):
    packages = [make_pkg(f"p{i:02d}") for i in range(10)]
    govdata.sync_category(conn, FakeClient(packages), "heal", "Gesundheit")

    packages[3] = make_pkg("p03", modified="2026-10-01T00:00:00.000000", title="neu")
    client = FakeClient(packages)
    assert govdata.sync_category(conn, client, "heal", "Gesundheit") == (1, 0)
    assert conn.execute("SELECT title FROM datasets WHERE id='p03'").fetchone() == ("neu",)
    # Gezielter Abruf ueber die ID statt Neuladen der Kategorie
    assert any(c["fq"] == 'id:("p03")' for c in client.calls)


def test_sync_unterscheidet_verschoben_und_geloescht(conn):
    packages = [make_pkg(f"p{i:02d}") for i in range(10)]
    govdata.sync_category(conn, FakeClient(packages), "heal", "Gesundheit")

    moved = make_pkg("p01", groups=("soci",), modified="2026-10-01T00:00:00")
    remaining = [p for p in packages if p["id"] not in ("p01", "p02")] + [moved]
    updated, removed = govdata.sync_category(conn, FakeClient(remaining), "heal", "Gesundheit")

    assert (updated, removed) == (1, 1)
    status = dict(conn.execute("SELECT id, removed_at IS NOT NULL FROM datasets"))
    assert status["p01"] == 0  # verschoben -> bleibt aktiv, jetzt in 'soci'
    assert status["p02"] == 1  # geloescht -> removed_at gesetzt, Zeile bleibt
    assert conn.execute(
        "SELECT category_slug FROM dataset_categories WHERE package_id='p01'").fetchall() == [("soci",)]
