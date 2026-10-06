from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_create_inventory_reservation() -> None:
    response = client.post(
        "/inventory/reservations",
        headers={"Idempotency-Key": "test-create-001"},
        json={
            "item_id": "item-001",
            "quantity": 2,
        },
    )

    assert response.status_code == 201

    payload = response.json()

    assert payload["reservation_id"]
    assert payload["item_id"] == "item-001"
    assert payload["quantity"] == 2
    assert payload["status"] == "reserved"


def test_quantity_must_be_greater_than_zero() -> None:
    response = client.post(
        "/inventory/reservations",
        headers={"Idempotency-Key": "test-invalid-quantity"},
        json={
            "item_id": "item-001",
            "quantity": 0,
        },
    )

    assert response.status_code == 422


def test_unknown_inventory_item_returns_domain_404() -> None:
    response = client.post(
        "/inventory/reservations",
        headers={"Idempotency-Key": "test-unknown-item"},
        json={
            "item_id": "item-999",
            "quantity": 1,
        },
    )

    assert response.status_code == 404

    payload = response.json()

    # Prevent the test passing simply because the route does not exist.
    assert payload.get("detail") != "Not Found"


def test_same_idempotency_key_returns_same_reservation() -> None:
    headers = {
        "Idempotency-Key": "test-idempotency-001",
    }

    request_body = {
        "item_id": "item-001",
        "quantity": 2,
    }

    first_response = client.post(
        "/inventory/reservations",
        headers=headers,
        json=request_body,
    )

    second_response = client.post(
        "/inventory/reservations",
        headers=headers,
        json=request_body,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201

    assert (
        first_response.json()["reservation_id"]
        == second_response.json()["reservation_id"]
    )

    assert first_response.json() == second_response.json()


def test_missing_idempotency_key_returns_422() -> None:
    response = client.post(
        "/inventory/reservations",
        json={
            "item_id": "item-001",
            "quantity": 1,
        },
    )

    assert response.status_code == 422


def test_existing_health_endpoint_still_works() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}