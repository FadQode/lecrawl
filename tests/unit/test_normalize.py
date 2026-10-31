"""### Tests for shared HTML text normalization."""

from lexcrawl.pipeline.normalize import paragraphs_from_html


def test_nested_paragraphs_in_list_items_are_not_duplicated() -> None:
    """### Preserve list text once when paragraphs are nested inside list items."""
    html = "<ul><li><p>One sentence.</p></li><li><p>Another sentence.</p></li></ul>"

    paragraphs = paragraphs_from_html(html)

    assert paragraphs == ["One sentence.", "Another sentence."]
