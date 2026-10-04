"""Converting SAOS judgment HTML into plain text split into operative part and reasoning."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Comment

_BLOCK_TAGS = ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "table", "ol", "ul"]
_WHITESPACE = re.compile(r"\s+")
# Courts write the heading as "UZASADNIENIE", "Uzasadnienie" or spaced out "U z a s a d n i e n i e".
_REASONING_HEADING = re.compile(
    r"^[ \t]*" + r"[ \t]*".join("UZASADNIENIE") + r"[ \t]*:?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def html_to_text(html: str) -> str:
    """Return the judgment text with one line per block element and normalised whitespace."""
    soup = BeautifulSoup(html or "", "lxml")
    for node in soup.find_all(string=True):
        if isinstance(node, Comment):
            node.extract()
        else:
            node.replace_with(_WHITESPACE.sub(" ", node.replace("\xa0", " ")))
    # Anonymised names are often glued to the next word in the source HTML.
    for span in soup.select("span.anon-block"):
        span.insert_after(" ")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    lines = (_WHITESPACE.sub(" ", line).strip() for line in soup.get_text().splitlines())
    return "\n".join(line for line in lines if line)


def split_sections(text: str) -> tuple[str, str]:
    """Split judgment text into (operative part, reasoning). Reasoning is empty if absent."""
    match = _REASONING_HEADING.search(text)
    if not match:
        return text, ""
    return text[: match.start()].strip(), text[match.end() :].strip()
