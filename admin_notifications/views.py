import hmac

from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import conf
from .services.callbacks import apply_sms_status


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def arkesel_sms_status(request, token):
    """Arkesel delivery report: ``?sms_id=<id>&status=<STATUS>``.

    Public by necessity (Arkesel cannot log in), gated by a secret in the path.
    A wrong or unconfigured secret looks exactly like a missing page. Every
    accepted request gets the same tiny response, whatever it matched, so the
    endpoint reveals nothing about orders or which ids exist.
    """
    secret = conf.arkesel_callback_secret()
    if not secret or not hmac.compare_digest(str(token).encode('utf-8'), secret.encode('utf-8')):
        raise Http404()
    sms_id = request.GET.get('sms_id') or request.POST.get('sms_id')
    status = request.GET.get('status') or request.POST.get('status')
    apply_sms_status(sms_id, status)
    return JsonResponse({'ok': True})
