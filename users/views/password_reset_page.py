"""Hosted reset page: the link in the reset email opens here.

Works in any browser, with no app or frontend site required. It also offers
the app deep link, whose reset screen accepts the same resetId + code.
"""
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework import permissions, views
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.renderers import TemplateHTMLRenderer
from rest_framework.response import Response

from config.throttling import RESET_PASSWORD_THROTTLES
from users.serializers.password_reset import ResetPasswordSerializer
from users.services.password_reset import apply_password_reset, find_valid_token

APP_RESET_URL = 'connect-laundry://authScreens/resetPassword'
TEMPLATE = 'users/password_reset_page.html'


def _first_error(errors):
    for value in errors.values():
        if isinstance(value, (list, tuple)) and value:
            return str(value[0])
        if value:
            return str(value)
    return 'Please check the form and try again.'


@method_decorator(ensure_csrf_cookie, name='get')
class PasswordResetPageView(views.APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    renderer_classes = [TemplateHTMLRenderer]
    parser_classes = [FormParser, MultiPartParser]

    def get_throttles(self):
        # Viewing the page is harmless; submitting a code is rate limited.
        return [t() for t in RESET_PASSWORD_THROTTLES] if self.request.method == 'POST' else []

    def _render(self, reset_id, *, error='', done=False, status=200):
        app_link = f'{APP_RESET_URL}?resetId={reset_id}' if reset_id else APP_RESET_URL
        return Response(
            {'reset_id': reset_id, 'error': error, 'done': done, 'app_link': app_link},
            template_name=TEMPLATE, status=status,
        )

    def get(self, request):
        return self._render(request.query_params.get('resetId', '')[:64])

    def _enforce_csrf(self, request):
        # DRF views are CSRF-exempt; this is a browser form, so check it.
        check = CSRFCheck(lambda _request: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise PermissionDenied('Your session expired. Reload the page and try again.')

    def post(self, request):
        self._enforce_csrf(request)
        reset_id = (request.data.get('reset_id') or '')[:64]
        serializer = ResetPasswordSerializer(data={
            'reset_id': reset_id or None,
            'token': request.data.get('token', ''),
            'new_password': request.data.get('new_password', ''),
            'confirm_password': request.data.get('confirm_password', ''),
        })
        if not serializer.is_valid():
            return self._render(reset_id, error=_first_error(serializer.errors), status=400)
        record = find_valid_token(serializer.validated_data.get('reset_id'), serializer.validated_data['token'])
        if record is None:
            return self._render(reset_id, error='This reset code is invalid or has expired. Request a new one in the app.', status=400)
        apply_password_reset(record, serializer.validated_data['new_password'])
        return self._render(reset_id, done=True)
