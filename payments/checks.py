from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


@register(Tags.security, deploy=True)
def check_paystack_production_configuration(app_configs, **kwargs):
    errors = []
    secret = str(getattr(settings, 'PAYSTACK_SECRET_KEY', '') or '')
    public = str(getattr(settings, 'PAYSTACK_PUBLIC_KEY', '') or '')
    callback = str(getattr(settings, 'PAYSTACK_CALLBACK_URL', '') or '')
    app_callback = str(getattr(settings, 'PAYSTACK_APP_CALLBACK_URL', '') or '')

    if not secret:
        errors.append(Error('PAYSTACK_SECRET_KEY is required.', id='payments.E001'))
    elif not settings.DEBUG and not secret.startswith('sk_live_'):
        errors.append(Error(
            'Production must use a Paystack live secret key.',
            hint='Configure an sk_live_ key in the backend secret store.',
            id='payments.E002',
        ))

    if not public:
        errors.append(Warning('PAYSTACK_PUBLIC_KEY is not configured.', id='payments.W001'))
    elif not settings.DEBUG and not public.startswith('pk_live_'):
        errors.append(Error(
            'Production must use a Paystack live public key.',
            hint='Configure the matching pk_live_ key in the backend secret store.',
            id='payments.E003',
        ))

    if not callback:
        errors.append(Error('PAYSTACK_CALLBACK_URL is required.', id='payments.E004'))
    elif not settings.DEBUG and not callback.startswith('https://'):
        errors.append(Error(
            'Production Paystack callback must use HTTPS.',
            hint='Use an HTTPS Simame callback URL that returns users to the app safely.',
            id='payments.E005',
        ))

    if not app_callback.startswith('connect-laundry://'):
        errors.append(Error(
            'PAYSTACK_APP_CALLBACK_URL must use the registered Simame app scheme.',
            id='payments.E007',
        ))

    if str(getattr(settings, 'PAYMENT_CURRENCY', '')).upper() != 'GHS':
        errors.append(Error('Simame Paystack currency must be GHS.', id='payments.E006'))

    return errors