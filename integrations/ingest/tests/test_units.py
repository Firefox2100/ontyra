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
from ontyra_ingest.parsers.mineru import blocks_from


def test_a_short_quote_is_widened_to_its_line() -> None:
    text = "- GPIO, SPI, LCD interface,\n\nUART, I2C, I2S, remote control,\n\nLED PWM, USB OTG"
    assert find_quote("I2C,", text) == "UART, I2C, I2S, remote control,"
    assert find_quote("nowhere,", text) is None


def test_quote_is_found_across_whitespace_and_returned_literally() -> None:
    text = "Alpha beta\n  gamma delta. Epsilon."
    assert find_quote("beta gamma   delta.", text) == "beta gamma delta."


@pytest.mark.parametrize("quote", ["not here at all", ""])
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


def test_mineru_structured_content_becomes_blocks() -> None:
    pages = [
        {"page_idx": 0, "blocks": [
            {"type": "doc_title", "content": "Datasheet"},
            {"type": "page_number", "content": "1"},
            {"type": "header", "content": "Running head"},
        ]},
        {"page_idx": 1, "blocks": [
            {"type": "paragraph_title", "content": "4 Electrical"},
            {"type": "paragraph_title", "content": "4.5.3 Wi-Fi RX"},
            {"type": "paragraph_title", "content": "Note:"},
            {"type": "text", "content": "Body."},
            {"type": "index", "content": "- 1 Intro 2"},
            {"type": "image", "content": "", "captions": [{"content": "Figure 3: Layout"}]},
            {"type": "table", "content": "", "captions": [{"content": "Table 1."}]},
        ]},
    ]  # fmt: skip
    blocks = blocks_from(pages)
    assert [(b.text, b.level, b.page) for b in blocks] == [
        ("Datasheet", 1, 1), ("4 Electrical", 2, 2), ("4.5.3 Wi-Fi RX", 4, 2), ("Note:", 0, 2),
        ("Body.", 0, 2), ("Figure 3: Layout", 0, 2), ("Table 1.", 0, 2),
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
    blocks = blocks_from([{"page_idx": 4, "blocks": [{"type": "table", "content": html}]}])
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


def test_a_spanned_cell_is_repeated_under_every_row_it_covers() -> None:
    from ontyra_ingest.parsers.mineru import table_lines

    html = (
        "<table><tr><td>Mode</td><td>MHz</td><td>mA</td></tr>"
        "<tr><td rowspan=2>Sleep</td><td>40</td><td>13</td></tr>"
        "<tr><td>80</td><td>22</td></tr>"
        "<tr><td>Idle</td><td colspan=2>0</td></tr></table>"
    )
    assert table_lines(html) == [
        "Mode: Sleep; MHz: 40; mA: 13",
        "Mode: Sleep; MHz: 80; mA: 22",
        "Mode: Idle; MHz: 0; mA: 0",
    ]


def test_mineru_formulas_become_plain_text() -> None:
    from ontyra_ingest.parsers.mineru import plain_math

    assert plain_math(r"Min: $0 . 7 5 \times \mathsf { V D D } ^ { 1 }$") == "Min: 0.75×VDD^1"
    assert plain_math(r"$\mathsf { V } _ { I H }$") == "V_IH"
    assert plain_math("no formula") == "no formula"


def test_label_headings_do_not_open_sections() -> None:
    page = {"page_idx": 0, "blocks": [
        {"type": "paragraph_title", "content": "4.1 Limits"},
        {"type": "paragraph_title", "content": "Note:"},
        {"type": "text", "content": "Mind the limit."},
    ]}  # fmt: skip
    assert [(b.text, b.level) for b in blocks_from([page])] == [
        ("4.1 Limits", 3), ("Note:", 0), ("Mind the limit.", 0),
    ]  # fmt: skip


def test_a_markdown_table_becomes_one_line_per_row() -> None:
    from ontyra_ingest.parsers.mineru import table_lines

    md = (
        "| Symbol | Parameter | Min | Max | Unit |\n| --- | --- | --- | --- | --- |\n"
        "| VDD33 | Power supply voltage | -0.3 | 3.6 | V |\n"
        "| T<sub>STORE</sub> | Storage temperature | -40 | 105 | \u00b0C |"
    )
    assert table_lines(md) == [
        "Symbol: VDD33; Parameter: Power supply voltage; Min: -0.3; Max: 3.6; Unit: V",
        "Symbol: TSTORE; Parameter: Storage temperature; Min: -40; Max: 105; Unit: \u00b0C",
    ]
    assert table_lines("| Boot | GPIO0 |\n| --- | --- |\n| Download Boot<sup>1</sup> | 0 |") == [
        "Boot: Download Boot^1; GPIO0: 0"
    ]


def test_a_chunks_saved_result_follows_its_text_not_its_position() -> None:
    from ontyra_ingest.models import Chunk
    from ontyra_ingest.pipeline import chunk_key
    from ontyra_ingest.profiles import all_profiles

    profile = all_profiles()["claims"]
    a = Chunk(index=3, text="Flash up to 16 MB", section=["Overview"], page=1)
    moved = a.model_copy(update={"index": 4})  # new content ahead of it shifted the index
    edited = a.model_copy(update={"text": "Flash up to 16 MB, PSRAM up to 8 MB"})
    assert chunk_key(profile, a) == chunk_key(profile, moved)
    assert chunk_key(profile, a) != chunk_key(profile, edited)


def test_a_parser_fix_does_not_reuse_results_saved_with_the_old_reading() -> None:
    from ontyra_ingest.parsers.mineru import MinerUParser

    assert MinerUParser("http://x", "basic", 1).version != MinerUParser("http://x", None, 1).version
    assert MinerUParser("http://x", "basic", 1).version.startswith(MinerUParser.reading + ":")
