"""Phase 2C tests: failed-login + system/security notifications, and audit-log
coverage for laundry approve/reject and order status changes.
"""
import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.exceptions import AuthenticationFailed

from users.models import User
from laundries.models.laundry import Laundry
from ordering.models import Order
from marketplace.models import Notification, AuditLog
from marketplace.services.notification_service import NotificationService


def _admin_notifs(category=None):
    qs = Notification.objects.filter(audience=Notification.Audience.ADMIN)
    return qs.filter(category=category) if category else qs


@pytest.fixture
def platform_admin(db):
    return User.objects.create_user(
        email='ops2@example.com', phone='233600000000',
        password='StrongPass123!', role=User.Role.ADMIN, is_staff=True,
    )


@pytest.mark.django_db
class TestFailedLoginSecurity:
    def test_failed_login_creates_security_notification_and_audit(self):
        from users.services.auth_service import AuthService
        req = APIRequestFactory().post('/api/v1/auth/login/')
        with pytest.raises(AuthenticationFailed):
            AuthService().login_user(email='ghost@example.com', password='nope', request=req)
        assert _admin_notifs(category='FAILED_LOGIN').exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.SECURITY_EVENT).exists()

    def test_repeated_failures_dedup_to_single_notification(self):
        from users.services.auth_service import AuthService
        req = APIRequestFactory().post('/api/v1/auth/login/')
        for _ in range(3):
            with pytest.raises(AuthenticationFailed):
                AuthService().login_user(email='ghost2@example.com', password='nope', request=req)
        assert _admin_notifs(category='FAILED_LOGIN').filter(
            dedup_key='failed_login:ghost2@example.com').count() == 1


@pytest.mark.django_db
class TestSystemAlert:
    def test_system_alert_creates_high_priority_admin_notification(self):
        n = NotificationService.system_alert(
            'Celery worker down', 'No workers responding', category='CELERY_FAILURE',
            dedup_key='celery_down')
        assert n.audience == Notification.Audience.ADMIN
        assert n.priority == Notification.Priority.HIGH
        # Dedup: a second identical alert does not duplicate.
        n2 = NotificationService.system_alert(
            'Celery worker down', 'still down', category='CELERY_FAILURE', dedup_key='celery_down')
        assert n2.id == n.id


@pytest.mark.django_db
class TestLaundryApprovalAudit:
    def _laundry(self):
        owner = User.objects.create_user(
            email='ow-appr@example.com', phone='233611111111', password='x', role=User.Role.OWNER)
        return Laundry.objects.create(
            name='Audit Shop', owner=owner, address='Accra', latitude=5.6,
            longitude=-0.1, phone_number='0240000010', status=Laundry.ApprovalStatus.PENDING)

    def _call(self, action, admin, laundry, **data):
        from laundries.views.admin_views import AdminLaundryViewSet
        view = AdminLaundryViewSet.as_view({'patch': action})
        req = APIRequestFactory().patch('/x/', data)
        force_authenticate(req, user=admin)
        return view(req, pk=str(laundry.id))

    def test_approve_writes_audit_and_notifies_owner(self, platform_admin, django_capture_on_commit_callbacks):
        laundry = self._laundry()
        # Owner notification/email fire via transaction.on_commit (approval is
        # atomic; side effects run only after a successful commit).
        with django_capture_on_commit_callbacks(execute=True):
            resp = self._call('approve', platform_admin, laundry)
        assert resp.status_code == 200
        assert AuditLog.objects.filter(
            action=AuditLog.Action.LAUNDRY_APPROVED, target_id=str(laundry.id)).exists()
        assert Notification.objects.filter(
            user=laundry.owner, category='LAUNDRY_APPROVED').exists()

    def test_reject_writes_audit_and_notifies_owner(self, platform_admin, django_capture_on_commit_callbacks):
        laundry = self._laundry()
        with django_capture_on_commit_callbacks(execute=True):
            resp = self._call('reject', platform_admin, laundry, reason='Incomplete docs')
        assert resp.status_code == 200
        log = AuditLog.objects.filter(
            action=AuditLog.Action.LAUNDRY_REJECTED, target_id=str(laundry.id)).first()
        assert log is not None
        assert log.metadata.get('reason') == 'Incomplete docs'
        assert Notification.objects.filter(
            user=laundry.owner, category='LAUNDRY_REJECTED').exists()


@pytest.mark.django_db
class TestOrderStatusAudit:
    def test_transition_writes_audit(self, platform_admin):
        owner = User.objects.create_user(
            email='ow-ord@example.com', phone='233622222222', password='x', role=User.Role.OWNER)
        customer = User.objects.create_user(
            email='cust-ord@example.com', phone='233633333333', password='x')
        laundry = Laundry.objects.create(
            name='Order Audit Shop', owner=owner, address='Accra', latitude=5.6,
            longitude=-0.1, phone_number='0240000011')
        order = Order.objects.create(
            user=customer, laundry=laundry, status='PENDING',
            total_amount=10, pickup_date=timezone.now())

        from ordering.services.order_state_machine import OrderStateMachine
        _, ok = OrderStateMachine.transition(order.id, Order.Status.CONFIRMED, customer)
        assert ok
        log = AuditLog.objects.filter(
            action=AuditLog.Action.ORDER_STATUS_CHANGED, target_id=str(order.id)).first()
        assert log is not None
        assert log.metadata.get('to') == 'CONFIRMED'
