"""### Tests for shared HTML text normalization."""

from lexcrawl.pipeline.normalize import paragraphs_from_html


def test_nested_paragraphs_in_list_items_are_not_duplicated() -> None:
    """### Preserve list text once when paragraphs are nested inside list items."""
    html = "<ul><li><p>One sentence.</p></li><li><p>Another sentence.</p></li></ul>"

    paragraphs = paragraphs_from_html(html)

    assert paragraphs == ["One sentence.", "Another sentence."]


def test_html_normalization_decodes_entities_and_ignores_non_content_tags() -> None:
    """### Body extraction keeps visible list text and drops executable or hidden markup."""
    html = "<main><ul><li>A &amp; B</li><li>Second</li></ul>"
    html += "<script>secret()</script><style>.x{}</style><noscript>fallback</noscript></main>"
    assert paragraphs_from_html(html) == ["A & B", "Second"]


def test_html_normalization_of_empty_markup_returns_no_paragraphs() -> None:
    """### Empty tags do not become artificial document text."""
    assert paragraphs_from_html("<div><p></p><script>code</script></div>") == []
