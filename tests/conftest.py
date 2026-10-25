import os

# The ported tests cover the single-agent (solo) build; team tests set TEAM_MODE themselves.
os.environ.setdefault("TEAM_MODE", "solo")
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.constants import PageLabelStyle
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)


def _pdf_with_image(width_pt: int, height_pt: int) -> bytes:
    """A one-page PDF whose only content is a red picture of the given size, placed at (100, 300)."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    image = DecodedStreamObject()
    image.set_data(bytes([200, 30, 30]) * (40 * 20))
    image.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(40),
        NameObject("/Height"): NumberObject(20),
        NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
        NameObject("/BitsPerComponent"): NumberObject(8),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): writer._add_object(image)}),
    })
    content = DecodedStreamObject()
    content.set_data(f"q {width_pt} 0 0 {height_pt} 100 300 cm /Im1 Do Q".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def pdf_with_image():
    return _pdf_with_image


@pytest.fixture
def pdf_bytes() -> bytes:
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    for text in ("Project introduction", "The renewal deadline is October 30.", "Budget appendix"):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.set_page_label(0, 2, style=PageLabelStyle.LOWERCASE_ROMAN)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
