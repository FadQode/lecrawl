"""### Unit tests for shared text normalization."""

from __future__ import annotations

from bs4 import BeautifulSoup

from lexcrawl.pipeline.normalize import collapse_whitespace, paragraphs_of, text_of


def test_collapse_whitespace_keeps_words() -> None:
    """### Whitespace runs collapse without dropping words or Unicode."""
    assert collapse_whitespace("  a\n\tb   c ") == "a b c"


def test_text_of_returns_empty_for_none() -> None:
    """### A missing node yields an empty string."""
    assert text_of(None) == ""


def test_paragraphs_of_reads_blocks_in_order() -> None:
    """### Paragraph extraction keeps document order and drops empty blocks."""
    soup = BeautifulSoup("<div><p>First</p><p>  </p><h2>Second</h2></div>", "lxml")
    assert paragraphs_of(soup.div) == ["First", "Second"]


def test_paragraphs_of_does_not_duplicate_nested_blocks() -> None:
    """### A nested block is not repeated inside its parent's text."""
    soup = BeautifulSoup("<div><li>Outer<p>Inner</p></li></div>", "lxml")
    assert paragraphs_of(soup.div) == ["Outer", "Inner"]
