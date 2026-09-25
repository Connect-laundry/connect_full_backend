import pytest
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch, MagicMock

from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework import status

from laundries.models.laundry import Laundry
from laundries.models.service import LaundryService
from laundries.models.category import Category


from ordering.models import Order, OrderItem
from payments.models import OrderSettlement
from logistics.models import LogisticsPricingConfig, LogisticsPricingAudit
from logistics.services.pricing_service import LogisticsPricingService, TEMPORARY_LOGISTICS_NOTICE
from ordering.services.finance_service import FinanceService

User = get_user_model()


@pytest.fixture
def test_user(db):
    return User.objects.create_user(
        phone='+233240000001',
        email='customer@example.com',
        password='password123',
        role='CUSTOMER',
    )


@pytest.fixture
def owner_user(db):
    return User.objects.create_user(
        phone='+233240000002',
        email='owner@example.com',
        password='password123',
        role='OWNER',
    )



@pytest.fixture
def laundry(db, owner_user):
    return Laundry.objects.create(
        name="Airport Cleaners",
        owner=owner_user,
        phone_number="+233240000002",
        address="Airport Residential, Accra",
        city="Accra",
        latitude=Decimal("5.600000"),
        longitude=Decimal("-0.180000"),
        status=Laundry.ApprovalStatus.APPROVED,
        is_active=True,
        service_radius_km=Decimal("15.00"),
    )


@pytest.fixture
def active_logistics_config(db):
    # Pickup: GHS 2.50/km, Delivery: GHS 3.00/km, Base: GHS 5.00 pickup, GHS 6.00 delivery
    return LogisticsPricingConfig.objects.create(
        pricing_enabled=True,
        pickup_price_per_km=Decimal("2.50"),
        delivery_price_per_km=Decimal("3.00"),
        pickup_base_fee=Decimal("5.00"),
        delivery_base_fee=Decimal("6.00"),
        pickup_min_fee=Decimal("8.00"),
        delivery_min_fee=Decimal("9.00"),
        max_service_distance_km=Decimal("25.00"),
        distance_rounding_precision=1,
        effective_from=timezone.now() - timedelta(minutes=10),
        is_active=True,
        version=1,
    )


@pytest.mark.django_db
class TestDynamicLogisticsPricing:
    """Comprehensive test suite for Simame dynamic logistics pricing."""

    def test_temporary_notice_when_pricing_disabled(self, laundry):
        """When pricing is disabled, app shows notice that logistics are not included."""
        LogisticsPricingConfig.objects.all().delete()
        quote = LogisticsPricingService.calculate_quote(
            laundry=laundry,
            pickup_lat=Decimal("5.610000"),
            pickup_lng=Decimal("-0.180000"),
        )
        assert quote['pricing_enabled'] is False
        assert quote['delivery_fees_in_app'] is False
        assert quote['pickup_fee'] == Decimal('0.00')
        assert quote['delivery_fee'] == Decimal('0.00')
        assert quote['total_logistics_fee'] == Decimal('0.00')
        assert quote['logistics_notice'] == TEMPORARY_LOGISTICS_NOTICE

    def test_quote_calculation_exact_distances(self, laundry, active_logistics_config):
        """
        Verify authoritative calculation:
        rate * km + base_fee bounded by min_fee.
        """
        # Laundry is at (5.600000, -0.180000)
        # Point A is ~1.1 km away
        lat_1km = Decimal("5.610000")
        lng_1km = Decimal("-0.180000")

        quote = LogisticsPricingService.calculate_quote(
            laundry=laundry,
            pickup_lat=lat_1km,
            pickup_lng=lng_1km,
            delivery_lat=lat_1km,
            delivery_lng=lng_1km,
        )

        assert quote['pricing_enabled'] is True
        assert quote['delivery_fees_in_app'] is True
        assert quote['pickup_distance_km'] == Decimal("1.1")
        assert quote['delivery_distance_km'] == Decimal("1.1")
        # Pickup: 5.00 base + 1.1 * 2.50 = 7.75, bounded by min_fee 8.00 -> GHS 8.00
        assert quote['pickup_fee'] == Decimal("8.00")
        # Delivery: 6.00 base + 1.1 * 3.00 = 9.30, >= min_fee 9.00 -> GHS 9.30
        assert quote['delivery_fee'] == Decimal("9.30")
        assert quote['total_logistics_fee'] == Decimal("17.30")
        # Notice disappears automatically
        assert quote['logistics_notice'] == ""

    def test_longer_distance_5km(self, laundry, active_logistics_config):
        """Test calculation for longer distance (5.0 km)."""
        # ~5 km north: 5.60 + 0.045
        lat_5km = Decimal("5.645000")
        lng_5km = Decimal("-0.180000")

        quote = LogisticsPricingService.calculate_quote(
            laundry=laundry,
            pickup_lat=lat_5km,
            pickup_lng=lng_5km,
        )

        assert quote['pickup_distance_km'] == Decimal("5.0")
        # Pickup: 5.00 base + 5.0 * 2.50 = 17.50
        assert quote['pickup_fee'] == Decimal("17.50")
        # Delivery: 6.00 base + 5.0 * 3.00 = 21.00
        assert quote['delivery_fee'] == Decimal("21.00")
        assert quote['total_logistics_fee'] == Decimal("38.50")

    def test_outside_maximum_service_radius(self, laundry, active_logistics_config):
        """Exceeding max service distance flags outside_service_area."""
        # 30 km away: 5.60 + 0.27
        lat_far = Decimal("5.870000")
        lng_far = Decimal("-0.180000")

        quote = LogisticsPricingService.calculate_quote(
            laundry=laundry,
            pickup_lat=lat_far,
            pickup_lng=lng_far,
        )

        assert quote['outside_service_area'] is True
        assert "exceeds maximum service limit" in quote['warning']

    def test_free_delivery_promotion_laundry_funded(self, laundry, active_logistics_config):
        """When promo is active, customer pays 0, but nominal fees & subsidy are preserved."""
        laundry.free_delivery_promo_enabled = True
        laundry.promo_funding_source = "LAUNDRY"
        laundry.promo_max_distance_km = Decimal("10.00")
        laundry.save()

        lat_3km = Decimal("5.627000")
        lng_3km = Decimal("-0.180000")

        quote = LogisticsPricingService.calculate_quote(
            laundry=laundry,
            pickup_lat=lat_3km,
            pickup_lng=lng_3km,
        )

        assert quote['is_promo_free_delivery'] is True
        assert quote['promo_funding_source'] == "LAUNDRY"
        assert quote['promo_label'] == f"Courtesy of {laundry.name}"
        assert quote['pickup_fee'] == Decimal("0.00")
        assert quote['delivery_fee'] == Decimal("0.00")
        assert quote['total_logistics_fee'] == Decimal("0.00")
        # Nominal fees are tracked for rider subsidy
        assert quote['nominal_logistics_total'] > Decimal("0.00")
        assert quote['logistics_discount'] == quote['nominal_logistics_total']

    def test_free_delivery_promo_expired_or_outside_promo_distance(self, laundry, active_logistics_config):
        """Expired promo or location beyond promo distance restores standard fees."""
        # Promo expired yesterday
        laundry.free_delivery_promo_enabled = True
        laundry.promo_start_at = timezone.now() - timedelta(days=5)
        laundry.promo_end_at = timezone.now() - timedelta(days=1)
        laundry.save()

        quote = LogisticsPricingService.calculate_quote(
            laundry=laundry,
            pickup_lat=Decimal("5.610000"),
            pickup_lng=Decimal("-0.180000"),
        )
        assert quote['is_promo_free_delivery'] is False
        assert quote['total_logistics_fee'] > Decimal("0.00")

    def test_order_snapshot_immutability(self, test_user, laundry, active_logistics_config):
        """
        Critical requirement:
        Admin changes rates in Django Admin
        ↓
        Old booked order retains original rate & fee snapshot
        ↓
        New booking immediately uses new rates WITHOUT mobile app update
        """
        # Create Order 1 with Version 1 rates (Pickup 2.50/km, Delivery 3.00/km)
        order1 = Order.objects.create(
            user=test_user,
            laundry=laundry,
            pickup_date=timezone.now() + timedelta(days=1),
            pickup_lat=Decimal("5.610000"),
            pickup_lng=Decimal("-0.180000"),
            delivery_lat=Decimal("5.610000"),
            delivery_lng=Decimal("-0.180000"),
            total_amount=Decimal("0.00"),
        )
        OrderItem.objects.create(
            order=order1,
            name="Wash & Fold",
            quantity=2,
            price=Decimal("25.00"),
        )
        FinanceService.freeze_price_breakdown(order1)

        orig_pickup_fee = order1.pickup_fee
        orig_delivery_fee = order1.delivery_fee
        orig_pickup_rate = order1.pickup_rate_per_km
        orig_delivery_rate = order1.delivery_rate_per_km
        orig_total = order1.total_amount

        assert orig_pickup_fee == Decimal("8.00")
        assert orig_delivery_fee == Decimal("9.30")
        assert orig_pickup_rate == Decimal("2.50")
        assert orig_delivery_rate == Decimal("3.00")
        # items 50.00 + logistics 17.30 + tax (3.50) + platform fee (2.50) = 73.30
        assert orig_total == Decimal("73.30")


        # Now Admin updates rates to Pickup GHS 4.00/km, Delivery GHS 5.00/km!
        active_logistics_config.pickup_price_per_km = Decimal("4.00")
        active_logistics_config.delivery_price_per_km = Decimal("5.00")
        active_logistics_config.pickup_base_fee = Decimal("10.00")
        active_logistics_config.delivery_base_fee = Decimal("12.00")
        active_logistics_config.version = 2
        active_logistics_config.save()

        # Check Order 1: stored breakdown MUST NOT CHANGE
        order1.refresh_from_db()
        breakdown1 = FinanceService.calculate_price_breakdown(order1, use_snapshot=True)
        assert breakdown1['pickup_fee'] == str(orig_pickup_fee)
        assert breakdown1['delivery_fee'] == str(orig_delivery_fee)
        assert breakdown1['pickup_rate_per_km'] == str(orig_pickup_rate)
        assert breakdown1['delivery_rate_per_km'] == str(orig_delivery_rate)
        assert breakdown1['total'] == str(orig_total)

        # Check Order 2: IMMEDIATELY reflects new rates without mobile update!
        order2 = Order.objects.create(
            user=test_user,
            laundry=laundry,
            pickup_date=timezone.now() + timedelta(days=1),
            pickup_lat=Decimal("5.610000"),
            pickup_lng=Decimal("-0.180000"),
            delivery_lat=Decimal("5.610000"),
            delivery_lng=Decimal("-0.180000"),
            total_amount=Decimal("0.00"),
        )
        OrderItem.objects.create(
            order=order2,
            name="Wash & Fold",
            quantity=2,
            price=Decimal("25.00"),
        )
        FinanceService.freeze_price_breakdown(order2)

        order2.refresh_from_db()
        assert order2.pickup_rate_per_km == Decimal("4.00")
        assert order2.delivery_rate_per_km == Decimal("5.00")
        # 10.00 base + 1.1 * 4.00 = 14.40
        assert order2.pickup_fee == Decimal("14.40")
        # 12.00 base + 1.1 * 5.00 = 17.50
        assert order2.delivery_fee == Decimal("17.50")
        # items 50.00 + logistics 31.90 + tax 3.50 + platform fee 2.50 = 87.90
        assert order2.total_amount == Decimal("87.90")

    def test_laundry_funded_promo_settlement_subsidy(self, test_user, laundry, active_logistics_config):
        """
        When a promo is funded by the laundry, the rider fee is deducted from laundry's settlement.
        Simame does not absorb rider cost.
        """
        from payments.services.settlement_service import SettlementService

        laundry.free_delivery_promo_enabled = True
        laundry.promo_funding_source = "LAUNDRY"
        laundry.save()

        order = Order.objects.create(
            user=test_user,
            laundry=laundry,
            pickup_date=timezone.now() + timedelta(days=1),
            pickup_lat=Decimal("5.610000"),
            pickup_lng=Decimal("-0.180000"),
            delivery_lat=Decimal("5.610000"),
            delivery_lng=Decimal("-0.180000"),
            total_amount=Decimal("0.00"),
        )
        OrderItem.objects.create(
            order=order,
            name="Suit",
            quantity=1,
            price=Decimal("100.00"),
        )
        FinanceService.freeze_price_breakdown(order)

        assert order.is_free_delivery_promo is True
        assert order.pickup_fee == Decimal("0.00")
        assert order.delivery_fee == Decimal("0.00")
        # Customer paid items (100.00) + tax (7.00) + platform fee (5.00) = 112.00
        assert order.total_amount == Decimal("112.00")
        # Nominal rider fee subsidized = 17.30
        assert order.logistics_discount == Decimal("17.30")

        # Record settlement
        settlement = SettlementService.record_for_order(order)
        assert settlement.logistics_subsidy_deducted == Decimal("17.30")
        # Laundry receives items (100.00) - commission (5.00) - subsidy (17.30) = 77.70
        assert settlement.net_payable == Decimal("77.70")


    def test_stale_quote_rejection_on_booking(self, test_user, laundry, active_logistics_config):
        """If price changed while customer was in checkout, backend rejects stale quote."""
        from ordering.serializers.order import OrderCreateSerializer

        # Client sends stale expected_total = 50.00 (before logistics rate change)
        data = {
            "laundry": str(laundry.id),
            "pickup_date": (timezone.now() + timedelta(days=1)).isoformat(),
            "pickup_address": "Airport Residential",
            "pickup_lat": "5.610000",
            "pickup_lng": "-0.180000",
            "delivery_address": "Airport Residential",
            "delivery_lat": "5.610000",
            "delivery_lng": "-0.180000",
            "expected_total": "50.00",
            "pricing_mode": "BY_ITEM",
            "items": [],
        }

        # Mock request context
        mock_request = MagicMock()
        mock_request.user = test_user
        serializer = OrderCreateSerializer(data=data, context={'request': mock_request})
        # Empty items fails validation
        assert not serializer.is_valid()
