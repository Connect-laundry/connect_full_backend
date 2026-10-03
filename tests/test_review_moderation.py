"""Reviews are published on the website, so App Store Guideline 1.2 needs
objectionable language filtered before a review is posted."""
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from laundries.models.laundry import Laundry
from laundries.models.review import Review
from laundries.services.content_filter import contains_objectionable_language
from ordering.models import Order
from users.models import User


@pytest.mark.parametrize('text', [
    'This place is shit', 'f*ck this laundry', 'F U C K', 'sh1t service',
    'you bastards', 'total bullsh!t', 'fuuuck no', 'B!TCH',
])
def test_flags_profanity_and_common_disguises(text):
    assert contains_objectionable_language(text)


@pytest.mark.parametrize('text', [
    '', 'Great service, fast and clean', 'Assessment: excellent',
    'Scunthorpe branch was nice', 'They passed my class shirts back crisp',
    'Dickson was our driver', 'Hancock street pickup', 'Sooo good!!!',
])
def test_leaves_ordinary_reviews_alone(text):
    assert not contains_objectionable_language(text)


@pytest.mark.django_db
class TestReviewEndpoint:
    @pytest.fixture
    def setup(self):
        owner = User.objects.create_user(
            email='mod-owner@example.com', phone='233555960001', password='StrongPass123!',
            role=User.Role.OWNER)
        customer = User.objects.create_user(
            email='mod-customer@example.com', phone='233555960002', password='StrongPass123!')
        laundry = Laundry.objects.create(
            name='Moderated Laundry', address='Accra', latitude=5.6, longitude=-0.18,
            phone_number='0240009601', owner=owner, status=Laundry.ApprovalStatus.APPROVED)
        Order.objects.create(
            user=customer, laundry=laundry, status=Order.Status.COMPLETED,
            pickup_date=timezone.now() - timedelta(days=2))
        client = APIClient()
        client.force_authenticate(user=customer)
        return client, laundry

    def test_rejects_an_offensive_comment(self, setup):
        client, laundry = setup
        response = client.post(
            f'/api/v1/laundries/{laundry.id}/reviews/',
            {'rating': 1, 'comment': 'Worst sh1t ever'}, format='json')

        assert response.status_code == 400
        assert 'offensive language' in response.content.decode()
        assert not Review.objects.filter(laundry=laundry).exists()

    def test_accepts_a_clean_comment(self, setup):
        client, laundry = setup
        response = client.post(
            f'/api/v1/laundries/{laundry.id}/reviews/',
            {'rating': 5, 'comment': 'Clothes came back fresh and on time.'}, format='json')

        assert response.status_code == 201, response.content[:300]
        assert Review.objects.filter(laundry=laundry).count() == 1
