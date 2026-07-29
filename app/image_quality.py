"""Fast, local capture-quality checks before handwriting OCR."""

from __future__ import annotations

from io import BytesIO
from typing import cast

from PIL import Image, ImageFilter, ImageOps, ImageStat

from .models import ImageQualityReport


def _analysis_image(image: Image.Image) -> Image.Image:
    gray = ImageOps.exif_transpose(image).convert("L")
    if gray.width > 480:
        height = max(1, round(gray.height * 480 / gray.width))
        gray = gray.resize((480, height), Image.Resampling.BILINEAR)
    if gray.height > 720:
        width = max(1, round(gray.width * 720 / gray.height))
        gray = gray.resize((width, 720), Image.Resampling.BILINEAR)
    return gray


def _projection_angle(gray: Image.Image) -> float:
    """Estimate text-line skew by maximizing horizontal ink projection."""
    if gray.width > 320:
        height = max(1, round(gray.height * 320 / gray.width))
        gray = gray.resize((320, height), Image.Resampling.BILINEAR)
    stats = ImageStat.Stat(gray)
    brightness = stats.mean[0]
    contrast = stats.stddev[0]
    threshold = round(min(220, max(65, brightness - max(10, contrast * 0.45))))
    best_angle = 0
    best_score = float("-inf")
    for angle in range(-12, 13, 2):
        rotated = gray.rotate(
            -angle,
            resample=Image.Resampling.BILINEAR,
            expand=False,
            fillcolor=255,
        )
        binary = rotated.point(
            [1 if value < threshold else 0 for value in range(256)]
        )
        flattened = cast(tuple[int, ...], binary.get_flattened_data())
        data = bytes(flattened)
        width, height = binary.size
        rows = [sum(data[offset : offset + width]) for offset in range(0, len(data), width)]
        mean = sum(rows) / max(1, height)
        score = sum((count - mean) ** 2 for count in rows) / max(1, height)
        if score > best_score:
            best_score = score
            best_angle = angle
    return float(best_angle)


def analyze_image_quality(image_bytes: bytes) -> ImageQualityReport:
    with Image.open(BytesIO(image_bytes)) as source:
        original_width, original_height = ImageOps.exif_transpose(source).size
        gray = _analysis_image(source)

    stats = ImageStat.Stat(gray)
    brightness = stats.mean[0]
    contrast = stats.stddev[0]
    edge_map = ImageOps.autocontrast(gray).filter(ImageFilter.FIND_EDGES)
    if edge_map.width > 4 and edge_map.height > 4:
        edge_map = edge_map.crop((2, 2, edge_map.width - 2, edge_map.height - 2))
    sharpness = ImageStat.Stat(edge_map).var[0]
    if contrast >= 18 and brightness >= 60:
        skew = _projection_angle(gray)
        halfway = max(1, gray.height // 2)
        top_skew = _projection_angle(gray.crop((0, 0, gray.width, halfway)))
        bottom_skew = _projection_angle(
            gray.crop((0, halfway, gray.width, gray.height))
        )
        perspective_delta = abs(top_skew - bottom_skew)
    else:
        skew = 0.0
        perspective_delta = 0.0
    shortest_edge = min(original_width, original_height)

    issues: list[str] = []
    guidance: list[str] = []
    severe = False
    deductions = 0.0

    if shortest_edge < 450:
        severe = True
        deductions += 0.45
        issues.append("resolution")
        guidance.append("Move closer so the shortest image edge is at least 600 px.")
    elif shortest_edge < 700:
        deductions += 0.18
        issues.append("resolution")
        guidance.append("Move a little closer so every handwritten line is larger.")

    if brightness < 45:
        severe = True
        deductions += 0.4
        issues.append("darkness")
        guidance.append("Increase the light and avoid casting a shadow over the page.")
    elif brightness < 90:
        deductions += 0.18
        issues.append("darkness")
        guidance.append("Use brighter, even light before taking the photo.")
    elif brightness > 248 and contrast < 12:
        severe = True
        deductions += 0.35
        issues.append("overexposure")
        guidance.append("Reduce glare and make sure the writing is visible on the page.")

    if contrast < 9:
        severe = True
        deductions += 0.4
        issues.append("contrast")
        guidance.append("Use darker writing or stronger, more even lighting.")
    elif contrast < 18:
        deductions += 0.18
        issues.append("contrast")
        guidance.append("Increase contrast between the writing and the paper.")

    if sharpness < 140:
        severe = True
        deductions += 0.35
        issues.append("blur")
        guidance.append("Hold the phone steady and tap the writing to focus.")
    elif sharpness < 320:
        deductions += 0.16
        issues.append("blur")
        guidance.append("Hold the phone steady and retake a sharper photo.")

    if abs(skew) >= 8:
        deductions += 0.2
        issues.append("tilt")
        guidance.append("Rotate the phone until the notebook lines look horizontal.")
    elif abs(skew) >= 5:
        deductions += 0.1
        issues.append("tilt")
        guidance.append("Straighten the page slightly before taking the photo.")

    if perspective_delta >= 8:
        deductions += 0.22
        issues.append("perspective")
        guidance.append("Hold the phone parallel to the page, not at an angle.")
    elif perspective_delta >= 5:
        deductions += 0.1
        issues.append("perspective")
        guidance.append("Move the phone closer to parallel with the page.")

    score = max(0.0, min(1.0, 1.0 - deductions))
    status = "retake" if severe or score < 0.45 else "usable" if issues else "good"
    return ImageQualityReport(
        status=status,
        score=round(score, 2),
        metrics={
            "width": float(original_width),
            "height": float(original_height),
            "shortest_edge": float(shortest_edge),
            "brightness": round(brightness, 1),
            "contrast": round(contrast, 1),
            "sharpness": round(sharpness, 1),
            "skew_degrees": skew,
            "perspective_delta_degrees": perspective_delta,
        },
        issues=list(dict.fromkeys(issues)),
        guidance=list(dict.fromkeys(guidance)),
    )
