import os
from django.core.checks import Error, register

@register()
def check_production_env_vars(app_configs, **kwargs):
    errors = []
    
    # Only run these checks if NOT in DEBUG mode (production)
    # However, it's often useful to run them in dev too, but with warnings.
    # We'll enforce them for production.
    DEBUG = os.getenv('DEBUG', 'False') == 'True'
    
    if not DEBUG:
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
