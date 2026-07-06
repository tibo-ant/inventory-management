"""
Tests for the restocking API endpoints:
  GET  /api/restock/recommendations
  POST /api/restock/orders
  GET  /api/restock/orders
"""
from datetime import datetime

import pytest

# conftest.py inserts the server directory into sys.path before test modules
# are collected, so the backend's in-memory stores and constants are
# importable here. Importing the real constants (rather than re-declaring
# local copies) means these tests always exercise the server's actual policy.
from main import MAX_RESTOCK_BUDGET, TREND_PRIORITY
from mock_data import demand_forecasts, inventory_items, restock_orders


@pytest.fixture(autouse=True)
def reset_restock_orders():
    """Restocking orders accumulate in a module-level in-memory list.

    Clear it around every test so each test starts from an empty order book
    and tests stay independent of execution order.
    """
    restock_orders.clear()
    yield
    restock_orders.clear()


def _inventory_by_sku(client):
    """Index the live inventory by SKU for cross-endpoint assertions."""
    return {item["sku"]: item for item in client.get("/api/inventory").json()}


@pytest.fixture
def exact_cost_item():
    """Temporarily register an item whose cost triggers float floor-division loss.

    15.99 is not exactly representable in binary floating point, so a naive
    `budget // unit_cost` undercounts when the budget is an exact multiple of
    the cost (700 * 15.99 = 11193.0, but 11193.0 // 15.99 == 699.0). The
    shortfall of 700 outranks every real item so the allocator reaches it
    first with the whole budget.
    """
    item = {
        "id": "test-float", "sku": "TST-999", "name": "Float Regression Widget",
        "category": "Sensors", "warehouse": "London",
        "quantity_on_hand": 0, "reorder_point": 1, "unit_cost": 15.99,
        "location": "Warehouse T-1", "last_updated": "2025-09-30T10:30:00",
        "lead_time_days": 7,
    }
    forecast = {
        "id": "test-float", "item_sku": "TST-999",
        "item_name": "Float Regression Widget",
        "current_demand": 700, "forecasted_demand": 700,
        "trend": "increasing", "period": "Next 30 days",
    }
    inventory_items.append(item)
    demand_forecasts.append(forecast)
    yield item
    inventory_items.remove(item)
    demand_forecasts.remove(forecast)


class TestRestockRecommendations:
    """Test suite for GET /api/restock/recommendations."""

    def test_recommendations_default_budget_is_zero(self, client):
        """Omitting the budget defaults to 0, which recommends nothing."""
        response = client.get("/api/restock/recommendations")
        assert response.status_code == 200

        data = response.json()
        assert data["budget"] == 0
        assert data["total_cost"] == 0
        assert data["remaining_budget"] == 0
        assert data["recommendations"] == []

    def test_recommendations_structure(self, client):
        """Test the shape and types of a recommendation."""
        response = client.get(f"/api/restock/recommendations?budget={MAX_RESTOCK_BUDGET}")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data["recommendations"], list)
        assert len(data["recommendations"]) > 0

        required_fields = [
            "sku", "name", "category", "warehouse", "unit_cost",
            "quantity_on_hand", "forecasted_demand", "trend", "shortfall",
            "recommended_quantity", "line_total", "lead_time_days"
        ]
        for rec in data["recommendations"]:
            for field in required_fields:
                assert field in rec, f"Missing field: {field}"
            assert isinstance(rec["quantity_on_hand"], int)
            assert isinstance(rec["shortfall"], int)
            assert isinstance(rec["recommended_quantity"], int)
            assert isinstance(rec["lead_time_days"], int)
            assert isinstance(rec["unit_cost"], (int, float))
            assert isinstance(rec["line_total"], (int, float))

    def test_recommendations_only_include_shortfalls(self, client):
        """Only items whose forecast exceeds current stock are recommended."""
        response = client.get(f"/api/restock/recommendations?budget={MAX_RESTOCK_BUDGET}")
        data = response.json()

        for rec in data["recommendations"]:
            assert rec["forecasted_demand"] > rec["quantity_on_hand"]
            assert rec["shortfall"] == rec["forecasted_demand"] - rec["quantity_on_hand"]
            assert rec["shortfall"] > 0

    def test_recommendations_never_exceed_budget(self, client):
        """The recommended spend must always fit inside the budget."""
        for budget in [100, 5000, 25000, 50000, 100000]:
            response = client.get(f"/api/restock/recommendations?budget={budget}")
            data = response.json()

            assert data["total_cost"] <= budget
            assert abs(data["remaining_budget"] - (budget - data["total_cost"])) < 0.01

            # The reported total must be the sum of the line totals
            calculated = sum(r["line_total"] for r in data["recommendations"])
            assert abs(data["total_cost"] - calculated) < 0.01

    def test_recommendations_line_totals(self, client):
        """Each line total must be quantity * unit_cost."""
        response = client.get(f"/api/restock/recommendations?budget={MAX_RESTOCK_BUDGET}")
        data = response.json()

        for rec in data["recommendations"]:
            expected = rec["recommended_quantity"] * rec["unit_cost"]
            assert abs(rec["line_total"] - expected) < 0.01

    def test_recommendations_quantity_never_exceeds_shortfall(self, client):
        """The recommender must never suggest ordering more than the gap."""
        for budget in [1000, 50000, MAX_RESTOCK_BUDGET]:
            response = client.get(f"/api/restock/recommendations?budget={budget}")
            for rec in response.json()["recommendations"]:
                assert 1 <= rec["recommended_quantity"] <= rec["shortfall"]

    def test_recommendations_full_coverage_with_max_budget(self, client):
        """With an unconstrained budget every shortfall is fully covered."""
        response = client.get(f"/api/restock/recommendations?budget={MAX_RESTOCK_BUDGET}")
        data = response.json()

        assert len(data["recommendations"]) > 0
        for rec in data["recommendations"]:
            assert rec["recommended_quantity"] == rec["shortfall"]

    def test_recommendations_shrink_with_tighter_budget(self, client):
        """A budget below full coverage must buy strictly less."""
        full = client.get(f"/api/restock/recommendations?budget={MAX_RESTOCK_BUDGET}").json()
        assert full["total_cost"] > 0

        tight_budget = round(full["total_cost"] / 2, 2)
        tight = client.get(f"/api/restock/recommendations?budget={tight_budget}").json()

        assert tight["total_cost"] < full["total_cost"]
        assert tight["total_cost"] <= tight_budget
        # At least one item must be partially filled or dropped entirely
        full_qty = sum(r["recommended_quantity"] for r in full["recommendations"])
        tight_qty = sum(r["recommended_quantity"] for r in tight["recommendations"])
        assert tight_qty < full_qty

    def test_recommendations_partial_fill_uses_leftover_budget(self, client):
        """When an item's full shortfall no longer fits, a partial fill of
        whole units is still recommended so the budget is not wasted.

        The budget is derived from the data rather than hardcoded: it affords
        exactly one unit fewer than the highest-priority item's full
        shortfall, so that item is guaranteed to be partially filled no
        matter how the sample data evolves.
        """
        full = client.get(f"/api/restock/recommendations?budget={MAX_RESTOCK_BUDGET}").json()
        first = full["recommendations"][0]
        assert first["shortfall"] >= 2, "sample data needs a multi-unit shortfall"

        budget = round(first["unit_cost"] * (first["shortfall"] - 1), 2)
        data = client.get(f"/api/restock/recommendations?budget={budget}").json()

        top = data["recommendations"][0]
        assert top["sku"] == first["sku"]
        assert top["recommended_quantity"] == first["shortfall"] - 1
        assert top["recommended_quantity"] < top["shortfall"]

    def test_recommendations_exact_budget_buys_exact_quantity(self, client, exact_cost_item):
        """A budget exactly covering an item's shortfall must buy the full shortfall.

        Regression test for float floor-division: `11193.0 // 15.99 == 699.0`,
        so a naive allocator buys 699 of 700 units and reports leftover budget
        that could have covered the last unit.
        """
        budget = round(700 * exact_cost_item["unit_cost"], 2)  # 11193.0, an exact 700x
        data = client.get(f"/api/restock/recommendations?budget={budget}").json()

        rec = next(r for r in data["recommendations"] if r["sku"] == exact_cost_item["sku"])
        assert rec["shortfall"] == 700
        assert rec["recommended_quantity"] == 700
        assert abs(data["remaining_budget"]) < 0.01

    def test_recommendations_priority_ordering(self, client):
        """Increasing-trend items must come first, then by largest shortfall."""
        response = client.get(f"/api/restock/recommendations?budget={MAX_RESTOCK_BUDGET}")
        recs = response.json()["recommendations"]
        assert len(recs) > 1

        priorities = [TREND_PRIORITY[r["trend"]] for r in recs]
        assert priorities == sorted(priorities), "Trend priority is not respected"

        # Within a trend group, shortfalls must be non-increasing
        for prev, curr in zip(recs, recs[1:]):
            if prev["trend"] == curr["trend"]:
                assert prev["shortfall"] >= curr["shortfall"]

    def test_recommendations_match_inventory(self, client):
        """Cross-endpoint check: every recommendation must agree with inventory."""
        inventory = _inventory_by_sku(client)
        response = client.get(f"/api/restock/recommendations?budget={MAX_RESTOCK_BUDGET}")

        for rec in response.json()["recommendations"]:
            assert rec["sku"] in inventory
            item = inventory[rec["sku"]]
            assert rec["unit_cost"] == item["unit_cost"]
            assert rec["quantity_on_hand"] == item["quantity_on_hand"]
            assert rec["lead_time_days"] == item["lead_time_days"]
            assert rec["warehouse"] == item["warehouse"]

    def test_recommendations_negative_budget_rejected(self, client):
        """A negative budget must fail validation."""
        response = client.get("/api/restock/recommendations?budget=-1")
        assert response.status_code == 422

    def test_recommendations_excessive_budget_rejected(self, client):
        """A budget above the sanity ceiling must fail validation."""
        response = client.get(f"/api/restock/recommendations?budget={MAX_RESTOCK_BUDGET + 1}")
        assert response.status_code == 422


class TestCreateRestockOrder:
    """Test suite for POST /api/restock/orders."""

    def test_create_restock_order_success(self, client):
        """A valid submission returns 201 with a fully-formed order."""
        response = client.post(
            "/api/restock/orders",
            json={"budget": 50000, "items": [
                {"sku": "MCU-402", "quantity": 670},
                {"sku": "TMP-201", "quantity": 100},
            ]},
        )
        assert response.status_code == 201

        order = response.json()
        assert order["status"] == "Submitted"
        assert order["order_number"].startswith("RST-")
        assert order["budget"] == 50000
        assert len(order["items"]) == 2
        assert order["total_cost"] > 0
        assert order["lead_time_days"] > 0
        assert "T" in order["created_date"]
        assert "T" in order["expected_delivery"]

    def test_created_order_appears_in_listing(self, client):
        """A submitted order must be returned by GET /api/restock/orders."""
        assert client.get("/api/restock/orders").json() == []

        created = client.post(
            "/api/restock/orders",
            json={"budget": 5000, "items": [{"sku": "MCU-402", "quantity": 10}]},
        ).json()

        listing = client.get("/api/restock/orders").json()
        assert len(listing) == 1
        assert listing[0]["order_number"] == created["order_number"]

    def test_create_order_ignores_client_supplied_prices(self, client):
        """The server must re-price from inventory, never trust the client.

        Extra fields like unit_cost in an item are silently dropped by the
        request model, so a client cannot buy at its own price.
        """
        inventory = _inventory_by_sku(client)
        real_cost = inventory["SRV-301"]["unit_cost"]
        assert real_cost > 0.01  # the tampered price must actually differ

        response = client.post(
            "/api/restock/orders",
            json={"budget": 50000, "items": [
                {"sku": "SRV-301", "quantity": 10, "unit_cost": 0.01, "line_total": 0.1}
            ]},
        )
        assert response.status_code == 201

        order = response.json()
        assert order["items"][0]["unit_cost"] == real_cost
        assert abs(order["total_cost"] - 10 * real_cost) < 0.01

    def test_create_order_total_matches_inventory_prices(self, client):
        """The order total must be the sum of quantity * inventory unit cost."""
        inventory = _inventory_by_sku(client)
        items = [{"sku": "MCU-402", "quantity": 5}, {"sku": "DRV-405", "quantity": 3}]

        order = client.post(
            "/api/restock/orders", json={"budget": 5000, "items": items}
        ).json()

        expected = sum(i["quantity"] * inventory[i["sku"]]["unit_cost"] for i in items)
        assert abs(order["total_cost"] - expected) < 0.01
        for line in order["items"]:
            assert abs(line["line_total"] - line["quantity"] * line["unit_cost"]) < 0.01

    def test_create_order_lead_time_is_max_of_items(self, client):
        """Delivery is gated by the slowest supplier among the order's items."""
        inventory = _inventory_by_sku(client)
        skus = ["MCU-402", "SRV-301"]  # 7-day and 28-day lead times

        order = client.post(
            "/api/restock/orders",
            json={"budget": 50000, "items": [{"sku": s, "quantity": 1} for s in skus]},
        ).json()

        expected_max = max(inventory[s]["lead_time_days"] for s in skus)
        assert order["lead_time_days"] == expected_max
        for line in order["items"]:
            assert line["lead_time_days"] == inventory[line["sku"]]["lead_time_days"]

    def test_create_order_expected_delivery_reflects_lead_time(self, client):
        """expected_delivery must be created_date + lead_time_days."""
        order = client.post(
            "/api/restock/orders",
            json={"budget": 50000, "items": [{"sku": "SRV-301", "quantity": 1}]},
        ).json()

        created = datetime.fromisoformat(order["created_date"])
        expected = datetime.fromisoformat(order["expected_delivery"])
        assert (expected - created).days == order["lead_time_days"]

    def test_create_order_sequential_order_numbers(self, client):
        """Order numbers must increment per submission."""
        first = client.post(
            "/api/restock/orders",
            json={"budget": 1000, "items": [{"sku": "MCU-402", "quantity": 1}]},
        ).json()
        second = client.post(
            "/api/restock/orders",
            json={"budget": 1000, "items": [{"sku": "MCU-402", "quantity": 1}]},
        ).json()

        assert first["order_number"].endswith("-0001")
        assert second["order_number"].endswith("-0002")

    def test_create_order_unknown_sku_returns_400(self, client):
        """A SKU that is not in inventory must be rejected."""
        response = client.post(
            "/api/restock/orders",
            json={"budget": 1000, "items": [{"sku": "FAKE-999", "quantity": 1}]},
        )
        assert response.status_code == 400
        assert "FAKE-999" in response.json()["detail"]
        # Nothing must be persisted on a rejected request
        assert client.get("/api/restock/orders").json() == []

    def test_create_order_exceeding_budget_returns_400(self, client):
        """The recomputed total must not exceed the submitted budget."""
        response = client.post(
            "/api/restock/orders",
            json={"budget": 10, "items": [{"sku": "SRV-301", "quantity": 100}]},
        )
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()
        assert client.get("/api/restock/orders").json() == []

    def test_create_order_duplicate_sku_returns_400(self, client):
        """Duplicate SKU lines must be rejected."""
        response = client.post(
            "/api/restock/orders",
            json={"budget": 1000, "items": [
                {"sku": "MCU-402", "quantity": 1},
                {"sku": "MCU-402", "quantity": 1},
            ]},
        )
        assert response.status_code == 400
        assert "duplicate" in response.json()["detail"].lower()

    def test_create_order_validation_errors_return_422(self, client):
        """Malformed requests must fail Pydantic validation with 422."""
        cases = [
            # zero quantity
            {"budget": 1000, "items": [{"sku": "MCU-402", "quantity": 0}]},
            # negative quantity
            {"budget": 1000, "items": [{"sku": "MCU-402", "quantity": -5}]},
            # empty items list
            {"budget": 1000, "items": []},
            # zero budget
            {"budget": 0, "items": [{"sku": "MCU-402", "quantity": 1}]},
            # budget above the sanity ceiling
            {"budget": MAX_RESTOCK_BUDGET + 1, "items": [{"sku": "MCU-402", "quantity": 1}]},
            # missing items
            {"budget": 1000},
        ]
        for payload in cases:
            response = client.post("/api/restock/orders", json=payload)
            assert response.status_code == 422, f"Expected 422 for {payload}"


class TestGetRestockOrders:
    """Test suite for GET /api/restock/orders."""

    def test_get_restock_orders_empty_initially(self, client):
        """The order book starts empty every session."""
        response = client.get("/api/restock/orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_restock_orders_newest_first(self, client):
        """Orders must be returned in reverse submission order."""
        for _ in range(3):
            client.post(
                "/api/restock/orders",
                json={"budget": 1000, "items": [{"sku": "MCU-402", "quantity": 1}]},
            )

        listing = client.get("/api/restock/orders").json()
        numbers = [order["order_number"] for order in listing]
        assert numbers == sorted(numbers, reverse=True)

    def test_restock_orders_do_not_appear_in_customer_orders(self, client):
        """Restocking orders are a separate collection from customer orders."""
        client.post(
            "/api/restock/orders",
            json={"budget": 1000, "items": [{"sku": "MCU-402", "quantity": 1}]},
        )

        customer_orders = client.get("/api/orders").json()
        assert not any(o["order_number"].startswith("RST-") for o in customer_orders)
