from app.main import app


def test_comparison_routes_are_exposed_without_checkout_routes():
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/api/plans/preview" in paths
    assert "/api/comparisons/preflight" in paths
    assert "/api/comparisons/estimate" in paths
    assert "/api/comparisons/proposals/{proposal_id}/verify-preflight" in paths
    assert "/api/comparisons/proposals/{proposal_id}/override" in paths
    assert "/api/comparisons/proposals/{proposal_id}/verify" in paths
    assert "/api/comparisons/{operation_id}/choose" in paths
    assert not any(
        forbidden in path.casefold()
        for path in paths
        for forbidden in ("checkout", "payment", "place-order", "place_order")
    )
