"""CZ306: a Complex Collection is a hydra collection group (hydra.yaml `collection_groups`) whose collections come from
several sources: the Collections view reads the groups with what they hold, pages a group's records (each tied to its own
collection), and makes one through hydra's own collectionops."""
from pathlib import Path

import pytest

import apollo
import archives


@pytest.fixture
def draft():
    return Path(archives.ensure_draft()["path"])


def _two_sources(draft):
    from hydra.archive import collections_io
    for name, sid, title, n in (("tarbell-actors", "doc-t", "The History of the Standard Oil Company", 3),
                                ("mapsaud-people", "doc-m", "Mapsaud Alwaleed", 2)):
        collections_io.write(draft / ".hydra" / "sources" / "entries" / f"{name}.json",
                             [{"id": sid, "schema": "Document", "properties": {"title": [title]}}], False)
        collections_io.write(collections_io.path(draft, name),
                             [{"id": f"{name}-{i}", "schema": "Person", "properties": {"name": [f"{name} {i}"], "sources": [sid]}}
                              for i in range(n)], False)


def test_a_group_lists_what_its_collections_hold_and_pages_their_records(draft):
    from hydra.archive import manifest
    _two_sources(draft)
    manifest.set_top(manifest.path(draft), "collection_groups",
                     {"both": {"title": "Both", "collections": ["tarbell-actors", "mapsaud-people", "gone"]}})
    got = archives.collections(str(draft))
    assert got["groups"] == [{"name": "both", "title": "Both", "collections": ["tarbell-actors", "mapsaud-people"], "count": 5,
                              "types": [{"schema": "Person", "label": "Person", "n": 5}],
                              "sources": ["Mapsaud Alwaleed", "The History of the Standard Oil Company"],
                              "added": max(r["added"] for r in got["collections"])}]
    page = archives.group_records(str(draft), "both", 2, 2)
    assert page["total"] == 5 and [(r["id"], r["collection"]) for r in page["records"]] == \
        [("tarbell-actors-2", "tarbell-actors"), ("mapsaud-people-0", "mapsaud-people")]
    assert page["records"][1]["source"] == "Mapsaud Alwaleed"
    assert archives.group_records(str(draft), "nope")["ok"] is False


def test_making_a_complex_collection_writes_one_hydra_group_and_leaves_the_others(draft):
    from hydra.archive import collmeta, manifest
    _two_sources(draft)
    manifest.set_top(manifest.path(draft), "collection_groups", {"tarbell": {"title": "Tarbell", "collections": ["tarbell-actors"]}})
    got = archives.make_group(str(draft), ["tarbell-actors", "mapsaud-people"], "Standard Oil · Mapsaud")
    assert got == {"ok": True, "name": "standard-oil-mapsaud", "title": "Standard Oil · Mapsaud", "collections": ["tarbell-actors", "mapsaud-people"]}
    again = archives.make_group(str(draft), ["mapsaud-people", "tarbell-actors"], "Standard Oil · Mapsaud")
    assert again["name"] == "standard-oil-mapsaud-2"
    groups = collmeta.groups(manifest.read(draft))
    assert groups["tarbell"] == {"title": "Tarbell", "collections": ["tarbell-actors"]}
    assert groups["standard-oil-mapsaud"]["collections"] == ["tarbell-actors", "mapsaud-people"]
    with pytest.raises(ValueError):
        archives.make_group(str(draft), ["tarbell-actors"], "One")
    with pytest.raises(ValueError):
        archives.make_group(str(draft), ["tarbell-actors", "nope"], "Missing")


def test_the_collectiongroup_command_answers_in_json_and_refuses_in_words(draft):
    _two_sources(draft)
    A = lambda **k: type("A", (), {"cmd": "collectiongroup", "archive": str(draft), "collection": None, "group": None, "offset": 0, "limit": 200, **k})()
    out = apollo.other_command(A(title="Both", collections="tarbell-actors,mapsaud-people"))
    assert out["ok"] and out["name"] == "both"
    bad = apollo.other_command(A(title="Lone", collections="tarbell-actors"))
    assert bad["ok"] is False and "two" in bad["why"]
    rows = apollo.other_command(type("A", (), {"cmd": "collections", "archive": str(draft), "collection": None, "group": "both", "offset": 0, "limit": 200})())
    assert rows["total"] == 5


def test_a_type_label_splits_its_words():
    assert archives.schema_label("GeoLink") == "Geo Link" and archives.schema_label("Person") == "Person"


def test_renaming_and_ungrouping_change_only_the_grouping(draft):
    from hydra.archive import collections_io, collmeta, manifest
    _two_sources(draft)
    key = archives.make_group(str(draft), ["tarbell-actors", "mapsaud-people"], "Old name")["name"]
    got = archives.edit_group(str(draft), key, "  New   name ")
    assert got["title"] == "New name" and got["collections"] == ["tarbell-actors", "mapsaud-people"]
    assert collmeta.groups(manifest.read(draft))[key] == {"title": "New name", "collections": ["tarbell-actors", "mapsaud-people"]}
    with pytest.raises(ValueError):
        archives.edit_group(str(draft), key, "   ")
    with pytest.raises(ValueError):
        archives.edit_group(str(draft), "nope", "X")
    archives.edit_group(str(draft), key)
    assert key not in collmeta.groups(manifest.read(draft))
    assert set(collections_io.names(draft)) >= {"tarbell-actors", "mapsaud-people"}
    assert len(collections_io.read(collections_io.path(draft, "tarbell-actors"))[0]) == 3


def test_a_group_takes_and_gives_members_and_keeps_its_title(draft):
    from hydra.archive import collections_io, collmeta, manifest
    _two_sources(draft)
    collections_io.write(collections_io.path(draft, "mapsaud-more"), [{"id": "m-1", "schema": "Person", "properties": {"name": ["M"]}}], False)
    key = archives.make_group(str(draft), ["tarbell-actors", "mapsaud-people"], "Both")["name"]
    got = archives.edit_group(str(draft), key, collections=["tarbell-actors", "mapsaud-people", "mapsaud-more"])
    assert got["title"] == "Both" and got["collections"] == ["tarbell-actors", "mapsaud-people", "mapsaud-more"]
    assert collmeta.groups(manifest.read(draft))[key]["collections"] == ["tarbell-actors", "mapsaud-people", "mapsaud-more"]
    with pytest.raises(ValueError):
        archives.edit_group(str(draft), key, collections=["tarbell-actors"])
