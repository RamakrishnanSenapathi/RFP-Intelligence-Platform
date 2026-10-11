from ingestion.chunker import chunk_document, is_heading
from ingestion.models import ParsedDocument, ParsedPage
from ingestion.parser import (detect_doc_type, find_date, normalize_text, parse_file, remove_repeated_lines,
                              rows_to_markdown)

HTML = """<html><head><title>Student and Staff Computing Devices - BidNet</title></head><body>
<nav>menu junk</nav><h1>Bid Information</h1>
<table><tr><th>Field</th><th>Value</th></tr>
<tr><td>Bid Number</td><td>JA-207652</td></tr><tr><td>Due Date</td><td>03/14/2025 2:00 PM EST</td></tr></table>
<p>Contact <b>Jane Doe</b> at jane@example.org</p><script>var x=1;</script></body></html>"""


def test_doc_type_detection():
    assert detect_doc_type("Addendum 2 - Student Devices.pdf") == ("addendum", 2)
    assert detect_doc_type("Addendum_No_3.pdf") == ("addendum", 3)
    assert detect_doc_type("Bid page BidNet Direct.html") == ("bid_page", None)
    assert detect_doc_type("Mercury Affidavit.pdf")[0] == "affidavit"
    assert detect_doc_type("Dell Laptop Specs.pdf")[0] == "specs"
    assert detect_doc_type("PORFP - Dell Laptop Final.pdf")[0] == "rfp"
    # content fallback for oddly named files
    assert detect_doc_type("scan0001.pdf", "ADDENDUM NO. 4 to the RFP")[0] == "addendum"


def test_find_date():
    assert find_date("Issued March 5, 2025 by the district") == "2025-03-05"
    assert find_date("Due 2025-04-01") == "2025-04-01"
    assert find_date("no dates here") is None


def test_normalize_hyphenation_and_joins():
    out = normalize_text("The vendor shall pro-\nvide all items\nin accordance with the terms.")
    assert "provide" in out
    assert "items in accordance" in out
    assert normalize_text("| a | b |\n| 1 | 2 |").count("\n") == 1  # tables untouched


def test_remove_repeated_headers_footers():
    pages = [f"ACME DISTRICT RFP\n" + "\n".join(f"Line {j} of page {i} with unique content." for j in range(8)) + f"\nPage {i} of 4"
             for i in range(1, 5)]
    cleaned = remove_repeated_lines(pages)
    assert all("ACME DISTRICT RFP" not in p and "Page" not in p for p in cleaned)
    assert all("unique content" in p for p in cleaned)


def test_rows_to_markdown():
    md = rows_to_markdown([["Item", "Qty"], ["Laptop", "10"], [None, None]])
    assert md.splitlines()[0] == "| Item | Qty |" and md.splitlines()[1] == "| --- | --- |" and "Laptop" in md


def test_parse_html_tables_and_cleaning(tmp_path):
    p = tmp_path / "Bid page - BidNet Direct.html"
    p.write_text(HTML, encoding="utf-8")
    doc = parse_file(p, "BidX")
    text = doc.pages[0].text
    assert doc.doc_type == "bid_page"
    assert "| Bid Number | JA-207652 |" in text
    assert "menu junk" not in text and "var x" not in text
    assert "Jane Doe at jane@example.org" in text


def test_parse_pdf_with_table_and_unreadable(tmp_path):
    import fitz
    pdf = tmp_path / "Addendum 1.pdf"
    d = fitz.open()
    page = d.new_page()
    page.insert_text((72, 72), "ADDENDUM NO. 1\nThe due date is extended to April 10, 2025 at 2:00 PM EST.")
    d.save(pdf)
    doc = parse_file(pdf, "B")
    assert doc.doc_type == "addendum" and doc.addendum_number == 1
    assert "extended" in doc.pages[0].text
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    res = parse_file(bad, "B")  # must not raise
    assert res.pages == [] and res.warnings


def test_chunker_headings_tables_and_pages():
    para = "The contractor shall deliver all devices within thirty days of award. " * 30
    text = ("1. SCOPE OF WORK\n\n" + para + "\n\n| Item | Qty |\n| --- | --- |\n| Laptop | 10 |\n\n"
            "2. SUBMISSION\n\nSubmit bids through the portal.")
    doc = ParsedDocument("B", "rfp.pdf", "x", "rfp", None, None, [ParsedPage(1, text), ParsedPage(2, "More text on page two. " * 5)])
    chunks = chunk_document(doc, max_chars=800, overlap=100, min_chars=100)
    assert all(len(c.text) < 1100 for c in chunks)
    assert any(c.is_table and "Laptop" in c.text for c in chunks)
    assert {c.page_number for c in chunks} == {1, 2}
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert any(c.section.startswith("2. SUBMISSION") for c in chunks)
    assert is_heading("TERMS AND CONDITIONS") and not is_heading("The vendor shall deliver.")
