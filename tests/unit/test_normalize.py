"""### Unit tests for shared text normalization."""

from __future__ import annotations

from bs4 import BeautifulSoup

from lexcrawl.pipeline.normalize import collapse_whitespace, paragraphs_of, text_of


def test_collapse_whitespace() -> None:
    """### Whitespace runs collapse without dropping words."""
    assert collapse_whitespace("  a\n\tb   c ") == "a b c"


def test_text_of_none() -> None:
    """### A missing node yields an empty string."""
    assert text_of(None) == ""


def test_paragraphs_in_order() -> None:
    """### Blocks are read in order and empty ones dropped."""
    soup = BeautifulSoup("<div><p>First</p><p>  </p><h2>Second</h2></div>", "lxml")
    assert paragraphs_of(soup.div) == ["First", "Second"]


def test_nested_block_not_duplicated() -> None:
    """### Nested block text is not repeated in the parent."""
    soup = BeautifulSoup("<div><li>Outer<p>Inner</p></li></div>", "lxml")
    assert paragraphs_of(soup.div) == ["Outer", "Inner"]
