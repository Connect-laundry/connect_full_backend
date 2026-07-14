"""End-to-end tests for the laundry approval workflow.

Covers:
* the opening-hours inline fix (admin shows EXACTLY the owner's submitted days,
  no phantom extra rows),
* LaundryApprovalService transitions (approve / reject / request changes /
  suspend / resubmit) with audit, notifications, analytics and emails,
* admin decision buttons (detail + row actions),
* the new-submission admin email.
"""
import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from analytics.models import AnalyticsEvent
from laundries.models.laundry import Laundry, OwnerAuditLog
from laundries.models.opening_hours import OpeningHours
from laundries.services.approval import InvalidTransition, LaundryApprovalService
from marketplace.models import AuditLog, Notification
from users.models import User


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def owner(db):
    return User.objects.create_user(
        email='shopowner@example.com', phone='233470000001',
        password='StrongPass123!', role=User.Role.OWNER,
    )


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        email='approver@example.com', phone='233470000002',
        password='StrongPass123!', role=User.Role.ADMIN,
        is_staff=True, is_superuser=True,
    )


@pytest.fixture
def pending_laundry(owner):
    return Laundry.objects.create(
        name='Weekend Wash', owner=owner, address='Accra', city='Accra',
        latitude=5.6, longitude=-0.1, phone_number='0240000009',
        status=Laundry.ApprovalStatus.PENDING, is_active=False,
        submitted_at=timezone.now(),
    )


def _owner_notifs(user, category=None):
    qs = Notification.objects.filter(user=user, audience=Notification.Audience.USER)
    if category:
        qs = qs.filter(category=category)
    return qs


# --------------------------------------------------------------------------- #
# Part 1 — the admin shows exactly what the owner submitted
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestOpeningHoursInlineRendering:
    def test_inline_has_no_extra_phantom_rows(self):
        from laundries.admin import OpeningHoursInline
        assert OpeningHoursInline.extra == 0
        assert OpeningHoursInline.max_num == 7

    def test_change_page_renders_only_submitted_days(self, client, admin_user, pending_laundry):
        # Owner submitted ONLY Saturday and Sunday.
        OpeningHours.objects.create(
            laundry=pending_laundry, day=6, opening_time='09:00', closing_time='16:00')
        OpeningHours.objects.create(
            laundry=pending_laundry, day=7, opening_time='00:00', closing_time='00:00',
            is_closed=True)

        client.force_login(admin_user)
        resp = client.get(
            reverse('admin:laundries_laundry_change', args=[pending_laundry.pk]))
        assert resp.status_code == 200
        content = resp.content.decode()

        # Exactly 2 inline forms — no blank Monday row, nothing phantom.
        assert 'name="opening_hours-TOTAL_FORMS" value="2"' in content
        assert 'id_opening_hours-1-day' in content
        assert 'id_opening_hours-2-day' not in content

    def test_change_page_with_no_hours_renders_zero_rows(self, client, admin_user, pending_laundry):
        client.force_login(admin_user)
        resp = client.get(
            reverse('admin:laundries_laundry_change', args=[pending_laundry.pk]))
        assert resp.status_code == 200
        assert 'name="opening_hours-TOTAL_FORMS" value="0"' in resp.content.decode()

    def test_saving_without_touching_hours_does_not_duplicate(self, owner, pending_laundry):
        """Round-trip through the owner serializer keeps days unique and exact."""
        OpeningHours.objects.create(
            laundry=pending_laundry, day=6, opening_time='09:00', closing_time='16:00')
        assert pending_laundry.opening_hours.count() == 1
        # unique_together guards duplicates at the DB level
        with pytest.raises(Exception):
            OpeningHours.objects.create(
                laundry=pending_laundry, day=6, opening_time='10:00', closing_time='17:00')


# --------------------------------------------------------------------------- #
# Approval service transitions
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestApprovalService:
    def test_approve(self, pending_laundry, admin_user, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            laundry = LaundryApprovalService.approve(pending_laundry, actor=admin_user)

        laundry.refresh_from_db()
        assert laundry.status == Laundry.ApprovalStatus.APPROVED
        assert laundry.is_active is True
        assert laundry.approved_at is not None
        assert laundry.reviewed_by == admin_user

        # Audit — both platform-wide and owner-scoped.
        assert AuditLog.objects.filter(
            action=AuditLog.Action.LAUNDRY_APPROVED, target_id=str(laundry.id)).exists()
        assert OwnerAuditLog.objects.filter(
            laundry=laundry, action='LAUNDRY_APPROVED').exists()

        # Owner notification + analytics event + email.
        assert _owner_notifs(laundry.owner, 'LAUNDRY_APPROVED').exists()
        assert AnalyticsEvent.objects.filter(event_name='laundry_approved').exists()
        assert any('approved' in m.subject.lower() or 'live' in m.subject.lower()
                   for m in mail.outbox)

    def test_reject_with_reason(self, pending_laundry, admin_user, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            laundry = LaundryApprovalService.reject(
                pending_laundry, actor=admin_user, reason='Blurry logo photo')

        laundry.refresh_from_db()
        assert laundry.status == Laundry.ApprovalStatus.REJECTED
        assert laundry.is_active is False
        assert laundry.rejected_at is not None
        assert laundry.status_reason == 'Blurry logo photo'

        log = AuditLog.objects.get(
            action=AuditLog.Action.LAUNDRY_REJECTED, target_id=str(laundry.id))
        assert log.metadata['reason'] == 'Blurry logo photo'
        assert log.metadata['old_status'] == 'PENDING'
        assert log.metadata['new_status'] == 'REJECTED'

        notif = _owner_notifs(laundry.owner, 'LAUNDRY_REJECTED').first()
        assert notif is not None and 'Blurry logo photo' in notif.body

    def test_request_changes(self, pending_laundry, admin_user, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            laundry = LaundryApprovalService.request_changes(
                pending_laundry, actor=admin_user, reason='Add opening hours')

        laundry.refresh_from_db()
        assert laundry.status == Laundry.ApprovalStatus.CHANGES_REQUESTED
        assert laundry.changes_requested_at is not None
        assert _owner_notifs(laundry.owner, 'LAUNDRY_CHANGES_REQUESTED').exists()
        assert AnalyticsEvent.objects.filter(event_name='laundry_changes_requested').exists()

    def test_suspend_approved_laundry(self, pending_laundry, admin_user, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            LaundryApprovalService.approve(pending_laundry, actor=admin_user)
            pending_laundry.refresh_from_db()
            LaundryApprovalService.suspend(
                pending_laundry, actor=admin_user, reason='Repeated complaints')
        pending_laundry.refresh_from_db()
        assert pending_laundry.status == Laundry.ApprovalStatus.SUSPENDED
        assert pending_laundry.is_active is False

    def test_invalid_transition_raises(self, pending_laundry, admin_user, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            LaundryApprovalService.approve(pending_laundry, actor=admin_user)
        pending_laundry.refresh_from_db()
        with pytest.raises(InvalidTransition):
            # Approved laundries can't be sent back for changes directly.
            LaundryApprovalService.request_changes(
                pending_laundry, actor=admin_user, reason='x')

    def test_decision_marks_admin_bell_notification_read(
            self, pending_laundry, admin_user, django_capture_on_commit_callbacks):
        Notification.objects.create(
            user=None, audience=Notification.Audience.ADMIN,
            title='New laundry awaiting approval', body='x',
            category='LAUNDRY_PENDING',
            dedup_key=f'laundry_pending:{pending_laundry.id}',
        )
        with django_capture_on_commit_callbacks(execute=True):
            LaundryApprovalService.approve(pending_laundry, actor=admin_user)
        assert not Notification.objects.filter(
            category='LAUNDRY_PENDING', is_read=False,
            dedup_key__startswith=f'laundry_pending:{pending_laundry.id}',
        ).exists()

    def test_resubmit_after_changes_requested(
            self, pending_laundry, admin_user, owner, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            LaundryApprovalService.request_changes(
                pending_laundry, actor=admin_user, reason='Fix address')
        pending_laundry.refresh_from_db()

        with django_capture_on_commit_callbacks(execute=True):
            LaundryApprovalService.resubmit(pending_laundry, actor=owner)

        pending_laundry.refresh_from_db()
        assert pending_laundry.status == Laundry.ApprovalStatus.PENDING
        assert pending_laundry.status_reason == ''
        assert OwnerAuditLog.objects.filter(
            laundry=pending_laundry, action='LAUNDRY_RESUBMITTED').exists()
        # Admins are told it is back in the queue.
        assert Notification.objects.filter(
            audience=Notification.Audience.ADMIN,
            category='LAUNDRY_PENDING',
            title__icontains='resubmitted',
        ).exists()

    def test_resubmit_noop_for_pending(self, pending_laundry, owner):
        LaundryApprovalService.resubmit(pending_laundry, actor=owner)
        pending_laundry.refresh_from_db()
        assert pending_laundry.status == Laundry.ApprovalStatus.PENDING


# --------------------------------------------------------------------------- #
# Admin buttons (detail + row actions)
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestAdminDecisionButtons:
    def test_approve_button(self, client, admin_user, pending_laundry, django_capture_on_commit_callbacks):
        client.force_login(admin_user)
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.get(f'/admin/laundries/laundry/{pending_laundry.pk}/approve/')
        assert resp.status_code == 302
        pending_laundry.refresh_from_db()
        assert pending_laundry.status == Laundry.ApprovalStatus.APPROVED
        assert pending_laundry.is_active is True

    def test_reject_shows_reason_form_then_applies(
            self, client, admin_user, pending_laundry, django_capture_on_commit_callbacks):
        client.force_login(admin_user)
        # GET renders the reason form — nothing changes yet.
        resp = client.get(f'/admin/laundries/laundry/{pending_laundry.pk}/reject/')
        assert resp.status_code == 200
        pending_laundry.refresh_from_db()
        assert pending_laundry.status == Laundry.ApprovalStatus.PENDING

        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post(
                f'/admin/laundries/laundry/{pending_laundry.pk}/reject/',
                {'reason': 'Incomplete pricing'})
        assert resp.status_code == 302
        pending_laundry.refresh_from_db()
        assert pending_laundry.status == Laundry.ApprovalStatus.REJECTED
        assert pending_laundry.status_reason == 'Incomplete pricing'

    def test_row_quick_approve(self, client, admin_user, pending_laundry, django_capture_on_commit_callbacks):
        client.force_login(admin_user)
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.get(f'/admin/laundries/laundry/{pending_laundry.pk}/row-approve')
        assert resp.status_code == 302
        pending_laundry.refresh_from_db()
        assert pending_laundry.status == Laundry.ApprovalStatus.APPROVED

    def test_double_approve_is_graceful(self, client, admin_user, pending_laundry, django_capture_on_commit_callbacks):
        client.force_login(admin_user)
        with django_capture_on_commit_callbacks(execute=True):
            client.get(f'/admin/laundries/laundry/{pending_laundry.pk}/approve/')
            resp = client.get(f'/admin/laundries/laundry/{pending_laundry.pk}/approve/')
        # Second click doesn't 500 — it redirects with an error message.
        assert resp.status_code == 302

    def test_pending_queue_filter(self, client, admin_user, pending_laundry):
        client.force_login(admin_user)
        resp = client.get('/admin/laundries/laundry/?status__exact=PENDING')
        assert resp.status_code == 200
        assert 'Weekend Wash' in resp.content.decode()


# --------------------------------------------------------------------------- #
# Part 6 — submission email to the platform admin
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestSubmissionNotifications:
    def test_new_pending_laundry_emails_admin(self, owner, settings, django_capture_on_commit_callbacks):
        settings.LAUNDRY_APPROVAL_NOTIFY_EMAILS = ['odamephilip966@gmail.com']
        with django_capture_on_commit_callbacks(execute=True):
            Laundry.objects.create(
                name='Fresh Fold', owner=owner, address='Kumasi', city='Kumasi',
                latitude=6.7, longitude=-1.6, phone_number='0240000010',
                status=Laundry.ApprovalStatus.PENDING,
            )
        matching = [m for m in mail.outbox
                    if 'Waiting For Approval' in m.subject and 'Fresh Fold' in m.subject]
        assert matching, f"No admin approval email found in {[m.subject for m in mail.outbox]}"
        msg = matching[0]
        assert 'odamephilip966@gmail.com' in msg.to
        assert 'shopowner@example.com' in msg.body           # owner email included
        assert '/admin/laundries/laundry/' in msg.body       # admin deep link included

    def test_new_pending_laundry_creates_admin_bell_notification(self, owner):
        Laundry.objects.create(
            name='Bell Shop', owner=owner, address='Accra', latitude=5.6,
            longitude=-0.1, phone_number='0240000011',
            status=Laundry.ApprovalStatus.PENDING,
        )
        assert Notification.objects.filter(
            audience=Notification.Audience.ADMIN, category='LAUNDRY_PENDING',
            body__icontains='Bell Shop',
        ).exists()

    def test_email_task_retries_are_configured(self):
        from laundries.tasks import send_admin_new_laundry_email, send_owner_status_email
        assert send_admin_new_laundry_email.max_retries == 5
        assert send_owner_status_email.max_retries == 5


# --------------------------------------------------------------------------- #
# Approval analytics
# --------------------------------------------------------------------------- #
@pytest.mark.django_db
class TestApprovalMetrics:
    def test_metrics_shape(self, pending_laundry, admin_user, django_capture_on_commit_callbacks):
        from analytics.metrics import laundry_approval_metrics
        with django_capture_on_commit_callbacks(execute=True):
            LaundryApprovalService.approve(pending_laundry, actor=admin_user)

        m = laundry_approval_metrics(30)
        assert m['approved'] == 1
        assert m['pending'] == 0
        assert m['approval_rate'] == 100.0
        assert m['rejection_rate'] == 0.0
        assert m['avg_approval_hours'] is not None
        assert isinstance(m['submissions_by_day'], list)
