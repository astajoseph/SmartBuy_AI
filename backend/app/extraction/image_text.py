"""Text printed on product images (pack size, net weight ...) via a small open-source OCR model.

Used by seller discovery only when a shop page's text leaves out a detail the product match needs - e.g. a listing
whose title has no pack size while the photo of the tube says "20 g".

Model: RapidOCR (rapidocr_onnxruntime, Apache-2.0) - PaddleOCR's text detection + recognition models converted to
ONNX, bundled in the wheel (~15 MB), CPU only, about 1-2 s per image. No GPU, no PyTorch, no network at run time.
If the package is missing, reading is skipped and the match simply stays unconfirmed.
"""
from __future__ import annotations

import io
import logging
import re
from functools import lru_cache

log = logging.getLogger(__name__)
MAX_PIXELS = 4000 * 4000


@lru_cache(maxsize=1)
def _engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        log.warning("rapidocr_onnxruntime not installed; product images are not read")
        return None
    return RapidOCR()


def available() -> bool:
    return _engine() is not None


def _fix_ocr_digits(text: str) -> str:
    """OCR reads a zero next to digits as the letter O ('2Og', 'O.5 kg'): put the digit back."""
    return re.sub(r"(?<=\d)[Oo](?=\s*(?:g|gm|gms|ml|kg|l)\b)|(?<![A-Za-z])[Oo](?=[.,]\d)", "0", text)


def read_text(image: bytes) -> str | None:
    """All text found in the image, in reading order, or None (not an image / OCR unavailable)."""
    engine = _engine()
    if engine is None or not image:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image)) as im:      # refuse non-images and decompression bombs before OCR
            if im.width * im.height > MAX_PIXELS:
                return None
            im.verify()
        result, _ = engine(image)
    except Exception:  # noqa: BLE001 - a broken image must not fail discovery
        log.info("product image could not be read", exc_info=True)
        return None
    return _fix_ocr_digits(" ".join(r[1] for r in result or [])) or None
