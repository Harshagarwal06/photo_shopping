from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen

import pytest
from PIL import Image
from playwright.sync_api import expect, sync_playwright

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("RUN_BROWSER_TESTS") != "1",
        reason="Set RUN_BROWSER_TESTS=1 after installing Chromium.",
    ),
]

ROOT = Path(__file__).resolve().parent.parent


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="module")
def live_demo(tmp_path_factory):
    port = available_port()
    state_dir = tmp_path_factory.mktemp("browser-state")
    environment = {
        **os.environ,
        "DEMO_MODE": "true",
        "MODEL_BACKEND": "local",
        "RECOGNITION_POLICY": "review",
        "GROQ_API_KEY": "",
        "HF_TOKEN": "",
        "NVIDIA_API_KEY": "",
        "STATE_DB_PATH": str(state_dir / "state.sqlite3"),
        "SWIGGY_REDIRECT_URI": (
            f"http://localhost:{port}/api/providers/instamart/callback"
        ),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/api/health", timeout=0.5) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.1)
    else:
        output = process.stdout.read() if process.stdout else ""
        process.terminate()
        raise RuntimeError(f"Demo server did not start:\n{output}")

    yield base_url
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_demo_uses_the_submitted_list_and_stays_responsive(live_demo):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 375, "height": 812})
        external_font_requests = []
        page.on(
            "request",
            lambda request: external_font_requests.append(request.url)
            if "fonts.google" in request.url
            else None,
        )
        page.goto(live_demo)
        page.get_by_label("Type your list (optional)").fill(
            "cheapest coffee 2 packs under ₹500"
        )
        page.get_by_role("button", name="Preview product matches").click()

        expect(
            page.get_by_role("heading", name="Check what the photo says")
        ).to_be_visible()
        page.get_by_role("button", name="Search Blinkit for these items").click()
        expect(
            page.get_by_role("heading", name="Preview the Blinkit matches")
        ).to_be_visible()
        review = page.locator("#review")
        expect(review).to_contain_text("coffee")
        expect(review).not_to_contain_text("dishwashing liquid")
        for width in (320, 375, 414, 768):
            page.set_viewport_size({"width": width, "height": 812})
            assert page.evaluate(
                "() => document.documentElement.scrollWidth "
                "<= document.documentElement.clientWidth"
            )
        page.set_viewport_size({"width": 375, "height": 812})
        assert (
            page.locator(".nav-slab").evaluate(
                "(element) => element.getBoundingClientRect().height"
            )
            <= 80
        )
        assert external_font_requests == []
        browser.close()


def test_photo_picker_surfaces_retake_guidance_before_submission(live_demo):
    image = Image.new("RGB", (100, 100), "black")
    payload = BytesIO()
    image.save(payload, format="PNG")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 375, "height": 812})
        page.goto(live_demo)
        page.locator("#request-image").set_input_files(
            {
                "name": "unreadable.png",
                "mimeType": "image/png",
                "buffer": payload.getvalue(),
            }
        )

        quality = page.locator("#photo-quality")
        expect(quality).to_have_attribute("data-status", "retake")
        expect(quality).to_contain_text("Retake recommended")
        browser.close()


def test_demo_compares_all_three_apps_without_live_connections(live_demo):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(live_demo)
        page.get_by_label("Type your list (optional)").fill(
            "2 L Amul milk\n12 eggs\n500 g basmati rice"
        )
        page.get_by_role("button", name="Compare prices").click()

        expect(
            page.get_by_role("heading", name="Check what the photo says")
        ).to_be_visible()
        page.get_by_role("button", name="Compare these items").click()
        expect(
            page.get_by_role(
                "heading", name="Confirm what every cart must satisfy"
            )
        ).to_be_visible()
        page.get_by_role("button", name="Confirm contract and compare").click()

        comparison = page.locator("#comparison")
        expect(comparison).to_be_visible(timeout=15_000)
        expect(comparison).to_contain_text("Blinkit")
        expect(comparison).to_contain_text("Swiggy Instamart")
        expect(comparison).to_contain_text("Zepto")
        expect(comparison).to_contain_text("No cart was changed")
        for width in (320, 375, 768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate(
                "() => document.documentElement.scrollWidth "
                "<= document.documentElement.clientWidth"
            )
        page.get_by_role(
            "button", name="Check exact-total requirements"
        ).click()
        expect(page.locator("#toast-stack")).to_contain_text(
            "cart reading is not ready"
        )
        browser.close()


def test_photo_review_can_add_an_ocr_line_that_was_missed(live_demo):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(live_demo)
        page.locator("#request-image").set_input_files(
            str(ROOT / "tests" / "fixtures" / "handwritten_list.jpeg")
        )
        expect(page.locator("#photo-quality")).to_have_attribute(
            "data-status", "good"
        )
        page.get_by_role("button", name="Preview product matches").click()

        expect(
            page.get_by_role("heading", name="Check what the photo says")
        ).to_be_visible()
        rows = page.locator("[data-plan-item]")
        before = rows.count()
        existing_product = rows.first.get_by_label("Product")
        existing_product.fill("edited paneer")
        existing_include = rows.nth(1).get_by_label("Include")
        existing_include.uncheck()
        page.get_by_role("button", name="Add a missing item").click()
        expect(rows).to_have_count(before + 1)
        expect(rows.first.get_by_label("Product")).to_have_value("edited paneer")
        expect(rows.nth(1).get_by_label("Include")).not_to_be_checked()
        manual_row = page.locator("[data-plan-item]").last
        manual_row.get_by_label("Product").fill("onions")
        expect(manual_row).to_contain_text("Added manually")
        for width in (320, 375, 768, 1280):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate(
                "() => document.documentElement.scrollWidth "
                "<= document.documentElement.clientWidth"
            )
        browser.close()
