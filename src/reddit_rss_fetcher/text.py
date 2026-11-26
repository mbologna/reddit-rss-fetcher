"""Plain text out of a post's or comment's HTML, cut to an excerpt.

The same rules as feed-refinery's text.py (mbologna/feed-refinery), so a
Reddit item reads like the items of every other feed: change both together.
"""

import re
from html import escape
from html.parser import HTMLParser

WHOLE = 0.9  # of a text's words in an excerpt that is all of it, but captions and bylines
PARAGRAPH = "\n\n"  # between an excerpt's paragraphs
BLANK_LINE = re.compile(r"\n\s*\n")
# A sentence's last character, with any closing quotes or brackets, before a space.
SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*(?=\s)")
# Tags that start or end a paragraph: their text doesn't run into the next.
BLOCKS = frozenset(
    "address article aside blockquote br dd div dl dt figcaption figure footer h1 h2 h3 h4 h5 h6 header hr li main"
    " ol p pre section table tr ul".split()
)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in BLOCKS:
            self.parts.append(PARAGRAPH)

    def handle_endtag(self, tag: str) -> None:
        if tag in BLOCKS:
            self.parts.append(PARAGRAPH)


def plain_paragraphs(html: str) -> str:
    """The text of an HTML snippet, its paragraphs apart by a blank line and whitespace within each collapsed."""
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    return _paragraphs("".join(parser.parts))


def squash(text: str) -> str:
    return " ".join(text.split())


def _paragraphs(text: str) -> str:
    return PARAGRAPH.join(p for p in map(squash, BLANK_LINE.split(text)) if p)


def holds_all(shown: str, words: int) -> bool:
    """Whether shown, an excerpt, is all of a text of that many words."""
    return len(shown.split()) >= WHOLE * words


def excerpt(text: str, limit: int) -> str:
    """text cut to at most limit characters. Paragraphs apart by a blank line stay apart; other whitespace collapses.

    A cut text ends with its last whole paragraph, else sentence, when one ends
    in the second half of the limit; otherwise at a word, with an ellipsis.
    """
    text = _paragraphs(text)
    if len(text) <= limit:
        return text
    end = text.rfind(PARAGRAPH, 0, limit + len(PARAGRAPH))
    if end < limit // 2:
        end = max((m.end() for m in SENTENCE_END.finditer(text, 0, limit + 1)), default=-1)
    if end >= limit // 2:
        return text[:end]
    cut = text[: limit - 1]
    if re.search(r"\s", cut):
        cut = re.sub(r"\s+\S*$", "", cut)
    return cut.rstrip(" ,;:.-\n") + "…"


def paragraphs(text: str, lead: str = "", tail: str = "") -> str:
    """An excerpt as escaped <p> elements, one per paragraph; lead and tail are HTML for the first's start and the last's end."""
    parts = [escape(p) for p in text.split(PARAGRAPH)]
    parts[0] = lead + parts[0]
    parts[-1] += tail
    return "\n".join(f"<p>{p}</p>" for p in parts)
