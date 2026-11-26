"""toolargs.py: the arguments of every tool, declared once, and what they mean.

Each tool lists its arguments as {name, label, type, default, ...}. The type is one of pages (a page range or list: "1-4", "1,40-45",
"135-end"), number (min, max), select (choices), switch or text. The window draws a field for each, sends the values with the run,
and the tool takes them through `clean`. `describe` is the one plain sentence of what the tool will do with those values.

    declarations(tool)             the tool's argument list, as the window draws it
    clean(tool, raw)               the values with defaults filled in; ValueError says what is wrong
    pages(text, total)             the page numbers a pages value names (`end` needs the total)
    describe(tool, args, pdf)      the sentence for the panel
"""
from __future__ import annotations

import re

AGENT_CHOICES = [["haiku", "Haiku"], ["sonnet", "Sonnet"], ["opus", "Opus"]]
FIELD_CHOICES = [["all", "All fields"], ["title", "Title and author"], ["dates", "Dates"], ["identifiers", "Identifiers"],
                 ["publisher", "Publisher"], ["subjects", "Subjects"], ["notes", "Notes"]]
FIELD_WORDS = {"title": "title and author", "dates": "date", "identifiers": "identifier", "publisher": "publisher", "subjects": "subject", "notes": "note"}
MAX_NOTE = 500

_PAGES = {"name": "pages", "label": "Pages", "type": "pages"}
_AGENT = {"name": "agent", "label": "Agent", "type": "select", "default": "haiku", "choices": AGENT_CHOICES}

ARGS: dict[str, list[dict]] = {
    "ocr": [{**_PAGES, "default": ""},
            {"name": "lang", "label": "Language", "type": "select", "default": "auto", "choices": "ocr_langs"},
            {"name": "dpi", "label": "DPI", "type": "number", "default": 200, "min": 72, "max": 400}],
    "text": [{**_PAGES, "default": ""}],
    "catalogue": [{**_PAGES, "default": "1-4"},
                  {"name": "max_pages", "label": "Max pages", "type": "number", "default": 4, "min": 1, "max": 12},
                  {"name": "dpi", "label": "DPI", "type": "number", "default": 110, "min": 72, "max": 200},
                  {"name": "fields", "label": "Fields", "type": "select", "default": "all", "choices": FIELD_CHOICES},
                  _AGENT,
                  {"name": "note", "label": "Note to the agent", "type": "text", "default": ""}],
    "proofread": [{**_PAGES, "default": ""}, _AGENT],
    "check": [],
    "build": [],
}

_ITEM = r"(?:\d+|end)"
_SPEC = re.compile(rf"{_ITEM}(?:\s*-\s*{_ITEM})?(?:\s*,\s*{_ITEM}(?:\s*-\s*{_ITEM})?)*")


def declarations(tool: str) -> list[dict]:
    """The tool's arguments; an OCR language list is filled in from the OCR script."""
    if tool not in ARGS:
        raise ValueError(f"tool: one of {', '.join(ARGS)}")
    out = []
    for a in ARGS[tool]:
        a = dict(a)
        if a.get("choices") == "ocr_langs":
            import tools
            a["choices"] = [[x["code"], x["name"]] for x in tools.ocr_langs()]
        out.append(a)
    return out


def _parts(text: str) -> list[tuple[int, int | None]]:
    """[(from, to)] in the order given; to is None for `end`, and from -1 for a lone `end`."""
    text = (text or "").strip().lower()
    if not text:
        return []
    if not _SPEC.fullmatch(text):
        raise ValueError("pages: numbers and ranges such as 1-4, 1,40-45 or 135-end")
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        a, b = a.strip(), b.strip()
        if a == "end":
            if b:
                raise ValueError("pages: end can only close a range")
            out.append((-1, None))
            continue
        lo = int(a)
        hi = None if b == "end" else int(b or a)
        if lo < 1 or (hi is not None and hi < lo):
            raise ValueError("pages: ranges run upward from page 1")
        out.append((lo, hi))
    return out


def pages(text: str, total: int | None = None) -> list[int] | None:
    """The page numbers the value names, in order, without repeats; None when it names none (all pages). `end` needs the total."""
    parts = _parts(text)
    if not parts:
        return None
    out: list[int] = []
    for lo, hi in parts:
        if hi is None or lo == -1:
            if not total:
                raise ValueError("the page count is unknown, so end cannot be used")
            lo, hi = (total, total) if lo == -1 else (lo, total)
        out += [n for n in range(lo, hi + 1) if n not in out and (not total or n <= total)]
    return out


def compact(nums: list[int]) -> str:
    """[1, 2, 3, 7] -> "1-3,7": the form the OCR script takes."""
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(out)


def clean(tool: str, raw: dict | None) -> dict:
    """Every declared argument with its value (the default when not given). Unknown names and bad values raise ValueError."""
    raw = raw or {}
    decl = declarations(tool)
    names = {a["name"] for a in decl}
    for k in raw:
        if k not in names:
            raise ValueError(f"{tool}: no argument {k!r}")
    out = {}
    for a in decl:
        v = raw.get(a["name"], a["default"])
        if v is None:
            v = a["default"]
        label = a["label"].lower()
        if a["type"] == "pages":
            v = str(v or "").strip()
            _parts(v)
        elif a["type"] == "number":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{label}: a whole number") from None
            if not a["min"] <= v <= a["max"]:
                raise ValueError(f"{label}: between {a['min']} and {a['max']}")
        elif a["type"] == "select":
            v = str(v)
            if v not in [c[0] for c in a["choices"]]:
                raise ValueError(f"{label}: one of {', '.join(c[0] for c in a['choices'])}")
        elif a["type"] == "switch":
            v = bool(v)
        else:
            v = " ".join(str(v or "").split())
            if len(v) > MAX_NOTE:
                raise ValueError(f"{label}: at most {MAX_NOTE} characters")
        out[a["name"]] = v
    return out


def _phrase(text: str, blank: str = "all pages") -> str:
    """"1,40-45,135-end" -> "pages 1, 40 to 45 and 135 to the end"."""
    parts = _parts(text)
    if not parts:
        return blank
    words = []
    for lo, hi in parts:
        words.append("the last page" if lo == -1 else str(lo) if hi == lo else f"{lo} to the end" if hi is None else f"{lo} to {hi}")
    if len(words) == 1:
        w = words[0]
        return w if w == "the last page" else f"page {w}" if " " not in w else f"pages {w}"
    return "pages " + ", ".join(words[:-1]) + " and " + words[-1]


def _count(text: str) -> int | None:
    """How many pages the value names, or None when it holds `end` (the total is not known here)."""
    parts = _parts(text)
    if not parts or any(hi is None for _, hi in parts):
        return None
    return len(pages(text))


def describe(tool: str, args: dict | None, pdf: bool = True) -> str:
    """One plain sentence: what the tool will do with these values."""
    a = clean(tool, args)
    agent = dict(AGENT_CHOICES).get(a.get("agent"), "")
    if tool == "ocr":
        lang = dict((c[0], c[1]) for c in declarations("ocr")[1]["choices"]).get(a["lang"], a["lang"])
        how = "detecting the language" if a["lang"] == "auto" else f"in {lang}"
        return f"Reads the words on {_phrase(a['pages'])} from their picture at {a['dpi']} dpi, {how}."
    if tool == "text":
        return f"Shows the text layer of {_phrase(a['pages'])}."
    if tool == "proofread":
        return f"{agent} reads {_phrase(a['pages'], 'the chosen pages')} against their text, one page at a time, and proposes corrections."
    if tool == "check":
        return "Runs Hydra's check on the archive and lists what it finds."
    if tool == "build":
        return "Builds the archive with Hydra, which skips one that is already fresh."
    n, cap = _count(a["pages"]), a["max_pages"]
    unit = "pages" if pdf else "parts"
    where = _phrase(a["pages"], "the first pages") if pdf else _phrase(a["pages"], "the first parts").replace("page", "part")
    if n is not None and n <= cap:
        read = f"reads {where}"
    else:
        read = f"reads up to {cap} of {where}" if a["pages"] else f"reads the first {cap} {unit}"
    if pdf:
        read += f" at {a['dpi']} dpi"
    fields = "its library catalogue record" if a["fields"] == "all" else f"the {FIELD_WORDS[a['fields']]} fields of its library catalogue record"
    out = f"{agent} {read} and writes {fields}, each citing its page."
    return out[:-1] + ", and follows your note." if a["note"] else out
