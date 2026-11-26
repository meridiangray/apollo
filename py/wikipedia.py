"""wikipedia.py: one Wikipedia article as a dropped text source, fetched only when the person asks.

    fetch(lang, query)        the article's text and facts: {title, text, url, language, retrieved}
    add(lang, query)          fetch, then take it in through sourcedrop.add (publisher Wikipedia, its URL, language, date)

This is the one place Apollo uses the network for a source, and only from the Wikipedia button on the Sources view. The text
comes through hydra's own extraction (hydra.api.webtext, imported read only): readability and a template for the article's
body, so headings and paragraphs stay and the menus, infobox, references and navigation do not. A refusal is a plain
sentence in a ValueError: not found, a disambiguation page, or the network blocked.
"""
from __future__ import annotations

import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import sourcedrop

LANG = re.compile(r"^[a-z]{2,3}(-[a-z]+)?$")
MAX_BYTES = 8_000_000
UA = {"User-Agent": "Mozilla/5.0 (compatible; Apollo source reader)", "Accept": "text/html"}
# the article's own body in Wikipedia's page: its text blocks, not its boxes, tables, references or edit links
TEMPLATE = {"content": ["div.mw-parser-output"],
            "drop": ["table", "sup.reference", "span.mw-editsection", "div.navbox", "div.reflist", "div.hatnote", "div.thumb",
                     "figure", "style", "div.mw-references-wrap", "ol.references", "div.toc", "div.metadata", "div.sidebar"]}


def _webtext():
    if str(sourcedrop.HYDRA_SRC) not in sys.path:
        sys.path.append(str(sourcedrop.HYDRA_SRC))
    from hydra.api import webtext
    return webtext


def parse(lang: str, query: str) -> tuple[str, str]:
    """(language, article title) from a language and a title, or a Wikipedia URL (whose host names the language)."""
    q = (query or "").strip()
    if not q:
        raise ValueError("Give an article title or its address.")
    lang = (lang or "en").strip().lower() or "en"
    if re.match(r"^https?://", q, re.I):
        u = urllib.parse.urlsplit(q)
        m = re.match(r"^([a-z]{2,3}(?:-[a-z]+)?)\.(?:m\.)?wikipedia\.org$", u.netloc.lower())
        if not m or not u.path.startswith("/wiki/"):
            raise ValueError("That is not a Wikipedia article address.")
        return m.group(1), urllib.parse.unquote(u.path[len("/wiki/"):]).replace("_", " ")
    if not LANG.match(lang):
        raise ValueError("The language is a Wikipedia code such as en or ar.")
    return lang, q


def _get(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(MAX_BYTES + 1)
            final = r.geturl()
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            raise ValueError("Wikipedia has no article with that title.") from None
        raise ValueError(f"Wikipedia answered {e.code}; the article could not be fetched.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("Wikipedia could not be reached: the network is blocked or down.") from None
    if len(data) > MAX_BYTES:
        raise ValueError("The article is too large to take in.")
    return data, final


def fetch(lang: str, query: str) -> dict:
    lang, title = parse(lang, query)
    url = f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe="()_,'-")
    data, final = _get(url)
    raw = data.decode("utf-8", "replace")
    if re.search(r'class="[^"]*\b(?:dmbox-disambig|mw-disambig)\b|id="disambigbox"|wgCategories[^]]*[Dd]isambiguation', raw):
        raise ValueError("That title is a disambiguation page: it lists several articles. Give the exact one.")
    text, headline, _ = _webtext().html_text(raw, urllib.parse.urlsplit(final).netloc, TEMPLATE)
    if len(re.sub(r"^#+ .*$", "", text, flags=re.M).split()) < 40:
        raise ValueError("No article text was found on that page.")
    head = (headline or title).strip()
    head = re.sub(r"\s*[-–—|]\s*Wikipedia.*$", "", head)
    return {"title": head or title, "text": text, "url": final, "language": lang, "retrieved": date.today().isoformat()}


def add(lang: str, query: str) -> dict:
    """Fetch the article and take it in as a text source. Returns sourcedrop.add's answer."""
    got = fetch(lang, query)
    with tempfile.TemporaryDirectory(prefix="apollo-wiki-") as d:
        f = Path(d) / (sourcedrop.slugify(got["title"]) + ".txt")
        f.write_text(got["text"], encoding="utf-8", newline="\n")
        return sourcedrop.add(f, title=got["title"], extra={"publisher": "Wikipedia", "url": got["url"], "language": got["language"],
                                                              "retrieved": got["retrieved"]})
