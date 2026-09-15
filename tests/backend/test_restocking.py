"""
Tests for restocking order API endpoints.
"""
import pytest
from datetime import datetime


class TestRestockingEndpoints:
    """Test suite for restocking-order-related endpoints."""

    def test_get_restocking_orders_empty_or_list(self, client):
        """Test that the restocking orders list endpoint always returns a list."""
        response = client.get("/api/restocking-orders")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)

    def test_create_restocking_order_happy_path(self, client):
        """Test creating a restocking order with valid demand-forecast SKUs."""
        # Use a known demand-forecast SKU so the recomputed cost is predictable
        response = client.get("/api/demand")
        forecasts = response.json()
        assert len(forecasts) > 0
        forecast = forecasts[0]

        payload = {
            "budget": 5000,
            "items": [
                {"item_sku": forecast["item_sku"], "quantity": 3}
            ]
        }
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert "id" in order
        assert "order_number" in order
        assert order["status"] == "Submitted"
        assert order["budget"] == 5000

        # Server must recompute cost/name from the forecast, not trust the request
        assert len(order["items"]) == 1
        line_item = order["items"][0]
        assert line_item["item_sku"] == forecast["item_sku"]
        assert line_item["item_name"] == forecast["item_name"]
        assert line_item["quantity"] == 3
        assert abs(line_item["unit_cost"] - forecast["unit_cost"]) < 0.01
        expected_line_total = round(3 * forecast["unit_cost"], 2)
        assert abs(line_item["line_total"] - expected_line_total) < 0.01
        assert abs(order["total_cost"] - expected_line_total) < 0.01

        # Lead time follows the same 7-14 day range used elsewhere in the app
        assert isinstance(order["lead_time_days"], int)
        assert 7 <= order["lead_time_days"] <= 14

        created = datetime.fromisoformat(order["created_date"])
        expected_delivery = datetime.fromisoformat(order["expected_delivery_date"])
        assert expected_delivery > created

    def test_create_restocking_order_multi_item(self, client):
        """Test that a single order can bundle multiple forecasted items."""
        forecasts = client.get("/api/demand").json()
        assert len(forecasts) >= 2

        payload = {
            "budget": 10000,
            "items": [
                {"item_sku": forecasts[0]["item_sku"], "quantity": 2},
                {"item_sku": forecasts[1]["item_sku"], "quantity": 5}
            ]
        }
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert len(order["items"]) == 2

        expected_total = round(
            2 * forecasts[0]["unit_cost"] + 5 * forecasts[1]["unit_cost"], 2
        )
        assert abs(order["total_cost"] - expected_total) < 0.01

    def test_create_restocking_order_unknown_sku(self, client):
        """Test that an unknown item_sku is rejected with a 400 and a clear detail message."""
        payload = {
            "budget": 1000,
            "items": [
                {"item_sku": "NOT-A-REAL-SKU", "quantity": 1}
            ]
        }
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 400

        data = response.json()
        assert "detail" in data
        assert "not-a-real-sku" in data["detail"].lower() or "unknown" in data["detail"].lower()

    def test_create_restocking_order_zero_quantity(self, client):
        """Test that a non-positive quantity is rejected."""
        forecasts = client.get("/api/demand").json()

        payload = {
            "budget": 1000,
            "items": [
                {"item_sku": forecasts[0]["item_sku"], "quantity": 0}
            ]
        }
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 400

    def test_create_restocking_order_no_items(self, client):
        """Test that an order with an empty item list is rejected."""
        payload = {"budget": 1000, "items": []}
        response = client.post("/api/restocking-orders", json=payload)
        assert response.status_code == 400

    def test_submitted_order_appears_in_list(self, client):
        """Test that a created order is retrievable via the list endpoint afterward."""
        forecasts = client.get("/api/demand").json()

        payload = {
            "budget": 2000,
            "items": [{"item_sku": forecasts[0]["item_sku"], "quantity": 1}]
        }
        create_response = client.post("/api/restocking-orders", json=payload)
        assert create_response.status_code == 201
        created_id = create_response.json()["id"]

        list_response = client.get("/api/restocking-orders")
        assert list_response.status_code == 200
        all_ids = [order["id"] for order in list_response.json()]
        assert created_id in all_ids

    def test_demand_forecasts_include_unit_cost(self, client):
        """Test that demand forecasts now carry a unit_cost usable for budget math."""
        response = client.get("/api/demand")
        assert response.status_code == 200

        data = response.json()
        assert len(data) > 0
        for item in data:
            assert "unit_cost" in item
            assert isinstance(item["unit_cost"], (int, float))
            assert item["unit_cost"] > 0
