from __future__ import annotations

from pathlib import Path

import pytest

from ontyra_ingest import identity
from ontyra_ingest.chunking import chunk
from ontyra_ingest.config import Settings
from ontyra_ingest.grounding import find_quote
from ontyra_ingest.llm import LlmError, parse_json
from ontyra_ingest.models import Block, ParsedDocument
from ontyra_ingest.parsers import ParserError, choose
from ontyra_ingest.parsers.builtin import BuiltinParser
from ontyra_ingest.parsers.mineru import blocks_from, content_list


def test_quote_is_found_across_whitespace_and_returned_literally() -> None:
    text = "Alpha beta\n  gamma delta. Epsilon."
    assert find_quote("beta gamma   delta.", text) == "beta gamma delta."


@pytest.mark.parametrize("quote", ["not here at all", "", "short"])
def test_quote_must_occur_and_be_long_enough(quote: str) -> None:
    assert find_quote(quote, "short text that is here, but not the quote.") is None


def test_quote_too_long_is_refused() -> None:
    long = "word " * 200
    assert find_quote(long, long) is None


def test_chunks_follow_sections_and_carry_the_heading_chain() -> None:
    blocks = [
        Block(text="Intro", level=1),
        Block(text="First paragraph of the intro."),
        Block(text="Methods", level=1),
        Block(text="Sub", level=2),
        Block(text="Text under the subsection.", page=3),
    ]
    chunks = chunk(blocks, max_chars=500, overlap=0)
    assert [c.section for c in chunks] == [["Intro"], ["Methods", "Sub"]]
    assert chunks[1].page == 3 and "p. 3" in chunks[1].locator


def test_long_text_is_split_with_overlap() -> None:
    sentence = "This is a sentence of some length. "
    blocks = [Block(text=sentence * 40)]
    chunks = chunk(blocks, max_chars=300, overlap=60)
    assert len(chunks) > 3
    assert all(len(c.text) <= 300 + 60 + 2 for c in chunks)
    assert chunks[1].text.startswith(chunks[0].text[-60:].lstrip()[:20])


def test_parse_json_tolerates_fences_and_reasoning() -> None:
    assert parse_json('<think>hmm</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": [1]} done') == {"a": [1]}
    with pytest.raises(LlmError):
        parse_json("no json")


def test_mineru_envelopes_and_blocks() -> None:
    items = [
        {"type": "text", "text": "Title", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "Body.", "page_idx": 1},
        {"type": "list", "list_items": ["one", "two"], "page_idx": 1},
        {"type": "table", "table_caption": ["Table 1."], "page_idx": 2},
        {"type": "image"},
    ]
    assert content_list({"results": {"x": {"content_list": items}}}) == items
    assert content_list({"content_list": '[{"type": "text"}]'}) == [{"type": "text"}]
    blocks = blocks_from(items)
    assert [(b.text, b.level, b.page) for b in blocks] == [
        ("Title", 1, 1), ("Body.", 0, 2), ("one", 0, 2), ("two", 0, 2), ("Table 1.", 0, 3),
    ]  # fmt: skip


async def test_builtin_reads_markdown_and_html(tmp_path: Path) -> None:
    md = tmp_path / "a.md"
    md.write_text("# Title\n\nFirst line\ncontinues here.\n\n## Part\n\nSecond.\n")
    doc = await BuiltinParser().parse(md)
    assert doc.title == "Title"
    assert [(b.text, b.level) for b in doc.blocks] == [
        ("Title", 1), ("First line continues here.", 0), ("Part", 2), ("Second.", 0),
    ]  # fmt: skip
    page = tmp_path / "a.html"
    page.write_text(
        "<html><head><title>T</title><style>x{}</style></head><body>"
        "<h1>Head</h1><p>Some <b>bold</b> text.</p><script>no()</script></body></html>"
    )
    html = await BuiltinParser().parse(page)
    assert html.title == "T"
    assert [(b.text, b.level) for b in html.blocks] == [("Head", 1), ("Some bold text.", 0)]


def test_parser_choice(tmp_path: Path) -> None:
    pdf, txt = tmp_path / "a.pdf", tmp_path / "a.txt"
    assert choose(Settings(), pdf).name == "builtin"
    assert choose(Settings(mineru_url="http://m"), pdf).name == "mineru"
    assert choose(Settings(mineru_url="http://m"), txt).name == "builtin"
    with pytest.raises(ParserError):
        choose(Settings(parser="mineru"), pdf)
    with pytest.raises(ParserError):
        choose(Settings(parser="nope"), pdf)
    with pytest.raises(ParserError):
        choose(Settings(parser="mineru", mineru_url="http://m"), txt)


def test_identity_prefers_doi_then_arxiv() -> None:
    doc = ParsedDocument(
        blocks=[Block(text="Paper. https://doi.org/10.1234/Abc.5. More arXiv:2401.01234v2")]
    )
    ids = identity.basic(doc)
    assert ids == {"doi": "10.1234/Abc.5", "arxiv": "2401.01234"}
    assert identity.uri_for(ids, "file:///x") == "doi:10.1234/abc.5"
    assert identity.uri_for({}, "file:///x") == "file:///x"


def test_mineru_table_rows_are_self_describing() -> None:
    html = (
        "<table><tr><td>Name</td><td>No.</td><td>Function</td></tr>"
        "<tr><td>GND</td><td>1</td><td>Ground</td></tr>"
        "<tr><td>EN</td><td>3</td><td></td></tr></table>"
    )
    blocks = blocks_from([{"type": "table", "table_body": html, "page_idx": 4}])
    assert [b.text for b in blocks] == [
        "Name: GND; No.: 1; Function: Ground",
        "Name: EN; No.: 3",
    ]
    assert blocks[0].page == 5


def test_parse_json_repairs_stray_backslashes() -> None:
    assert parse_json(r'{"a": "VDD\_SPI and \alpha", "b": "tab\there"}') == {
        "a": r"VDD\_SPI and \alpha",
        "b": "tab\there",
    }


def test_quote_tolerates_spacing_and_case_and_returns_the_documents_words() -> None:
    text = "The module   draws 240 µA in\nLight-sleep mode."
    assert find_quote("the module draws 240 µa in light-sleep mode.", text) == (
        "The module draws 240 µA in Light-sleep mode."
    )
    assert find_quote("the module draws 250 µA in light-sleep mode.", text) is None


def test_mineru_formulas_become_plain_text() -> None:
    from ontyra_ingest.parsers.mineru import plain_math

    assert plain_math(r"Min: $0 . 7 5 \times \mathsf { V D D } ^ { 1 }$") == "Min: 0.75×VDD^1"
    assert plain_math(r"$\mathsf { V } _ { I H }$") == "V_IH"
    assert plain_math("no formula") == "no formula"


def test_label_headings_do_not_open_sections() -> None:
    items = [
        {"type": "text", "text": "4.1 Limits", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "Note:", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "Mind the limit.", "page_idx": 0},
    ]
    assert [(b.text, b.level) for b in blocks_from(items)] == [
        ("4.1 Limits", 1), ("Note:", 0), ("Mind the limit.", 0),
    ]  # fmt: skip
