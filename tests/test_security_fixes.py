"""Regression tests for the security hardening fixes."""
import io

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from laundries.models.review import Review
from ordering.models import Order


def _review_url(laundry_id):
    return reverse('review_create', kwargs={'laundry_id': laundry_id})


@pytest.mark.django_db
class TestReviewFraud:
    def test_cannot_review_without_completed_order(self, auth_client, sample_laundry):
        resp = auth_client.post(
            _review_url(sample_laundry.id),
            {'rating': 5, 'comment': 'Great'},
            format='json',
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert Review.objects.count() == 0

    def test_pending_order_does_not_grant_review(self, auth_client, sample_order, sample_laundry):
        # sample_order is PENDING by default.
        resp = auth_client.post(
            _review_url(sample_laundry.id),
            {'rating': 4, 'comment': 'Nope'},
            format='json',
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert Review.objects.count() == 0

    def test_can_review_after_completed_order(self, auth_client, sample_order, sample_laundry):
        sample_order.status = Order.Status.COMPLETED
        sample_order.save(update_fields=['status'])
        resp = auth_client.post(
            _review_url(sample_laundry.id),
            {'rating': 5, 'comment': 'Excellent'},
            format='json',
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert Review.objects.count() == 1

    def test_cannot_review_same_laundry_twice(self, auth_client, sample_order, sample_laundry):
        sample_order.status = Order.Status.DELIVERED
        sample_order.save(update_fields=['status'])
        first = auth_client.post(
            _review_url(sample_laundry.id),
            {'rating': 5, 'comment': 'First'},
            format='json',
        )
        assert first.status_code == status.HTTP_201_CREATED
        second = auth_client.post(
            _review_url(sample_laundry.id),
            {'rating': 1, 'comment': 'Second'},
            format='json',
        )
        assert second.status_code == status.HTTP_400_BAD_REQUEST
        assert Review.objects.count() == 1


@pytest.mark.django_db
class TestMediaUploadFolderWhitelist:
    def _png(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buf = io.BytesIO()
        Image.new('RGB', (8, 8), color=(32, 96, 160)).save(buf, format='PNG')
        return SimpleUploadedFile('test.png', buf.getvalue(), content_type='image/png')

    def test_path_traversal_folder_rejected(self, auth_client):
        resp = auth_client.post(
            reverse('media_upload'),
            {'file': self._png(), 'folder': '../../etc'},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_allowed_folder_accepted(self, auth_client, settings):
        settings.DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
        resp = auth_client.post(
            reverse('media_upload'),
            {'file': self._png(), 'folder': 'avatars'},
            format='multipart',
        )
        assert resp.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestPaymentVerifyOwnership:
    def test_other_user_cannot_verify_foreign_payment(self, monkeypatch, sample_order):
        from payments.models import Payment
        from users.models import User
        from payments.services.paystack import PaystackService

        payment = Payment.objects.create(
            user=sample_order.user,
            order=sample_order,
            amount=sample_order.total_amount,
            currency='GHS',
            transaction_reference='ref-owner-123',
            status=Payment.Status.PENDING,
        )

        # Pretend Paystack reports success so we reach the ownership gate.
        monkeypatch.setattr(
            PaystackService,
            'verify_transaction',
            lambda self, reference: {
                'status': True,
                'data': {'status': 'success', 'amount': int(payment.amount * 100), 'currency': 'GHS'},
            },
        )

        attacker = User.objects.create_user(
            email='attacker@example.com', phone='233000000999', password='StrongPass123!'
        )
        client = APIClient()
        client.force_authenticate(user=attacker)
        resp = client.get(reverse('payment_verify', kwargs={'reference': 'ref-owner-123'}))
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        payment.refresh_from_db()
        # Attacker must not have flipped the payment to SUCCESS.
        assert payment.status != Payment.Status.SUCCESS
