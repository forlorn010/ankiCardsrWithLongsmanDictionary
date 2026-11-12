"""Longman Dictionary scraper used by the Anki card generator.

The module deliberately uses only the Python standard library, so the app can
run on a fresh Python installation without ``pip install``.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable, Iterator, Optional

BASE_URL = "https://www.ldoceonline.com"
DICTIONARY_URL = BASE_URL + "/dictionary/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 20
DEFAULT_DELAY = 0.8

_VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_SKIP_TEXT_ELEMENTS = {"script", "style", "noscript"}


# ---------------------------------------------------------------------------
# A tiny DOM
# ---------------------------------------------------------------------------


class Node:
    """A minimal HTML node with just enough navigation for this project."""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: Optional[dict[str, str]] = None,
                 parent: Optional["Node"] = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node | str] = []
        self.parent = parent

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def has_class(self, name: str) -> bool:
        return name in self.classes

    def find_all(self, tag: Optional[str] = None,
                 cls: Optional[str] = None) -> Iterator["Node"]:
        for child in self.children:
            if not isinstance(child, Node):
                continue
            if (tag is None or child.tag == tag) and (
                cls is None or child.has_class(cls)
            ):
                yield child
            yield from child.find_all(tag, cls)

    def find(self, tag: Optional[str] = None,
             cls: Optional[str] = None) -> Optional["Node"]:
        return next(self.find_all(tag, cls), None)

    def direct_children(self, tag: Optional[str] = None,
                        cls: Optional[str] = None) -> Iterator["Node"]:
        for child in self.children:
            if not isinstance(child, Node):
                continue
            if (tag is None or child.tag == tag) and (
                cls is None or child.has_class(cls)
            ):
                yield child

    def text(self) -> str:
        parts: list[str] = []
        self._collect_text(parts)
        return "".join(parts)

    def _collect_text(self, parts: list[str]) -> None:
        if self.tag in _SKIP_TEXT_ELEMENTS:
            return
        if self.tag == "br":
            parts.append(" ")
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            else:
                child._collect_text(parts)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Node {self.tag} class={self.attrs.get('class', '')!r}>"


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        node = Node(tag, {key: value or "" for key, value in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str,
                           attrs: list[tuple[str, Optional[str]]]) -> None:
        node = Node(tag, {key: value or "" for key, value in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        # Close the nearest matching open element. This also recovers from
        # unclosed <p>/<li> tags that are common in hand-written HTML.
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    parser = _TreeBuilder()
    parser.feed(html)
    parser.close()
    return parser.root


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def normalize_headword(value: str) -> str:
    value = value.replace("\u00b7", "").replace("\u2027", "")
    value = value.replace("\u2019", "'").replace("\u2018", "'")
    value = re.sub(r"[\s\-–—]+", " ", value).strip().lower()
    value = re.sub(r"[.,;:!?]+$", "", value)
    return value


def normalize_pos(value: str) -> str:
    value = value.replace("\u00b7", " ")
    value = re.sub(r"[/|]", ",", value)
    value = re.sub(r"[^A-Za-z, -]", " ", value)
    return re.sub(r"\s+", " ", value).strip().lower()


def split_pos(value: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r",", normalize_pos(value))
        if part.strip()
    ]


def normalize_example(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u2022", " ")
    value = value.replace("\u2019", "'").replace("\u2018", "'")
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^[\-–—·•]+\s*", "", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([(])\s+", r"\1", value)
    value = re.sub(r"\s+([)])", r"\1", value)
    return value.strip()


# Inflected forms that regex rules cannot derive. Only common irregular verbs.
_IRREGULAR_FORMS: dict[str, tuple[str, ...]] = {
    "be": ("am", "is", "are", "was", "were", "been", "being"),
    "become": ("became", "becoming"),
    "begin": ("began", "begun", "beginning"),
    "break": ("broke", "broken", "breaking"),
    "bring": ("brought", "bringing"),
    "build": ("built", "building"),
    "buy": ("bought", "buying"),
    "catch": ("caught", "catching"),
    "choose": ("chose", "chosen", "choosing"),
    "come": ("came", "coming"),
    "do": ("does", "did", "done", "doing"),
    "drink": ("drank", "drunk", "drinking"),
    "drive": ("drove", "driven", "driving"),
    "eat": ("ate", "eaten", "eating"),
    "fall": ("fell", "fallen", "falling"),
    "feel": ("felt", "feeling"),
    "find": ("found", "finding"),
    "fly": ("flew", "flown", "flying"),
    "forget": ("forgot", "forgotten", "forgetting"),
    "get": ("got", "gotten", "getting"),
    "give": ("gave", "given", "giving"),
    "go": ("went", "gone", "going"),
    "grow": ("grew", "grown", "growing"),
    "have": ("has", "had", "having"),
    "hear": ("heard", "hearing"),
    "hold": ("held", "holding"),
    "keep": ("kept", "keeping"),
    "know": ("knew", "known", "knowing"),
    "lead": ("led", "leading"),
    "leave": ("left", "leaving"),
    "lose": ("lost", "losing"),
    "make": ("made", "making"),
    "meet": ("met", "meeting"),
    "pay": ("paid", "paying"),
    "put": ("putting"),
    "read": ("reading"),
    "ride": ("rode", "ridden", "riding"),
    "ring": ("rang", "rung", "ringing"),
    "rise": ("rose", "risen", "rising"),
    "run": ("ran", "running"),
    "say": ("said", "saying"),
    "see": ("saw", "seen", "seeing"),
    "sell": ("sold", "selling"),
    "send": ("sent", "sending"),
    "shake": ("shook", "shaken", "shaking"),
    "shine": ("shone", "shining"),
    "show": ("shown", "showing"),
    "sing": ("sang", "sung", "singing"),
    "sit": ("sat", "sitting"),
    "sleep": ("slept", "sleeping"),
    "speak": ("spoke", "spoken", "speaking"),
    "spend": ("spent", "spending"),
    "stand": ("stood", "standing"),
    "steal": ("stole", "stolen", "stealing"),
    "swim": ("swam", "swum", "swimming"),
    "take": ("took", "taken", "taking"),
    "teach": ("taught", "teaching"),
    "tell": ("told", "telling"),
    "think": ("thought", "thinking"),
    "throw": ("threw", "thrown", "throwing"),
    "understand": ("understood", "understanding"),
    "wake": ("woke", "woken", "waking"),
    "wear": ("wore", "worn", "wearing"),
    "win": ("won", "winning"),
    "write": ("wrote", "written", "writing"),
}


def _is_vowel(char: str) -> bool:
    return char.lower() in "aeiou"


def _looks_cvc(word: str) -> bool:
    """Return True for short consonant-vowel-consonant words (run, stop)."""
    if len(word) < 3:
        return False
    a, b, c = word[-3], word[-2], word[-1]
    return not _is_vowel(a) and _is_vowel(b) and not _is_vowel(c) and c not in "wxy"


def _base_forms(word: str) -> set[str]:
    """Generate regular inflections for a single lowercase token."""
    forms = {word, word + "'s"}
    irregular = _IRREGULAR_FORMS.get(word)
    if irregular:
        forms.update(irregular)

    if word.endswith("y") and len(word) > 1 and not _is_vowel(word[-2]):
        stem = word[:-1]
        forms.update({stem + "ies", stem + "ied", stem + "ier", stem + "iest",
                      word + "ing"})
    elif word.endswith("ie"):
        forms.update({word + "s", word[:-2] + "ying", word + "d"})
    elif word.endswith(("s", "x", "z", "ch", "sh", "o")):
        forms.update({word + "es", word + "ed", word + "ing"})
        if word.endswith("o"):
            forms.add(word + "ing")
    elif word.endswith("e"):
        forms.update({word + "s", word + "d", word + "r", word + "st",
                      word[:-1] + "ing"})
    else:
        forms.update({word + "s", word + "ed", word + "ing",
                      word + "er", word + "est"})
        if _looks_cvc(word):
            doubled = word + word[-1]
            forms.update({doubled + "ed", doubled + "ing", doubled + "er",
                          doubled + "est"})

    if word.endswith("f"):
        forms.add(word[:-1] + "ves")
    if word.endswith("fe"):
        forms.add(word[:-2] + "ves")
    return forms


def replacement_forms(word: str, marked: Iterable[str] = ()) -> list[str]:
    """Return regex alternatives that should be blanked in an example."""
    clean = normalize_headword(word)
    if not clean:
        return []

    tokens = clean.split()
    forms: set[str] = set()

    if len(tokens) == 1:
        forms.update(_base_forms(tokens[0]))
    else:
        first_forms = _base_forms(tokens[0])
        rest = tokens[1:]
        for first in first_forms:
            forms.add(" ".join([first, *rest]))
        # Phrases ending in a countable noun can be pluralised.
        last_forms = _base_forms(tokens[-1])
        for last in last_forms:
            forms.add(" ".join([*tokens[:-1], last]))

    for marked_text in marked:
        candidate = normalize_headword(marked_text)
        if not candidate:
            continue
        # Do not blank isolated words from a multi-word headword; the phrase
        # itself is blanked as one unit.
        if len(tokens) > 1 and len(candidate.split()) == 1:
            continue
        forms.add(candidate)

    # Longest alternatives first so phrases win over their component words.
    forms.discard("")
    return sorted(forms, key=lambda item: (-len(item), item))


BLANK = "_____"

_VOWELS = set("aeiou")
_ENDINGS_AFTER_DROPPED_E = {"ing", "ed", "er", "est"}


def _ending_after_base(base: str, matched: str) -> str:
    """Return the ending of ``matched`` when it follows ``base`` literally."""
    base_l = base.casefold()
    matched_l = matched.casefold()
    if not base_l or len(matched_l) <= len(base_l):
        return ""

    if matched_l.startswith(base_l):
        # Consonant doubling before -ed/-ing/-er/-est: stop → stopped.
        if (
            len(base_l) >= 2
            and base_l[-1] not in _VOWELS
            and base_l[-1] not in "wxy"
            and matched_l.startswith(base_l + base_l[-1])
        ):
            return matched[len(base_l) + 1:]
        return matched[len(base_l):]

    # Silent final -e dropped before a vowel suffix: make → making.
    if base_l.endswith("e") and len(base_l) > 2:
        stem = base_l[:-1]
        if matched_l.startswith(stem):
            ending = matched_l[len(stem):]
            if ending in _ENDINGS_AFTER_DROPPED_E:
                return matched[len(stem):]

    return ""


def _visible_tail(base: str, matched: str) -> str:
    """Return the text that should follow the blank.

    The blank replaces the dictionary form, so ``neglect`` inside ``neglected``
    becomes ``_____ed``. For phrasal verbs the first word is usually the one
    that inflects, so ``takes off`` becomes ``_____s off``. When the inflected
    form does not contain the base form literally (``study`` → ``studied``,
    ``go`` → ``went``), the whole form is hidden and an empty string is
    returned.
    """
    base_tokens = base.split()
    matched_tokens = re.split(r"[\s\-\u2013\u2014]+", matched)
    if len(base_tokens) > 1 and len(matched_tokens) > 1:
        ending = _ending_after_base(base_tokens[0], matched_tokens[0])
        if ending:
            return ending + " " + " ".join(matched_tokens[1:])
    return _ending_after_base(base, matched)


def blank_word(sentence: str, word: str, marked: Iterable[str] = ()) -> Optional[str]:
    """Replace the target word (and its inflections) with ``_____``.

    If the example contains an inflected form rather than the dictionary form,
    the inflection's ending is kept visible after the blank (``neglected`` →
    ``_____ed``). Returns ``None`` when no form of the word occurs in the
    sentence.
    """
    forms = replacement_forms(word, marked)
    if not forms:
        return None

    base = normalize_headword(word)
    alternatives = []
    for form in forms:
        pieces = [re.escape(piece) for piece in form.split()]
        alternatives.append(r"[\s\-\u2013\u2014]+".join(pieces))
    pattern = re.compile(
        r"(?<![\w])(?:" + "|".join(alternatives) + r")(?![\w])",
        re.IGNORECASE,
    )

    def replacement(match: re.Match[str]) -> str:
        return BLANK + _visible_tail(base, match.group(0))

    result, count = pattern.subn(replacement, sentence)
    return result if count else None


# ---------------------------------------------------------------------------
# Longman page model
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    """One ``span.dictentry`` from the main dictionary column."""

    node: Node
    headword: str
    pos_list: list[str]
    homnum: str = ""
    american_of: Optional[str] = None
    groups: list[Node] = field(default_factory=list)

    @property
    def pos_display(self) -> str:
        return ", ".join(self.pos_list)


@dataclass
class ParsedPage:
    """The pieces of a Longman entry page the scraper cares about."""

    url: str
    entries: list[Entry]
    requested: str

    def entries_for(self, part_of_speech: Optional[str]) -> list[Entry]:
        if not part_of_speech:
            return self.entries

        wanted = normalize_pos(part_of_speech)
        matching = [entry for entry in self.entries if wanted in entry.pos_list]
        if not matching:
            return []

        # Prefer an entry whose *main* part of speech is the requested one.
        # This matters for pages like "color", where a "noun, verb" entry
        # contains noun examples and a separate "verb" entry contains verb
        # examples.
        primary = [entry for entry in matching if entry.pos_list[0] == wanted]
        return primary or matching


def _find_entry_column(root: Node) -> Optional[Node]:
    """Follow Longman's content path and return the main dictionary column."""
    body = root.find("body", "page") or root.find("body")
    search_root = body or root
    content = search_root.find("div", "content") or search_root
    cell = (
        content.find("div", "responsive_cell6")
        or content.find("div", "responsive_cell")
        or content
    )
    entry_content = cell.find("div", "entry_content") or cell
    return entry_content.find("div", "dictionary")


def _node_text(node: Optional[Node]) -> str:
    return normalize_example(node.text()) if node is not None else ""


def _find_american_spelling(entry: Entry) -> Optional[str]:
    for definition in entry.node.find_all(cls="DEF"):
        text = definition.text()
        match = re.search(r"american\s+spelling\s+of\s+(.+)", text, re.IGNORECASE)
        if not match:
            continue
        ref = definition.find(cls="REFHWD")
        candidate = ref.text() if ref is not None else match.group(1)
        candidate = normalize_headword(candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate:
            return candidate
    return None


def _main_groups(entry: Entry) -> list[Node]:
    """Return example groups that belong to the entry's main headword."""
    head = normalize_headword(entry.headword)
    groups: list[Node] = []
    for assetlink in entry.node.direct_children("span", "assetlink"):
        for group in assetlink.find_all("span", "exaGroup"):
            title_node = group.find("span", "title")
            title = normalize_headword(title_node.text()) if title_node else ""
            if title and head and title != head:
                continue
            groups.append(group)
    return groups


def parse_entry_page(html: str, url: str, requested_word: str) -> ParsedPage:
    root = parse_html(html)
    column = _find_entry_column(root)
    if column is None:
        return ParsedPage(url=url, entries=[], requested=requested_word)

    entries: list[Entry] = []
    section = 0
    is_first_entry = True
    for node in column.find_all("span", "dictentry"):
        # A Longman page can contain several dictionaries in one column
        # (e.g. Contemporary English followed by Business English). Only the
        # first section matches the selector in the project requirements.
        has_section_intro = node.find("span", "dictionary_intro") is not None
        if has_section_intro and not is_first_entry:
            section += 1
        is_first_entry = False
        if section > 0:
            continue

        hwd_node = (
            node.find("span", "HWD")
            or node.find("span", "PHRVBHWD")
        )
        if hwd_node is None:
            continue
        headword = normalize_headword(hwd_node.text())
        if not headword:
            continue

        pos_node = node.find("span", "POS")
        pos_list = split_pos(pos_node.text()) if pos_node is not None else []
        homnum_node = node.find("span", "HOMNUM")
        entry = Entry(
            node=node,
            headword=headword,
            pos_list=pos_list,
            homnum=homnum_node.text().strip() if homnum_node is not None else "",
        )
        entry.american_of = _find_american_spelling(entry)
        entry.groups = _main_groups(entry)
        entries.append(entry)

    return ParsedPage(url=url, entries=entries, requested=requested_word)


def _example_nodes(group: Node) -> Iterator[Node]:
    for node in group.find_all("span"):
        classes = node.classes
        if "exa" in classes and "exaGroup" not in classes:
            yield node


def _first_usable_example(group: Node, word: str,
                          extra_forms: Iterable[str] = ()) -> Optional[
                              tuple[Node, list[str], str, str]
                          ]:
    """Return the first example in which the target word can be blanked.

    Longman's very first corpus line occasionally contains only a derived
    form (for example "walkers" for the noun "walk"). In that case the next
    example is used instead of emitting a card without a blank.
    """
    for node in _example_nodes(group):
        marked: list[str] = []
        for marked_node in node.find_all("span", "NodeW"):
            text = normalize_example(marked_node.text())
            if text:
                marked.append(text)
        sentence = normalize_example(node.text())
        front = blank_word(sentence, word, [*extra_forms, *marked])
        if front is not None:
            return node, marked, sentence, front
    return None


# ---------------------------------------------------------------------------
# Errors and results
# ---------------------------------------------------------------------------


class LongmanError(Exception):
    """Base class for predictable scraper failures."""


class NotFoundError(LongmanError):
    pass


class PartOfSpeechError(LongmanError):
    pass


class NoExampleError(LongmanError):
    pass


class NetworkError(LongmanError):
    pass


@dataclass
class Card:
    word: str
    front: str
    url: str
    part_of_speech: str = ""
    example: str = ""
    example_url: str = ""
    american_of: str = ""
    matched_headword: str = ""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def word_to_slug(word: str) -> str:
    slug = normalize_headword(word)
    slug = slug.replace("'", "-")
    slug = re.sub(r"[\s\-]+", "-", slug).strip("-")
    return urllib.parse.quote(slug, safe="-")


class LongmanDictionary:
    """Fetch and parse Longman pages with caching and polite rate limiting."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT,
                 delay: float = DEFAULT_DELAY,
                 stop_event: Optional[threading.Event] = None) -> None:
        self.timeout = timeout
        self.delay = delay
        self.stop_event = stop_event
        self._cache: dict[str, tuple[str, str]] = {}
        self._last_request = 0.0
        self._lock = threading.Lock()

    # -- networking -----------------------------------------------------

    def _sleep(self, seconds: float) -> None:
        if self.stop_event is not None:
            self.stop_event.wait(seconds)
        else:
            time.sleep(seconds)

    def _throttle(self) -> None:
        with self._lock:
            wait = self.delay - (time.monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()

    def fetch(self, url: str) -> tuple[str, str]:
        """Fetch a URL and return ``(html, final_url)``."""
        if url in self._cache:
            return self._cache[url]
        if self.stop_event is not None and self.stop_event.is_set():
            raise NetworkError("Stopped by user.")

        self._throttle()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                # urllib does not transparently decompress gzip, so ask for
                # plain text.
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )

        last_error: Optional[Exception] = None
        for attempt in range(3):
            if self.stop_event is not None and self.stop_event.is_set():
                raise NetworkError("Stopped by user.")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    final_url = response.geturl()
                    charset = response.headers.get_content_charset() or "utf-8"
                try:
                    html = raw.decode(charset, errors="replace")
                except LookupError:
                    html = raw.decode("utf-8", errors="replace")
                final_url = final_url.split("#", 1)[0]
                self._cache[url] = (html, final_url)
                if final_url != url:
                    self._cache[final_url] = (html, final_url)
                return html, final_url
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise NotFoundError(
                        f"Longman has no page for “{url}”."
                    ) from exc
                if exc.code in (403, 429):
                    last_error = exc
                else:
                    raise NetworkError(
                        f"Longman returned HTTP {exc.code}."
                    ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc

            if attempt < 2:
                # Small backoff; keep it interruptible.
                self._sleep(1.2 * (attempt + 1))

        raise NetworkError(
            "Could not reach the Longman Dictionary. "
            "Check your internet connection and try again."
        ) from last_error

    # -- parsing --------------------------------------------------------

    @staticmethod
    def _find_example(candidates: list[Entry], word: str,
                      extra_forms: Iterable[str] = ()) -> Optional[
                          tuple[Entry, Node, list[str], str, str]
                      ]:
        """Find the first usable example in the candidate entries."""
        for candidate in candidates:
            for group in candidate.groups:
                found = _first_usable_example(group, word, extra_forms)
                if found is not None:
                    node, marks, sentence, front = found
                    return candidate, node, marks, sentence, front
        return None

    @staticmethod
    def _matched_pos(entry: Entry, requested: Optional[str]) -> str:
        if requested:
            wanted = normalize_pos(requested)
            selected = [pos for pos in entry.pos_list if pos == wanted]
            if selected:
                return ", ".join(selected)
        return entry.pos_display

    def _american_fallback(self, word: str, part_of_speech: Optional[str],
                           american_of: str, card_url: str) -> Card:
        british_url = DICTIONARY_URL + word_to_slug(american_of)
        try:
            html, final_url = self.fetch(british_url)
        except NotFoundError as exc:
            raise NoExampleError(
                f"No example sentence was found for “{word}”, and its British "
                f"spelling “{american_of}” has no Longman page."
            ) from exc
        british_page = parse_entry_page(html, final_url, american_of)
        if not british_page.entries:
            raise NoExampleError(
                f"No example sentence was found for “{word}” or its British "
                f"spelling “{american_of}”."
            )

        candidates = british_page.entries_for(part_of_speech)
        if not candidates:
            raise PartOfSpeechError(
                f"“{word}” has no {part_of_speech} entry, and its British "
                f"spelling “{american_of}” has none either."
            )

        found = self._find_example(candidates, word, [american_of])
        if found is not None:
            entry, _node, _marked, sentence, front = found
            return Card(
                word=word,
                front=front,
                url=card_url,
                part_of_speech=self._matched_pos(entry, part_of_speech),
                example=sentence,
                example_url=british_page.url,
                american_of=american_of,
                matched_headword=entry.headword,
            )

        raise NoExampleError(
            f"No example sentence was found for “{word}” or its British "
            f"spelling “{american_of}”."
        )

    def _parse(self, word: str, part_of_speech: Optional[str],
               url: Optional[str] = None) -> tuple[ParsedPage, Card]:
        url = url or (DICTIONARY_URL + word_to_slug(word))
        html, final_url = self.fetch(url)
        page = parse_entry_page(html, final_url, word)

        if not page.entries:
            raise NotFoundError(
                f"“{word}” was not found in the Longman Dictionary."
            )

        # The note can be on a different homonym entry than the one selected
        # for the requested part of speech, so look at the whole page.
        american_of = next(
            (entry.american_of for entry in page.entries if entry.american_of),
            None,
        )

        candidates = page.entries_for(part_of_speech)
        if not candidates:
            if american_of:
                return page, self._american_fallback(
                    word, part_of_speech, american_of, final_url,
                )
            available = sorted({pos for entry in page.entries for pos in entry.pos_list})
            suffix = f" Available: {', '.join(available)}." if available else ""
            raise PartOfSpeechError(
                f"“{word}” has no {part_of_speech} entry.{suffix}"
            )

        found = self._find_example(
            candidates, word,
            [american_of] if american_of else [],
        )
        if found is None:
            # The "American spelling of ..." note lives on the American page,
            # but examples are often only present on the British page.
            if american_of:
                return page, self._american_fallback(
                    word, part_of_speech, american_of, final_url,
                )
            raise NoExampleError(
                f"No example sentence was found for “{word}”"
                + (f" ({candidates[0].pos_display})" if candidates else "")
                + "."
            )

        entry, _example_node, _marked, sentence, front = found

        return page, Card(
            word=word,
            front=front,
            url=final_url,
            part_of_speech=self._matched_pos(entry, part_of_speech),
            example=sentence,
            example_url=final_url,
            american_of=american_of or "",
            matched_headword=entry.headword,
        )

    def lookup(self, word: str, part_of_speech: Optional[str] = None) -> Card:
        word = word.strip()
        if not word:
            raise ValueError("Word is empty.")
        _, card = self._parse(word, part_of_speech)
        return card
