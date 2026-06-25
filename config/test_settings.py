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

from .settings import BASE_DIR

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
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}

# Silence security warnings that are irrelevant for CI / test environments
SILENCED_SYSTEM_CHECKS = [
    'security.W004',  # SECURE_HSTS_SECONDS
    'security.W008',  # SECURE_SSL_REDIRECT
    'security.W012',  # SESSION_COOKIE_SECURE
    'security.W016',  # CSRF_COOKIE_SECURE
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
        'config.throttling.BurstUserThrottle',
        'config.throttling.SustainedUserThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'burst_user': '6000/minute',
        'sustained_user': '10000/day',
        'review': '500/hour',
        'feedback': '3/hour',
        'legal_public': '1000/hour',
        'anon': '1000/day',
        'auth_login_ip': '10/minute',
        'auth_login_account': '5/minute',
        'auth_register_ip': '5/minute',
        'auth_register_account': '300/hour',
        'auth_refresh_ip': '2000/minute',
        'password_reset_ip': '3/hour',
        'password_reset_account': '3/hour',
        'reset_password_ip': '3/hour',
        'payment_create': '10/minute',
        'admin_search': '1000/minute',
        'notif_track': '6000/minute',
    }
}

TAX_RATE = 0.07
DELIVERY_FEE_BASE = 10.00
PLATFORM_FEE_RATE = 0.05
FRONTEND_URL = 'http://localhost:3000'
PAYSTACK_SECRET_KEY = 'test-paystack-secret'
PASSWORD_RESET_TOKEN_EXPIRY_HOURS = 24
PAYMENT_CURRENCY = 'GHS'

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
    }
}

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

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

