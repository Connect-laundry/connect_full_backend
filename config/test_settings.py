import os

os.environ.setdefault('DISABLE_SENTRY', 'True')
if not os.environ.get('SECRET_KEY'):
    os.environ['SECRET_KEY'] = 'connect-laundry-ci-test-secret-key-not-used-outside-test-settings'

# Deploy checks run against this settings module in CI, but the production
# Clerk checker intentionally reads process env values. Provide non-secret
# placeholders so CI can exercise the check without requiring live credentials.
os.environ.setdefault('CLERK_APPLICATION_ID', 'app_test_ci_clerk_application')
os.environ.setdefault('CLERK_PUBLISHABLE_KEY', 'pk_test_ci_clerk_publishable_key')
os.environ.setdefault('CLERK_SECRET_KEY', 'sk_test_ci_clerk_secret_key')
os.environ.setdefault('CLERK_JWT_ISSUER', 'https://ci-clerk.example.test')
os.environ.setdefault('CLERK_JWKS_URL', 'https://ci-clerk.example.test/.well-known/jwks.json')
os.environ.setdefault('CLERK_JWT_AUDIENCE', 'connect-backend')
os.environ.setdefault('CLERK_WEBHOOK_SECRET', 'whsec_ci_clerk_webhook_secret')

from pathlib import Path

from .settings import BASE_DIR, THROTTLE_RATES as _PRODUCTION_THROTTLE_RATES

# Production limits, except the general API budget, so unrelated tests that
# make many calls are not throttled. Throttle tests override scopes explicitly.
TEST_THROTTLE_RATES = {
    **_PRODUCTION_THROTTLE_RATES,
    'burst_user': '6000/m',
    'sustained_user': '100000/d',
    'burst_anon': '6000/m',
    'sustained_anon': '100000/d',
    'review': '500/h',
    'admin_search': '1000/m',
    'notif_track': '6000/m',
    'test_push': '5/h',
    'places': '60/m',
}

SECRET_KEY = os.environ.get('SECRET_KEY')
AUTH_USER_MODEL = 'users.User'


DEBUG = False
ROOT_URLCONF = 'config.test_urls'
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver']
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'test-staticfiles'
# Keep test uploads out of the repo tree (without this, FileSystemStorage
# writes to MEDIA_ROOT='' — i.e. the project root — littering laundries/,
# avatars/, uploads/ etc. with test images).
import tempfile
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(tempfile.gettempdir()) / 'connect-test-media'
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}

# Silence security warnings and CI deploy checks that are irrelevant for test environments
SILENCED_SYSTEM_CHECKS = [
    'security.W004',  # SECURE_HSTS_SECONDS
    'security.W008',  # SECURE_SSL_REDIRECT
    'security.W012',  # SESSION_COOKIE_SECURE
    'security.W016',  # CSRF_COOKIE_SECURE
    'payments.E002',
    'payments.E003',
    'payments.E004',
    'payments.E005',
    'payments.E007',
    'payments.W001',
    'drf_spectacular.W001',
]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

INSTALLED_APPS = [
    'unfold',
    'unfold.contrib.filters',
    'unfold.contrib.forms',
    'unfold.contrib.import_export',
    'unfold.contrib.guardian',
    'unfold.contrib.simple_history',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'drf_spectacular',
    'corsheaders',
    'rest_framework_simplejwt.token_blacklist',
    'users',
    'marketplace',
    'ordering',
    'logistics',
    'payments',
    'laundries',
    'analytics',
    'django_celery_results',
    'cloudinary',
    'cloudinary_storage',
    'django.contrib.postgres',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'config.middleware.deactivation.DeactivationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'config.middleware.request_id.RequestIDMiddleware',
    'config.middleware.security.SecurityHeadersMiddleware',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'users.auth.authentication.ClerkOrJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'config.exception_handler.custom_exception_handler',
    'DEFAULT_THROTTLE_CLASSES': [
        # Burst + sustained evaluated together; rejected requests don't count.
        'config.throttling.GeneralThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': TEST_THROTTLE_RATES,
}

TAX_RATE = 0.07
PLATFORM_FEE_RATE = 0.05
# Mirrors production: logistics settle directly with the laundry. Tests that
# exercise in-app logistics billing override this explicitly.
DELIVERY_FEES_IN_APP = False
FRONTEND_URL = 'http://localhost:3000'
PAYSTACK_SECRET_KEY = 'test-paystack-secret'
PASSWORD_RESET_TOKEN_EXPIRY_HOURS = 24
PAYMENT_CURRENCY = 'GHS'

# Connect Insights — Sentry Issues API + report recipients (test defaults).
SENTRY_API_BASE = 'https://sentry.io/api/0'
SENTRY_API_TOKEN = ''
SENTRY_ORG_SLUG = ''
SENTRY_PROJECT_SLUG = ''
ANALYTICS_REPORT_RECIPIENTS = []

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'connect-test-cache',
    },
    # Same LocMem storage, so tests that clear `cache` reset every counter.
    'throttle': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'connect-test-cache',
    },
}

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
EXPO_PUSH_ENABLED = False
EXPO_ACCESS_TOKEN = ''
PUSH_ENVIRONMENT = 'staging'
# Run post-commit push dispatch on the test DB connection, not a thread.
PUSH_DISPATCH_IN_THREAD = False
PUSH_PENDING_DISPATCH_BATCH_SIZE = 500
PUSH_MAX_RECEIPT_RETRIES = 3

MIGRATION_MODULES = {
    'marketplace': None,
    'ordering': None,
    'logistics': None,
    'payments': None,
    'laundries': None,
    'django_celery_results': None,
}

from datetime import timedelta
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=10),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=14),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': False,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'CHECK_REVOKE_TOKEN': True,
    'REVOKE_TOKEN_CLAIM': 'hash_password',
}

# PWA and Web Push VAPID settings for test environment
PWA_VERSION = '1.0.0'
WEBPUSH_VAPID_PUBLIC_KEY = 'BIdn2JpX0b0J0gJ8_VlE-xG1-s2Rz6kU8eWd1Y4r5t-W-zLd6vGvLd6-rG9yYt2H-t_rWd3uX5r2'
WEBPUSH_VAPID_PRIVATE_KEY = ''
WEBPUSH_VAPID_CLAIMS = {
    'sub': 'mailto:odamephilip966@gmail.com'
}

# Initialize Google Application Credentials from environment JSON if present
try:
    from laundries.utils.credentials import initialize_google_credentials
    initialize_google_credentials()
except Exception:
    pass


# Tests address the app directly (REMOTE_ADDR), without Render's proxies, and
# use local-memory cache. The warnings exist for real deployments.
CLIENT_IP_HEADER = ''
TRUSTED_PROXY_COUNT = 0
IP_DIAGNOSTICS_ENABLED = False
SILENCED_SYSTEM_CHECKS = [
    *globals().get('SILENCED_SYSTEM_CHECKS', []),
    'users.W_CLIENT_IP_PROXY',
    'users.W_THROTTLE_LOCAL_MEMORY',
]

# Existing async tests explicitly exercise worker-enabled mode. Direct mode has
# separate production-like regressions in test_launch_optional_workers.py.
PUSH_USE_CELERY = True
CRITICAL_TASKS_USE_CELERY = True

# Recovery sweeps are exercised explicitly in tests/test_push_sweep.py.
PUSH_INPROCESS_SWEEP_ENABLED = False
