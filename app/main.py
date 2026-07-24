from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote_plus

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, get_settings
from .constraints import enforce_constraints
from .matcher import match_product
from .models import (
    AddressSelectionRequest,
    CartConstraints,
    ConfirmRequest,
    ConfirmResponse,
    DraftCart,
    DraftItem,
    ProviderSelectionRequest,
    SearchRequest,
    StreamEvent,
)
from .planner import plan_cart
from .providers import GroceryProvider, ProviderError, create_providers


settings = get_settings()
providers = create_providers(settings)
ACTIVE_PROVIDER_ID = settings.grocery_provider
DRAFTS: dict[str, DraftCart] = {}
DRAFT_CONSTRAINTS: dict[str, CartConstraints] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await asyncio.gather(*(provider.close() for provider in providers.values()))


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)


def encode_event(event: StreamEvent) -> bytes:
    return (event.model_dump_json() + "\n").encode("utf-8")


def get_provider(provider_id: str | None = None) -> GroceryProvider:
    selected_id = provider_id or ACTIVE_PROVIDER_ID
    provider = providers.get(selected_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="That grocery provider is unavailable.")
    return provider


def provider_mutations_allowed(provider: GroceryProvider) -> bool:
    return settings.cart_mutations_allowed_for(provider.provider_id)


@app.get("/api/health")
async def health() -> dict[str, object]:
    provider = get_provider()
    return {
        "status": "ok",
        "dry_run": settings.dry_run,
        "demo_mode": settings.demo_mode,
        "safety_lock": settings.safety_lock,
        "grocery_provider": provider.provider_id,
        "provider_name": provider.display_name,
        "auto_add_to_cart": settings.auto_add_to_cart,
        "instamart_cart_writes": settings.instamart_cart_writes,
        "cart_mutations_allowed": provider_mutations_allowed(provider),
        "checkout_disabled": True,
        "providers": [
            {
                "id": available.provider_id,
                "display_name": available.display_name,
                "cart_mutations_allowed": provider_mutations_allowed(available),
            }
            for available in providers.values()
        ],
        "local_vision_fallback": settings.local_vision_fallback,
        "model_backend": settings.model_backend,
        "model_configured": (
            settings.model_backend == "local" or bool(settings.hf_token) or settings.demo_mode
        ),
        "model_id": settings.planner_model,
    }


@app.get("/tokens.css", include_in_schema=False)
async def design_tokens() -> FileResponse:
    return FileResponse(ROOT / "tokens.css", media_type="text/css")


@app.post("/api/login")
async def login() -> dict[str, object]:
    provider = get_provider()
    try:
        result = await provider.connect()
        return result.model_dump(mode="json")
    except ProviderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/providers/status")
async def provider_status(
    refresh: bool = Query(default=False),
    provider_id: str | None = Query(default=None, alias="provider"),
) -> dict[str, object]:
    provider = get_provider(provider_id)
    try:
        return (await provider.status(refresh=refresh)).model_dump(mode="json")
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/providers/connect")
async def connect_provider(
    provider_id: str | None = Query(default=None, alias="provider"),
) -> dict[str, object]:
    provider = get_provider(provider_id)
    try:
        return (await provider.connect()).model_dump(mode="json")
    except ProviderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/providers/select")
async def select_provider(request: ProviderSelectionRequest) -> dict[str, object]:
    global ACTIVE_PROVIDER_ID
    provider = get_provider(request.provider_id)
    ACTIVE_PROVIDER_ID = provider.provider_id
    try:
        status = await provider.status()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "active_provider": provider.provider_id,
        "display_name": provider.display_name,
        "cart_mutations_allowed": provider_mutations_allowed(provider),
        "status": status.model_dump(mode="json"),
    }


@app.get("/api/providers/instamart/callback")
async def instamart_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    global ACTIVE_PROVIDER_ID
    provider = get_provider("instamart")
    ACTIVE_PROVIDER_ID = "instamart"
    if error:
        return RedirectResponse(
            f"/?provider=instamart&provider_error={quote_plus(error)}"
        )
    if not code or not state:
        return RedirectResponse(
            "/?provider=instamart&provider_error=Missing+Swiggy+authorization+response"
        )
    try:
        await provider.complete_oauth(code, state)
    except ProviderError as exc:
        return RedirectResponse(
            f"/?provider=instamart&provider_error={quote_plus(str(exc))}"
        )
    ACTIVE_PROVIDER_ID = "instamart"
    return RedirectResponse("/?provider=instamart&provider_connected=instamart")


@app.post("/api/providers/disconnect")
async def disconnect_provider(
    provider_id: str | None = Query(default=None, alias="provider"),
) -> dict[str, object]:
    provider = get_provider(provider_id)
    try:
        await provider.disconnect()
        return {"disconnected": True}
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/providers/address")
async def select_provider_address(
    request: AddressSelectionRequest,
    provider_id: str | None = Query(default=None, alias="provider"),
) -> dict[str, object]:
    provider = get_provider(provider_id)
    try:
        return (await provider.select_address(request.address_id)).model_dump(mode="json")
    except ProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/drafts/stream")
async def create_draft_stream(
    text: str = Form(default=""),
    image: UploadFile | None = File(default=None),
    provider_id: str | None = Form(default=None),
) -> StreamingResponse:
    image_bytes: bytes | None = None
    image_type = "image/jpeg"
    if image is not None:
        image_type = image.content_type or image_type
        if not image_type.startswith("image/"):
            raise HTTPException(status_code=415, detail="The uploaded file must be an image.")
        image_bytes = await image.read()
        if len(image_bytes) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="The image must be smaller than 12 MB.")
    if not text.strip() and not image_bytes:
        raise HTTPException(status_code=422, detail="Add a photo, a typed request, or both.")
    draft_provider = get_provider(provider_id)
    mutations_allowed = provider_mutations_allowed(draft_provider)

    async def generate() -> AsyncIterator[bytes]:
        try:
            yield encode_event(
                StreamEvent(event="stage", stage="planner", message="Reading the request and building a cart plan…")
            )
            plan = await asyncio.to_thread(
                plan_cart,
                text=text,
                image_bytes=image_bytes,
                image_media_type=image_type,
                settings=settings,
            )
            yield encode_event(
                StreamEvent(
                    event="plan",
                    stage="planner",
                    message=f"Planned {len(plan.items)} item{'s' if len(plan.items) != 1 else ''}.",
                    data=plan.model_dump(mode="json"),
                )
            )

            draft_items: list[DraftItem] = []
            for index, planned in enumerate(plan.items, start=1):
                yield encode_event(
                    StreamEvent(
                        event="stage",
                        stage="retrieval",
                        message=(
                            f"Searching {draft_provider.display_name} for {planned.search_term} "
                            f"({index}/{len(plan.items)})…"
                        ),
                    )
                )
                candidates = await draft_provider.search(planned.search_term)
                yield encode_event(
                    StreamEvent(
                        event="stage",
                        stage="matcher",
                        message=f"Comparing {len(candidates)} result{'s' if len(candidates) != 1 else ''} for {planned.search_term}…",
                    )
                )
                decision = await asyncio.to_thread(match_product, planned, candidates, settings)
                draft_item = DraftItem(
                    planned=planned,
                    candidates=candidates,
                    selected_product_id=decision.product_id,
                    units_to_add=decision.units_to_add,
                    reason=decision.reason,
                )
                draft_items.append(draft_item)
                yield encode_event(
                    StreamEvent(
                        event="item",
                        stage="matcher",
                        message=f"Matched {planned.search_term}.",
                        data=draft_item.model_dump(mode="json"),
                    )
                )

            yield encode_event(
                StreamEvent(
                    event="stage",
                    stage="constraints",
                    message="Checking caps, quantities, and cart budget…",
                )
            )
            draft = enforce_constraints(
                draft_items,
                plan.constraints,
                dry_run=not mutations_allowed,
                provider_id=draft_provider.provider_id,
                provider_name=draft_provider.display_name,
            )
            if plan.processing_note:
                draft.notices.append(plan.processing_note)
            if settings.auto_add_to_cart and mutations_allowed:
                selected_items = [
                    item
                    for item in draft.items
                    if not item.removed
                    and item.selected_product is not None
                    and item.units_to_add > 0
                ]
                if selected_items:
                    yield encode_event(
                        StreamEvent(
                            event="stage",
                            stage="cart",
                            message=(
                                "Adding the best matches to your "
                                f"{draft_provider.display_name} cart…"
                            ),
                        )
                    )
                    add_results = await draft_provider.add_items(
                        [
                            (item.selected_product, item.units_to_add)
                            for item in selected_items
                        ],
                        operation_id=draft.id,
                    )
                    draft.auto_add_messages.extend(
                        result.message for result in add_results if result.success
                    )
                    draft.auto_add_errors.extend(
                        f"{result.message} No checkout or order action was attempted."
                        for result in add_results
                        if not result.success
                    )
                    succeeded = sum(result.success for result in add_results)
                    if succeeded:
                        draft.notices.append(
                            f"Added {succeeded} best-match item"
                            f"{'s' if succeeded != 1 else ''} to your "
                            f"{draft_provider.display_name} cart. "
                            "Checkout, payment, and order placement remain unavailable in this app."
                        )
            DRAFTS[draft.id] = draft
            DRAFT_CONSTRAINTS[draft.id] = plan.constraints
            yield encode_event(
                StreamEvent(
                    event="draft",
                    stage="review",
                    message=(
                        f"Best matches added to {draft_provider.display_name}."
                        if draft.auto_add_messages
                        else f"{draft_provider.display_name} Add needs attention."
                        if draft.auto_add_errors
                        else "Best matches are ready to review."
                    ),
                    data=draft.model_dump(mode="json"),
                )
            )
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            yield encode_event(StreamEvent(event="error", stage="error", message=message))

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.post("/api/search")
async def research_item(request: SearchRequest) -> DraftItem:
    draft = DRAFTS.get(request.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft cart not found. Create a new draft.")
    item = next(
        (entry for entry in draft.items if entry.planned.id == request.planned_item_id),
        None,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Draft item not found.")
    draft_provider = get_provider(draft.provider_id or None)
    item.planned.search_term = request.query.strip()
    try:
        candidates = await draft_provider.search(item.planned.search_term)
        decision = await asyncio.to_thread(match_product, item.planned, candidates, settings)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    item.candidates = candidates
    item.selected_product_id = decision.product_id
    item.units_to_add = decision.units_to_add
    item.reason = decision.reason
    item.flags = []
    constraints = DRAFT_CONSTRAINTS.get(draft.id, CartConstraints())
    refreshed = enforce_constraints(
        draft.items,
        constraints,
        dry_run=draft.dry_run,
        draft_id=draft.id,
        provider_id=draft.provider_id,
        provider_name=draft.provider_name,
    )
    DRAFTS[draft.id] = refreshed
    return next(entry for entry in refreshed.items if entry.planned.id == request.planned_item_id)


@app.post("/api/confirm", response_model=ConfirmResponse)
async def confirm_cart(request: ConfirmRequest) -> ConfirmResponse:
    draft = DRAFTS.get(request.draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft cart not found. Create a new draft.")
    draft_provider = get_provider(draft.provider_id or None)
    mutations_allowed = provider_mutations_allowed(draft_provider)
    if settings.auto_add_to_cart and mutations_allowed:
        raise HTTPException(
            status_code=409,
            detail="Automatic Add is enabled; this draft cannot be added a second time.",
        )

    selections = []
    for selection in request.selections:
        item = next(
            (entry for entry in draft.items if entry.planned.id == selection.planned_item_id),
            None,
        )
        if not item:
            raise HTTPException(status_code=422, detail="A selected draft item no longer exists.")
        product = next((entry for entry in item.candidates if entry.id == selection.product_id), None)
        if not product:
            raise HTTPException(status_code=422, detail="A selected product is not part of this draft.")
        selections.append((product, selection.units_to_add))

    results = await draft_provider.add_items(selections, operation_id=draft.id)

    succeeded = sum(result.success for result in results)
    return ConfirmResponse(
        results=results,
        succeeded=succeeded,
        failed=len(results) - succeeded,
        dry_run=not mutations_allowed,
    )


static_dir = Path(ROOT / "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
