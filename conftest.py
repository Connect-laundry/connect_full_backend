import pytest
from django.conf import settings
from unittest.mock import patch
from decimal import Decimal

@pytest.fixture(autouse=True)
def force_celery_eager(settings):
    """
    Force Celery to run tasks immediately in the same process.
    Prevents WinError 10061 (Broker connection errors) during tests.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_BROKER_URL = 'memory://'
    settings.CELERY_RESULT_BACKEND = 'cache'
    settings.CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
    # Some older versions or specific setups might check this
    settings.CELERY_BROKER_TRANSPORT = 'memory'

@pytest.fixture(autouse=True)
def mock_celery_retry():
    """
    Global mock for celery task retry to avoid broker connection attempts.
    """
    with patch('celery.app.task.Task.retry') as mock_retry:
        mock_retry.side_effect = Exception("Celery retry called but caught by mock for testing")
        yield mock_retry

@pytest.fixture(autouse=True)
def mock_celery_task_execution():
    """
    Force Celery tasks to execute synchronously when .delay() or .apply_async() is called.
    This is a foolproof way to bypass broker connection issues.
    """
    def mock_delay(self, *args, **kwargs):
        return self.run(*args, **kwargs)

    with patch('celery.app.task.Task.delay', autospec=True) as mock_d:
        mock_d.side_effect = mock_delay
        with patch('celery.app.task.Task.apply_async', autospec=True) as mock_a:
            mock_a.side_effect = lambda self, args=None, kwargs=None, **other: self.run(*(args or []), **(kwargs or {}))
            yield

@pytest.fixture
def authenticated_user(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    user = User.objects.create_user(
        email="testuser@example.com", 
        password="password123", 
        first_name="Test",
        last_name="User",
        phone="1234567890",
        role="OWNER" # Added role for validation
    )
    return user

@pytest.fixture
def auth_client(client, authenticated_user):
    client.force_login(authenticated_user)
    return client

@pytest.fixture
def sample_laundry(db, authenticated_user):
    from laundries.models import Laundry
    return Laundry.objects.create(
        name="Test Laundry",
        owner=authenticated_user,
        address="123 Test St",
        phone_number="1234567890",
        latitude=Decimal('5.603700'),
        longitude=Decimal('-0.187000'),
        status='APPROVED',
        is_active=True,
        pricing_methods=['PER_KG']
    )

@pytest.fixture
def sample_order(db, authenticated_user, sample_laundry):
    from ordering.models import Order
    return Order.objects.create(
        user=authenticated_user,
        laundry=sample_laundry,
        estimated_price=Decimal('100.00'),
        final_price=Decimal('100.00'),
        status='PENDING',
        order_no='ORD-TEST-123'
    )

@pytest.fixture
def sample_payment(db, sample_order):
    from payments.models import Payment
    return Payment.objects.create(
        order=sample_order,
        amount=100.00,
        transaction_reference="REF_TEST_123",
        status='PENDING'
    )
