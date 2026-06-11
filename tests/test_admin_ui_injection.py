"""Verifies the Admin Operations Center UI assets are injected into every
Unfold admin page (so the ⌘K search + notification bell actually load).
"""
import pytest
from django.test import Client

from users.models import User


@pytest.fixture
def admin_urls(settings):
    # test_settings does not import the full UNFOLD dict; pull the real project
    # config so this test exercises the actual STYLES/SCRIPTS injection.
    import config.settings as base
    settings.ROOT_URLCONF = 'config.urls'
    settings.UNFOLD = base.UNFOLD
    return settings


@pytest.mark.django_db
class TestAdminUIInjection:
    def _staff(self):
        return User.objects.create_user(
            email='ui-admin@example.com', phone='233700000000',
            password='StrongPass123!', role=User.Role.ADMIN,
            is_staff=True, is_superuser=True,
        )

    def test_admin_index_injects_search_and_bell_assets(self, admin_urls):
        client = Client()
        client.force_login(self._staff())
        resp = client.get('/admin/')
        assert resp.status_code == 200
        html = resp.content.decode()
        assert 'admin_ops/admin_ops.js' in html
        assert 'admin_ops/admin_ops.css' in html

    def test_assets_not_served_to_anonymous(self, admin_urls):
        resp = Client().get('/admin/')
        assert resp.status_code in (301, 302)
