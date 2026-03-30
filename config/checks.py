import os
from django.core.checks import Error, register

@register()
def check_production_env_vars(app_configs, **kwargs):
    errors = []

    import sys
    from django.conf import settings        

    # Skip checks if running tests, makemigrations, or if DEBUG is explicitly True
    IS_TESTING = 'test' in sys.argv or 'pytest' in sys.argv[0]
    IS_MANAGEMENT_TASK = any(arg in sys.argv for arg in ['makemigrations', 'migrate', 'check'])
    
    if not settings.DEBUG and not IS_TESTING and not IS_MANAGEMENT_TASK:
        critical_vars = [
            'SECRET_KEY',
            'DATABASE_URL',
            'REDIS_URL',
            'CLOUDINARY_CLOUD_NAME',
            'CLOUDINARY_API_KEY',
            'CLOUDINARY_API_SECRET',
            'SENTRY_DSN',
            'PAYSTACK_SECRET_KEY',
        ]
        
        for var in critical_vars:
            if not os.getenv(var):
                errors.append(
                    Error(
                        f"Missing critical environment variable: {var}",
                        hint=f"Set {var} in your production environment.",
                        id=f"config.E00{critical_vars.index(var) + 1}",
                    )
                )
    
    return errors
