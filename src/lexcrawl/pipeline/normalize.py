"""### Shared text normalization used by every source actor."""

from __future__ import annotations

from bs4 import BeautifulSoup
from bs4.element import Tag

# $ Tags that normally separate distinct blocks of source text.$
_BLOCK_TAGS = ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "td", "th")


def collapse_whitespace(value: str) -> str:
    """### Collapse whitespace runs without dropping any words."""
    return " ".join(value.split())


def text_of(node: Tag | None) -> str:
    """### Return collapsed text for an optional node."""
    if node is None:
        return ""
    return collapse_whitespace(node.get_text(" ", strip=True))


def paragraphs_of(container: Tag | None) -> list[str]:
    """### Return non-empty paragraph blocks in their original order."""
    if container is None:
        return []
    blocks: list[Tag] = list(container.find_all(_BLOCK_TAGS))
    if not blocks:
        blocks = [container]
    return [text for block in blocks if (text := _block_text(block))]


def _block_text(block: Tag) -> str:
    """### Return a block's own text without repeating nested block contents."""
    clone = BeautifulSoup(str(block), "lxml")
    copied_block = clone.find(block.name)
    if not isinstance(copied_block, Tag):
        return text_of(block)
    for nested_block in copied_block.find_all(_BLOCK_TAGS):
        nested_block.decompose()
    return text_of(copied_block)


def paragraphs_from_html(fragment: str | bytes | None) -> list[str]:
    """### Extract paragraph text from an HTML fragment or document."""
    if not fragment:
        return []
    soup = BeautifulSoup(fragment, "lxml")
    body = soup.body or soup
    return paragraphs_of(body)
