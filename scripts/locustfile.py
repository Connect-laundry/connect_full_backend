"""
Simame (Laundry Connect) - Comprehensive Load & Resilience Test Suite
Simulates realistic KNUST campus user journeys:
 - Discovery & search
 - Laundry detail & menu browsing
 - Real-time price estimation
 - Order creation with idempotency keys
 - Payment status polling & notifications
 - Owner dashboard order listing
"""

import json
import random
import time
import uuid
from locust import HttpUser, task, between, events, tag


class CustomerUser(HttpUser):
    wait_time = between(1, 4)

    def on_start(self):
        """Simulate user auth or guest discovery."""
        self.auth_token = None
        self.user_id = str(uuid.uuid4())
        self.laundry_ids = []
        self.item_ids = []

        # Bootstrap: fetch public laundries
        res = self.client.get("/api/v1/laundries/?limit=10", name="[Public] Discover Laundries")
        if res.status_code == 200:
            data = res.json()
            results = data.get("results", [])
            self.laundry_ids = [l["id"] for l in results if "id" in l]

    @tag("browse", "baseline")
    @task(10)
    def browse_laundries(self):
        """Browse approved campus laundries with location filters."""
        lat = 6.6745 + (random.uniform(-0.01, 0.01))
        lng = -1.5716 + (random.uniform(-0.01, 0.01))
        self.client.get(
            f"/api/v1/laundries/?latitude={lat:.4f}&longitude={lng:.4f}&ordering=distance",
            name="[Browse] Geo-Sorted Laundries",
        )

    @tag("browse", "baseline")
    @task(6)
    def view_laundry_details(self):
        """View specific laundry profile and pricing catalogue."""
        if not self.laundry_ids:
            return
        laundry_id = random.choice(self.laundry_ids)
        self.client.get(
            f"/api/v1/laundries/{laundry_id}/",
            name="[Browse] Laundry Details",
        )

    @tag("booking", "load")
    @task(4)
    def calculate_price_estimate(self):
        """Simulate shopping cart price estimation."""
        if not self.laundry_ids:
            return
        laundry_id = random.choice(self.laundry_ids)
        payload = {
            "laundry": laundry_id,
            "pickup_lat": 6.6745,
            "pickup_lng": -1.5716,
            "items": [
                {"item": str(uuid.uuid4()), "service_type": str(uuid.uuid4()), "quantity": random.randint(1, 5)}
            ]
        }
        self.client.post(
            "/api/v1/booking/estimate/",
            json=payload,
            name="[Booking] Price Estimate",
        )

    @tag("booking", "load", "burst")
    @task(2)
    def create_order_idempotent(self):
        """Simulate order placement with unique idempotency key."""
        if not self.laundry_ids:
            return
        laundry_id = random.choice(self.laundry_ids)
        idempotency_key = f"locust_{uuid.uuid4()}"
        payload = {
            "laundry": laundry_id,
            "pickup_address": "Unity Hall, Room 45, KNUST",
            "delivery_address": "Unity Hall, Room 45, KNUST",
            "pickup_date": "2026-08-20T10:00:00Z",
            "payment_method": "CARD",
            "pricing_mode": "BY_ITEM",
            "items": []
        }
        headers = {
            "X-Idempotency-Key": idempotency_key,
        }
        self.client.post(
            "/api/v1/booking/create/",
            json=payload,
            headers=headers,
            name="[Booking] Place Order (Idempotent)",
        )

    @tag("health")
    @task(1)
    def health_check(self):
        """Periodic readiness / health check verification."""
        self.client.get("/readiness/", name="[System] Readiness Check")


class OwnerUser(HttpUser):
    wait_time = between(2, 6)

    @tag("owner")
    @task(5)
    def list_orders(self):
        """Owner app polling active orders."""
        self.client.get("/api/v1/orders/?limit=20", name="[Owner] Orders List")

    @tag("owner")
    @task(2)
    def check_earnings(self):
        """Owner app checking settlements and earnings."""
        self.client.get("/api/v1/payments/settlements/", name="[Owner] Settlements")
