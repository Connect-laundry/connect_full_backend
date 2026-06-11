"""Tests for the admin-panel role-change audit logging."""
import logging

import pytest

from users.admin import UserAdmin
from users.models import User
from django.contrib.admin.sites import AdminSite


class _Req:
    """Minimal stand-in for the admin request (only needs .user)."""

    def __init__(self, user):
        self.user = user


@pytest.fixture
def admin_instance():
    return UserAdmin(User, AdminSite())


@pytest.fixture
def acting_admin(db):
    return User.objects.create_superuser(
        email='root@example.com', phone='233900000000', password='StrongPass123!'
    )


@pytest.mark.django_db
class TestAdminRoleAudit:
    def test_escalation_to_admin_logs_warning(self, admin_instance, acting_admin, caplog):
        target = User.objects.create_user(
            email='target@example.com', phone='233900000001',
            password='StrongPass123!', role=User.Role.CUSTOMER,
        )
        target.role = User.Role.ADMIN
        with caplog.at_level(logging.WARNING):
            admin_instance.save_model(_Req(acting_admin), target, form=None, change=True)

        target.refresh_from_db()
        assert target.role == User.Role.ADMIN
        records = [r for r in caplog.records if getattr(r, 'event', None) == 'admin_user_role_changed']
        assert len(records) == 1
        rec = records[0]
        assert rec.levelno == logging.WARNING
        assert rec.new_role == 'ADMIN'
        assert rec.previous_role == 'CUSTOMER'
        assert rec.actor_email == 'root@example.com'
        assert rec.target_email == 'target@example.com'

    def test_non_admin_role_change_logs_info(self, admin_instance, acting_admin, caplog):
        target = User.objects.create_user(
            email='target2@example.com', phone='233900000002',
            password='StrongPass123!', role=User.Role.CUSTOMER,
        )
        target.role = User.Role.OWNER
        with caplog.at_level(logging.INFO):
            admin_instance.save_model(_Req(acting_admin), target, form=None, change=True)

        records = [r for r in caplog.records if getattr(r, 'event', None) == 'admin_user_role_changed']
        assert len(records) == 1
        assert records[0].levelno == logging.INFO
        assert records[0].new_role == 'OWNER'

    def test_no_role_change_logs_nothing(self, admin_instance, acting_admin, caplog):
        target = User.objects.create_user(
            email='target3@example.com', phone='233900000003',
            password='StrongPass123!', role=User.Role.CUSTOMER,
        )
        target.first_name = 'Renamed'  # change something other than role
        with caplog.at_level(logging.INFO):
            admin_instance.save_model(_Req(acting_admin), target, form=None, change=True)

        records = [r for r in caplog.records if getattr(r, 'event', None) == 'admin_user_role_changed']
        assert records == []
