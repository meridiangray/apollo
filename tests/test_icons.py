"""Every decision kind the engine flows write has an icon of its own in the generated entity-icons.js (not _default)."""
import json
import re

import conftest

ICONS = conftest.APOLLO / "go" / "ui" / "entity-icons.js"
FLOWS = conftest.TESTING / "pageflow" / "flows"
# kinds that are not decisions: questions the flows raise, or the card kinds of a finance change, never a "Found" chip
NOT_DECISIONS = {"finance", "assemble", "wikidata", "reference", "links"}


def icons() -> dict:
    text = ICONS.read_text(encoding="utf-8")
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def flow_kinds() -> set:
    """The kinds the flows' own source writes: take("kind", ...) and dec.append({"kind": "kind", ...})."""
    found = set()
    for f in FLOWS.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        found |= set(re.findall(r'\btake\("([a-z-]+)"', src))
        found |= set(re.findall(r'dec\.append\(\{"kind": "([a-z-]+)"', src))
    return found - NOT_DECISIONS


def test_every_engine_decision_kind_has_its_own_icon():
    got = icons()
    kinds = flow_kinds() | {"accept", "reject", "leave", "verdict", "date", "order", "prop"}   # wikidata's verdict words, refine/timeline
    assert len(kinds) > 25
    missing = sorted(k for k in kinds if k not in got)
    assert not missing, f"decision kinds without an icon: {missing}"
    same = sorted(k for k in kinds if got[k] == got["_default"])
    assert not same, f"decision kinds drawn as the default circle: {same}"
    marks = [got[k] for k in kinds]
    assert len(set(marks)) == len(marks), "two decision kinds share one icon"


def test_decision_link_is_not_the_link_schema():
    got = icons()
    assert got["link"] != got["_default"]
    assert "Link" not in got or got["Link"] != got["link"]
