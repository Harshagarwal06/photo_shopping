"""Real on-device OCR, not a stub.

These run the Swift Vision script against committed photographs, so they need
macOS and its `swift` driver. Everything else about handwriting recognition is
untestable without a real image: both defects guarded here were invisible to a
stubbed `recognize_text`.
"""

import shutil
import sys
from pathlib import Path

import pytest

from app.local_vision import recognize_text

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = ["Water Bottle", "Coke Can", "black pen", "Coffee"]

requires_vision = pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("swift") is None,
    reason="macOS Vision OCR needs Darwin and the Swift driver.",
)


def recognized_lines(name: str) -> list[str]:
    image = (FIXTURES / name).read_bytes()
    text = recognize_text(image, "image/jpeg")
    return [line.strip() for line in text.splitlines() if line.strip()]


@requires_vision
def test_upscaling_separates_touching_handwritten_strokes():
    """At native resolution the "bl" of "black" is recognised as a single "H"."""
    lines = recognized_lines("handwritten_list.jpeg")

    assert lines == EXPECTED
    assert "Hack pen" not in lines


@requires_vision
def test_exif_rotated_photo_reads_upright_and_in_order():
    """The same list stored rotated 90° CCW with EXIF orientation 6.

    Order matters as much as content: applying the orientation to the request
    instead of to the pixels leaves Vision's bounding boxes in the stored
    coordinate space, which silently reverses the recognised lines.
    """
    assert recognized_lines("handwritten_list_rotated.jpeg") == EXPECTED
