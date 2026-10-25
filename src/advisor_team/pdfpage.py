"""Render one PDF page to PNG with PDFium: the app's source preview shows it, and the chart reader sends it to the
model. Pure Python PNG encoding keeps the image free of extra dependencies."""

from __future__ import annotations

import struct
import threading
import zlib
from dataclasses import dataclass

RENDER_SCALE = 2.0  # 144 dpi: sharp in a preview pane on a high-DPI screen, and legible to a vision model
FIGURE_WIDTH = 1600  # pixels across a chart sent to the model: about the resolution charts are exported at
MAX_FIGURE_SCALE = 4.0
MIN_FIGURE_SHARE = 0.08  # an image smaller than this share of the page is a logo or an icon, not a chart
# PDFium is not thread-safe; renders run one at a time.
RENDER_LOCK = threading.Lock()


class UnreadablePdf(ValueError):
    """The bytes are not a PDF that PDFium can open."""


class EmptyPdf(ValueError):
    """The PDF has no pages."""


@dataclass
class RenderedPage:
    png: bytes
    page: int
    page_count: int
    width: int
    height: int
    text: str
    # True when the image is the page's largest picture (a chart) cropped from the page, not the whole page.
    figure: bool = False


def png_rgb(width: int, height: int, stride: int, pixels: bytes) -> bytes:
    """A minimal PNG (8-bit RGB) from a row-padded RGB buffer."""
    rows = b"".join(b"\x00" + pixels[row * stride:row * stride + width * 3] for row in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b"")
    )


def figure_box(pdf_page) -> tuple[float, float, float, float] | None:
    """The bounds (left, bottom, right, top) of the page's largest image, if it is big enough to be a chart."""
    import pypdfium2.raw as pdfium_c

    page_width, page_height = pdf_page.get_size()
    best, best_area = None, MIN_FIGURE_SHARE * page_width * page_height
    for item in pdf_page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=3):
        left, bottom, right, top = item.get_pos()
        left, bottom = max(left, 0.0), max(bottom, 0.0)
        right, top = min(right, page_width), min(top, page_height)
        area = (right - left) * (top - bottom)
        if right > left and top > bottom and area >= best_area:
            best, best_area = (left, bottom, right, top), area
    return best


def render_page(
    content: bytes, page: int | None, *, scale: float = RENDER_SCALE, figure: bool = False,
) -> RenderedPage:
    """One page as a PNG plus its text. Out-of-range pages are clamped to the document. With ``figure``, the image
    is the page's largest picture at about FIGURE_WIDTH pixels across, or the whole page when it has none."""
    import pypdfium2 as pdfium

    with RENDER_LOCK:
        try:
            document = pdfium.PdfDocument(content)
        except pdfium.PdfiumError as error:
            raise UnreadablePdf("The file is not a readable PDF.") from error
        try:
            count = len(document)
            if count < 1:
                raise EmptyPdf("The PDF has no pages.")
            number = min(max(page or 1, 1), count)
            pdf_page = document[number - 1]
            try:
                text_page = pdf_page.get_textpage()
                try:
                    text = text_page.get_text_bounded()
                finally:
                    text_page.close()
                box = figure_box(pdf_page) if figure else None
                crop = (0.0, 0.0, 0.0, 0.0)
                if box is not None:
                    left, bottom, right, top = box
                    page_width, page_height = pdf_page.get_size()
                    crop = (left, bottom, page_width - right, page_height - top)
                    scale = min(MAX_FIGURE_SCALE, max(scale, FIGURE_WIDTH / (right - left)))
                bitmap = pdf_page.render(scale=scale, crop=crop, rev_byteorder=True)
                try:
                    if bitmap.n_channels != 3:
                        raise UnreadablePdf("The PDF page rendered in an unexpected format.")
                    width, height = bitmap.width, bitmap.height
                    image = png_rgb(width, height, bitmap.stride, bytes(bitmap.buffer))
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
        finally:
            document.close()
    return RenderedPage(image, number, count, width, height, text, figure=box is not None)
