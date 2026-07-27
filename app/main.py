from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote_plus, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

from .comparison_service import ComparisonService
from .config import ROOT, get_settings
from .constraints import enforce_constraints
from .llm import ModelBackendError
from .matcher import match_product
from .models import (
    AddressSelectionRequest,
    CartConstraints,
    CartPlan,
    ComparisonChoiceRequest,
    ComparisonOperation,
    ComparisonPreflight,
    ComparisonPreflightRequest,
    ComparisonProposal,
    ConfirmRequest,
    ConfirmResponse,
    DraftCart,
    DraftItem,
    ProviderSelectionRequest,
    ProposalOverrideRequest,
    SearchRequest,
    StreamEvent,
    VerifiedComparisonRequest,
)
from .providers import GroceryProvider, ProviderError, create_providers
from .planner import plan_cart, retry_uncertain_with_cloud
from .recognition import resolve_plan_autonomously


settings = get_settings()
providers = create_providers(settings)
comparison_service = ComparisonService(providers, settings)
ACTIVE_PROVIDER_ID = settings.grocery_provider
DRAFTS: OrderedDict[str, DraftCart] = OrderedDict()
DRAFT_CONSTRAINTS: OrderedDict[str, CartConstraints] = OrderedDict()
ALLOWED_IMAGE_TYPES = {
    "image/heic",
    "image/heif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await asyncio.gather(*(provider.close() for provider in providers.values()))


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
LOCAL_BROWSER_HOSTS = {"127.0.0.1", "::1", "localhost", "testserver"}


@app.middleware("http")
async def enforce_local_browser_origin(request: Request, call_next):
    """Reject DNS-rebinding and cross-site browser writes to the local app."""
    request_host = request.url.hostname
    if request_host not in LOCAL_BROWSER_HOSTS:
        return JSONResponse(
            {"detail": "Photo Shopping accepts requests only on a local address."},
            status_code=400,
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            request_port = request.url.port or (443 if request.url.scheme == "https" else 80)
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if parsed.hostname not in LOCAL_BROWSER_HOSTS or origin_port != request_port:
                return JSONResponse(
                    {"detail": "Cross-origin requests to the local app are blocked."},
                    status_code=403,
                )
    return await call_next(request)


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


def remember_draft(draft: DraftCart, constraints: CartConstraints) -> None:
    DRAFTS[draft.id] = draft
    DRAFTS.move_to_end(draft.id)
    DRAFT_CONSTRAINTS[draft.id] = constraints
    DRAFT_CONSTRAINTS.move_to_end(draft.id)
    while len(DRAFTS) > settings.max_state_records:
        expired_id, _ = DRAFTS.popitem(last=False)
        DRAFT_CONSTRAINTS.pop(expired_id, None)


async def read_uploaded_image(
    image: UploadFile | None,
) -> tuple[bytes | None, str]:
    if image is None:
        return None, "image/jpeg"
    image_type = (image.content_type or "").casefold()
    if image_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Upload a JPG, PNG, WebP, HEIC, or HEIF image.",
        )
    image_bytes = await image.read()
    if len(image_bytes) > 12 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The image must be smaller than 12 MB.")
    if not image_bytes:
        raise HTTPException(status_code=422, detail="The uploaded image is empty.")
    if image_type not in {"image/heic", "image/heif"}:
        try:
            with Image.open(BytesIO(image_bytes)) as decoded:
                decoded.verify()
        except (OSError, SyntaxError, UnidentifiedImageError) as exc:
            raise HTTPException(
                status_code=422,
                detail="The uploaded file is not a readable image.",
            ) from exc
    return image_bytes, image_type


async def prepare_plan(
    *,
    text: str,
    image_bytes: bytes | None,
    image_type: str,
    provider_ids: list[str],
    use_cloud: bool = False,
) -> CartPlan:
    """Build a plan and apply the configured recognition decision policy."""
    plan = await asyncio.to_thread(
        plan_cart,
        text=text,
        image_bytes=image_bytes,
        image_media_type=image_type,
        settings=settings,
    )
    uncertain = image_bytes and any(item.needs_review for item in plan.items)
    if not uncertain:
        return plan

    if settings.recognition_policy == "autonomous_safe":
        local_plan = plan.model_copy(deep=True)
        cloud_plan: CartPlan | None = None
        cloud_note = ""
        if settings.cloud_backend is not None:
            try:
                cloud_plan = await asyncio.to_thread(
                    retry_uncertain_with_cloud,
                    plan=plan.model_copy(deep=True),
                    image_bytes=image_bytes,
                    settings=settings,
                    blind=True,
                )
                cloud_note = (
                    f" Only uncertain line crops were sent to "
                    f"{settings.cloud_backend.title()} for an independent second opinion; "
                    "the complete photograph stayed local."
                )
            except ModelBackendError as exc:
                cloud_note = (
                    " Hosted vision was unavailable; autonomous recognition continued "
                    f"with local and catalogue evidence ({exc})."
                )
        selected_providers = {
            provider_id: providers[provider_id]
            for provider_id in provider_ids
            if provider_id in providers
        }
        resolved = await resolve_plan_autonomously(
            local_plan,
            cloud_plan,
            selected_providers,
            settings,
        )
        resolved.processing_note += cloud_note
        return resolved

    if use_cloud:
        return await asyncio.to_thread(
            retry_uncertain_with_cloud,
            plan=plan,
            image_bytes=image_bytes,
            settings=settings,
        )
    return plan


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
        "blinkit_cart_writes": settings.blinkit_cart_writes,
        "instamart_cart_writes": settings.instamart_cart_writes,
        "zepto_cart_writes": settings.zepto_cart_writes,
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
        "model_configured": settings.model_backend_configured() or settings.demo_mode,
        "cloud_retry_available": settings.cloud_backend is not None and not settings.demo_mode,
        "cloud_retry_provider": settings.cloud_backend or "",
        "cloud_model_id": settings.cloud_model if settings.cloud_backend else "",
        "recognition_policy": settings.recognition_policy,
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


@app.post("/api/plans/preview", response_model=CartPlan)
async def preview_plan(
    text: str = Form(default=""),
    image: UploadFile | None = File(default=None),
    use_cloud: bool = Form(default=False),
    provider_ids: str = Form(default=""),
) -> CartPlan:
    image_bytes, image_type = await read_uploaded_image(image)
    if not text.strip() and not image_bytes:
        raise HTTPException(status_code=422, detail="Add a photo, a typed request, or both.")
    try:
        selected = [
            provider_id.strip()
            for provider_id in provider_ids.split(",")
            if provider_id.strip()
        ] or [ACTIVE_PROVIDER_ID]
        return await prepare_plan(
            text=text,
            image_bytes=image_bytes,
            image_type=image_type,
            provider_ids=selected,
            use_cloud=use_cloud,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/drafts/stream")
async def create_draft_stream(
    text: str = Form(default=""),
    image: UploadFile | None = File(default=None),
    provider_id: str | None = Form(default=None),
    plan_json: str = Form(default=""),
) -> StreamingResponse:
    image_bytes, image_type = await read_uploaded_image(image)
    reviewed_plan: CartPlan | None = None
    if plan_json.strip():
        try:
            reviewed_plan = CartPlan.model_validate_json(plan_json)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"The reviewed plan is invalid: {exc}") from exc
    if not text.strip() and not image_bytes and reviewed_plan is None:
        raise HTTPException(status_code=422, detail="Add a photo, a typed request, or both.")
    draft_provider = get_provider(provider_id)
    mutations_allowed = provider_mutations_allowed(draft_provider)

    async def generate() -> AsyncIterator[bytes]:
        try:
            yield encode_event(
                StreamEvent(event="stage", stage="planner", message="Reading the request and building a cart plan…")
            )
            plan = reviewed_plan
            if plan is None:
                plan = await prepare_plan(
                    text=text,
                    image_bytes=image_bytes,
                    image_type=image_type,
                    provider_ids=[draft_provider.provider_id],
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
                if planned.needs_review and not planned.confirmed:
                    autonomous_skip = (
                        settings.recognition_policy == "autonomous_safe"
                        or planned.recognition_decision == "skipped"
                    )
                    draft_item = DraftItem(
                        planned=planned,
                        candidates=[],
                        selected_product_id=None,
                        units_to_add=0,
                        reason=(
                            "Skipped automatically because recognition evidence was unsafe."
                            if autonomous_skip
                            else "Review this transcription before provider search."
                        ),
                        flags=[
                            (
                                "This uncertain line was skipped automatically; no product "
                                "was searched or added."
                                if autonomous_skip
                                else "This line was not sent to the grocery provider because "
                                "its transcription is uncertain."
                            )
                        ],
                    )
                    draft_items.append(draft_item)
                    yield encode_event(
                        StreamEvent(
                            event="item",
                            stage="matcher",
                            message=(
                                f"Safely skipped uncertain line {planned.search_term}."
                                if autonomous_skip
                                else f"Waiting for review of {planned.search_term}."
                            ),
                            data=draft_item.model_dump(mode="json"),
                        )
                    )
                    continue
                provider_query = planned.provider_query
                yield encode_event(
                    StreamEvent(
                        event="stage",
                        stage="retrieval",
                        message=(
                            f"Searching {draft_provider.display_name} for {provider_query} "
                            f"({index}/{len(plan.items)})…"
                        ),
                    )
                )
                # One provider failure must not discard the items already searched:
                # the rest of the list is still worth reviewing, and the failure is
                # reported on its own item rather than as an empty result.
                search_error = ""
                try:
                    candidates = await draft_provider.search(provider_query)
                except ProviderError as exc:
                    candidates = []
                    search_error = str(exc) or exc.__class__.__name__
                yield encode_event(
                    StreamEvent(
                        event="stage",
                        stage="matcher",
                        message=(
                            f"{draft_provider.display_name} could not search for {planned.search_term}."
                            if search_error
                            else f"Comparing {len(candidates)} result{'s' if len(candidates) != 1 else ''} for {planned.search_term}…"
                        ),
                    )
                )
                if search_error:
                    draft_item = DraftItem(
                        planned=planned,
                        candidates=[],
                        selected_product_id=None,
                        units_to_add=0,
                        reason=search_error,
                        flags=[f"{draft_provider.display_name} search failed: {search_error}"],
                    )
                else:
                    decision = await asyncio.to_thread(
                        match_product, planned, candidates, settings
                    )
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
            remember_draft(draft, plan.constraints)
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
    item.planned.apply_manual_query(request.query)
    try:
        candidates = await draft_provider.search(item.planned.provider_query)
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
    remember_draft(refreshed, constraints)
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


@app.post("/api/comparisons/preflight", response_model=ComparisonPreflight)
async def comparison_preflight(
    request: ComparisonPreflightRequest,
) -> ComparisonPreflight:
    return await comparison_service.preflight(
        list(request.provider_ids),
        mode=request.mode,
        proposal_id=request.proposal_id,
    )


@app.post("/api/comparisons/estimate", response_model=ComparisonProposal)
async def estimate_comparison(
    text: str = Form(default=""),
    image: UploadFile | None = File(default=None),
    provider_ids: str = Form(default="blinkit,instamart,zepto"),
    plan_json: str = Form(default=""),
) -> ComparisonProposal:
    image_bytes, image_type = await read_uploaded_image(image)
    reviewed_plan: CartPlan | None = None
    if plan_json.strip():
        try:
            reviewed_plan = CartPlan.model_validate_json(plan_json)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"The reviewed plan is invalid: {exc}") from exc
    if not text.strip() and not image_bytes and reviewed_plan is None:
        raise HTTPException(status_code=422, detail="Add a photo, a typed request, or both.")

    selected = [
        provider_id.strip()
        for provider_id in provider_ids.split(",")
        if provider_id.strip()
    ]
    if not selected:
        raise HTTPException(status_code=422, detail="Choose at least one provider.")
    try:
        plan = reviewed_plan
        if plan is None:
            plan = await prepare_plan(
                text=text,
                image_bytes=image_bytes,
                image_type=image_type,
                provider_ids=selected,
            )
        return await comparison_service.estimate(plan, selected)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post(
    "/api/comparisons/proposals/{proposal_id}/verify-preflight",
    response_model=ComparisonPreflight,
)
async def verified_comparison_preflight(
    proposal_id: str,
) -> ComparisonPreflight:
    proposal = comparison_service.proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Comparison proposal not found.")
    return await comparison_service.preflight(
        proposal.provider_ids,
        mode="verified",
        proposal_id=proposal_id,
    )


@app.post(
    "/api/comparisons/proposals/{proposal_id}/override",
    response_model=ComparisonProposal,
)
async def override_comparison_proposal(
    proposal_id: str,
    request: ProposalOverrideRequest,
) -> ComparisonProposal:
    try:
        return comparison_service.override(proposal_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/comparisons/proposals/{proposal_id}/verify",
    response_model=ComparisonOperation,
)
async def verify_comparison(
    proposal_id: str,
    request: VerifiedComparisonRequest,
) -> ComparisonOperation:
    try:
        return await comparison_service.verify(
            proposal_id,
            request.confirmation_token,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(
    "/api/comparisons/{operation_id}",
    response_model=ComparisonOperation,
)
async def get_comparison(operation_id: str) -> ComparisonOperation:
    operation = comparison_service.operations.get(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Comparison operation not found.")
    return operation


@app.post(
    "/api/comparisons/{operation_id}/choose",
    response_model=ComparisonOperation,
)
async def choose_comparison(
    operation_id: str,
    request: ComparisonChoiceRequest,
) -> ComparisonOperation:
    try:
        return await comparison_service.choose(operation_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


static_dir = Path(ROOT / "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
