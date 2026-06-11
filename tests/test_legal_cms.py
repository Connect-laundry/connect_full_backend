import zipfile

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from marketplace.models import AuditLog, LegalPage, UserLegalAcceptance
from marketplace.services.legal import publish_legal_page
from users.models import User


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        email='legal-admin@example.com',
        phone='233900000001',
        password='StrongPass123!',
        role=User.Role.ADMIN,
        is_staff=True,
    )


@pytest.fixture
def customer(db):
    return User.objects.create_user(
        email='legal-customer@example.com',
        phone='233900000002',
        password='StrongPass123!',
    )


@pytest.fixture
def admin_client(admin_user):
    client = APIClient()
    client.force_authenticate(admin_user)
    return client


def create_page(**overrides):
    now = timezone.now()
    data = {
        'title': 'Privacy Policy',
        'slug': 'privacy-policy',
        'document_type': 'PRIVACY_POLICY',
        'content_markdown': '# Privacy\n\nWe protect **data**.\n\n<script>alert(1)</script>',
        'version_number': '1.0',
        'effective_date': now,
        'published_at': now,
        'is_published': True,
        'is_active': True,
        'is_public': True,
    }
    data.update(overrides)
    return LegalPage.objects.create(**data)


@pytest.mark.django_db
def test_public_legal_api_returns_latest_sanitized_document():
    create_page(version_number='1.0', is_active=False)
    latest = create_page(
        title='Privacy Policy v2',
        version_number='2.0',
        content_markdown='# Privacy\n\nNo raw <script>alert(1)</script> here.',
    )

    resp = APIClient().get(reverse('legal_detail', kwargs={'slug': 'privacy-policy'}))

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data['data']['id'] == str(latest.id)
    assert resp.data['data']['version_number'] == '2.0'
    assert '<script>' not in resp.data['data']['content_html']
    assert '&lt;script&gt;' in resp.data['data']['content_html']


@pytest.mark.django_db
def test_admin_create_publish_and_immutable_update(admin_client):
    create_resp = admin_client.post(reverse('legal_admin_create'), {
        'title': 'Terms of Service',
        'slug': 'terms-of-service',
        'document_type': 'TERMS_OF_SERVICE',
        'content_markdown': '# Terms\n\nBe kind.',
        'version_number': '1.0',
    }, format='json')
    assert create_resp.status_code == status.HTTP_201_CREATED
    page_id = create_resp.data['data']['id']

    publish_resp = admin_client.post(reverse('legal_admin_publish'), {'id': page_id}, format='json')
    assert publish_resp.status_code == status.HTTP_200_OK
    assert publish_resp.data['data']['is_published'] is True

    patch_resp = admin_client.patch(reverse('legal_admin_detail', kwargs={'pk': page_id}), {
        'content_markdown': '# Terms\n\nUpdated terms.',
    }, format='json')
    assert patch_resp.status_code == status.HTTP_200_OK
    assert patch_resp.data['data']['new_version_created'] is True
    assert patch_resp.data['data']['page']['version_number'] == '1.1'

    original = LegalPage.objects.get(pk=page_id)
    assert original.content_markdown == '# Terms\n\nBe kind.'
    assert AuditLog.objects.filter(action=AuditLog.Action.LEGAL_DOCUMENT_PUBLISHED).exists()


@pytest.mark.django_db
def test_publishing_new_version_deactivates_previous_current(admin_user):
    v1 = create_page(version_number='1.0')
    v2 = v1.clone_as_new_version(
        actor=admin_user,
        changes={'content_markdown': '# Privacy\n\nChanged.', 'version_number': '1.1'},
    )

    publish_legal_page(v2, actor=admin_user)

    v1.refresh_from_db()
    v2.refresh_from_db()
    assert v1.is_active is False
    assert v2.is_active is True
    assert v2.is_published is True


@pytest.mark.django_db
def test_user_acceptance_is_idempotent_and_reacceptance_detected(customer):
    page = create_page(requires_user_reacceptance=True)
    client = APIClient()
    client.force_authenticate(customer)

    first = client.post(reverse('legal_user_acceptance'), {
        'slug': 'privacy-policy',
        'platform': 'mobile',
        'app_version': '1.0.0',
    }, format='json')
    second = client.post(reverse('legal_user_acceptance'), {'slug': 'privacy-policy'}, format='json')
    assert first.status_code == status.HTTP_201_CREATED
    assert second.status_code == status.HTTP_201_CREATED
    assert UserLegalAcceptance.objects.filter(user=customer, legal_page=page).count() == 1

    v2 = page.clone_as_new_version(changes={
        'version_number': '1.1',
        'content_markdown': '# Privacy\n\nChanged.',
        'requires_user_reacceptance': True,
    })
    publish_legal_page(v2)

    status_resp = client.get(reverse('legal_user_acceptance'))
    assert status_resp.status_code == status.HTTP_200_OK
    pending = status_resp.data['data']['pending_required']
    assert pending[0]['slug'] == 'privacy-policy'
    assert pending[0]['needs_reacceptance'] is True


@pytest.mark.django_db
def test_customer_cannot_use_legal_admin_api(customer):
    client = APIClient()
    client.force_authenticate(customer)

    resp = client.post(reverse('legal_admin_create'), {
        'title': 'NDA',
        'slug': 'nda',
        'document_type': 'NDA',
        'content_markdown': 'Secret',
    }, format='json')

    assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_admin_rollback_publishes_historical_content(admin_client):
    old = create_page(version_number='1.0', content_markdown='# Privacy\n\nOld.')
    new = old.clone_as_new_version(changes={
        'version_number': '1.1',
        'content_markdown': '# Privacy\n\nNew.',
    })
    publish_legal_page(new)

    resp = admin_client.post(reverse('legal_admin_rollback'), {'id': str(old.id)}, format='json')

    assert resp.status_code == status.HTTP_200_OK
    assert resp.data['data']['content_markdown'] == '# Privacy\n\nOld.'
    assert resp.data['data']['is_published'] is True


@pytest.mark.django_db
def test_public_html_legal_page_serves_seo_document():
    create_page(seo_title='Connect Privacy')

    resp = APIClient().get(reverse('public_legal_page', kwargs={'slug': 'privacy-policy'}))

    assert resp.status_code == status.HTTP_200_OK
    content = resp.content.decode()
    assert '<title>Connect Privacy</title>' in content
    assert '<h1>Privacy Policy</h1>' in content


@pytest.mark.django_db
def test_import_legal_docx_command_creates_published_page(tmp_path):
    source = tmp_path / 'CONNECTLAUNDRY PRIVACY POLICY.docx'
    document_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Privacy Policy</w:t></w:r></w:p>
    <w:p><w:r><w:t>We protect customer data.</w:t></w:r></w:p>
  </w:body>
</w:document>'''
    with zipfile.ZipFile(source, 'w') as docx:
        docx.writestr('word/document.xml', document_xml)

    call_command('import_legal_docx', str(tmp_path))

    page = LegalPage.objects.get(slug='privacy-policy')
    assert page.is_published is True
    assert page.version_number == '1.0'
    assert '<h1>Privacy Policy</h1>' in page.content_html
