import base64
from io import BytesIO

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from advisor_team.evidence import Blob, Source, find_evidence

SOURCE = Source(
    kind="onedrive", title="Plan.pdf", resource_path="/me/drive/items/123",
    web_url="https://example.sharepoint.com/Plan.pdf", folder="/drive/root:/Planning",
)


def test_pages_quotes_labels_and_conversion(pdf_bytes: bytes) -> None:
    result = find_evidence(pdf_bytes, SOURCE, "RENEWAL   deadline", converted=True)
    assert result["page_count"] == 3
    assert result["pagination"] == "converted_pdf"
    assert result["matches"] == [{
        "page_number": 2, "page_label": "ii",
        "quote": "The renewal deadline is October 30.", "matched_text": "renewal deadline",
    }]
    assert result["source"]["folder"] == "/drive/root:/Planning"
    assert len(result["sha256"]) == 64
    assert result["limitation"] is None


def test_no_match_is_not_evidence(pdf_bytes: bytes) -> None:
    assert find_evidence(pdf_bytes, SOURCE, "invented statement")["matches"] == []


def pdf_with(*pages: str) -> bytes:
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_a_phrase_that_lost_its_punctuation_still_finds_its_page() -> None:
    # Work IQ's passage quoted the caption without its colon, percent sign and hyphen.
    caption = "Figure 2: NVDA weight against the 25% single-stock limit"
    data = pdf_with("Portfolio overview", "Holdings", caption)
    result = find_evidence(data, SOURCE, "Figure 2 NVDA weight against the 25 single stock limit")
    assert [match["page_number"] for match in result["matches"]] == [3]
    assert result["matches"][0]["matched_text"] == caption
    # Still whole words, in order: another number is another caption.
    assert find_evidence(data, SOURCE, "Figure 2 NVDA weight against the 2 single stock limit")["matches"] == []


def test_a_decimal_point_is_never_dropped_to_match_another_number() -> None:
    # "2.5%" is two words, 2 and 5: they need a separator between them, so they never match "25%".
    data = pdf_with("Figure 2: NVDA weight against the 25% single-stock limit", "Cash need of $15 million")
    assert find_evidence(data, SOURCE, "NVDA weight against the 2.5% single-stock limit")["matches"] == []
    assert find_evidence(data, SOURCE, "Cash need of $1.5 million")["matches"] == []
    assert [m["page_number"] for m in find_evidence(data, SOURCE, "Cash need of 15 million")["matches"]] == [2]


def test_scanned_and_encrypted_pdf() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    result = find_evidence(output.getvalue(), SOURCE, "deadline")
    assert result["matches"] == []
    assert "OCR" in result["limitation"]
    writer.encrypt("password")
    output = BytesIO()
    writer.write(output)
    with pytest.raises(ValueError, match="Encrypted"):
        find_evidence(output.getvalue(), SOURCE, "deadline")


@pytest.mark.parametrize("override", [
    {"statusCode": 403}, {"contentType": "application/octet-stream"},
    {"base64Content": "not-base64"}, {"sizeBytes": 1},
])
def test_blob_rejects_invalid_payload(pdf_bytes: bytes, override: dict) -> None:
    values = {
        "statusCode": 200, "contentType": "application/pdf", "sizeBytes": len(pdf_bytes),
        "base64Content": base64.b64encode(pdf_bytes).decode(),
    } | override
    with pytest.raises(ValueError):
        Blob.model_validate(values).pdf_bytes()


def test_source_rejects_unsafe_links() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        Source(kind="onedrive", title="Plan.pdf", resource_path="/me/drive/items/1",
               web_url="https://user:password@example.com/file")
