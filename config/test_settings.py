import os

os.environ.setdefault('DISABLE_SENTRY', 'True')

from .settings import *  # noqa: F401,F403


DEBUG = False
ROOT_URLCONF = 'config.test_urls'
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

INSTALLED_APPS = [
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
    'config.middleware.request_id.RequestIDMiddleware',
    'config.middleware.security.SecurityHeadersMiddleware',
]

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
