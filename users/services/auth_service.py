# pyre-ignore[missing-module]
from django.contrib.auth import authenticate
# pyre-ignore[missing-module]
from rest_framework import serializers
# pyre-ignore[missing-module]
from rest_framework.exceptions import AuthenticationFailed

from .session_service import issue_tokens_for_user

class AuthService:
    @staticmethod
    def get_tokens_for_user(user, request):
        tokens = issue_tokens_for_user(user, request)
        return {
            'refresh': tokens['refresh'],
            'access': tokens['access'],
        }

    def register_user(self, validated_data, request):
        validated_data.pop('password_confirm')
        password = validated_data.pop('password')
        
        # pyre-ignore[missing-module]
        from ..models import User
        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        user.save()
        
        return user, self.get_tokens_for_user(user, request)

    def login_user(self, email, password, request):
        user = authenticate(email=email, password=password)

        if not user:
            self._record_failed_login(email, request)
            raise AuthenticationFailed("Invalid email or password.")

        if not user.is_active:
            self._record_failed_login(email, request, reason='disabled')
            raise AuthenticationFailed("User account is disabled.")

        return user, self.get_tokens_for_user(user, request)

    @staticmethod
    def _record_failed_login(email, request, reason='invalid_credentials'):
        """Audit + surface a (deduped) security notification for failed logins.

        Best-effort: must never interfere with the auth response. The dedup_key
        collapses repeated failures for the same account into a single unread
        admin notification until it is read, preventing notification spam.
        """
        try:
            from marketplace.services.audit import record_audit
            from marketplace.services.notification_service import NotificationService
            from marketplace.models import AuditLog, Notification
            safe_email = (email or '')[:254]
            record_audit(
                action=AuditLog.Action.SECURITY_EVENT,
                request=request,
                target_type='User',
                target_repr=safe_email,
                metadata={'event': 'failed_login', 'reason': reason},
            )
            NotificationService.notify_admins(
                title="Failed login attempt",
                body=f"A failed login attempt was recorded for {safe_email}.",
                category='FAILED_LOGIN',
                priority=Notification.Priority.HIGH,
                action_url='/admin/users/user/',
                dedup_key=f'failed_login:{safe_email.lower()}',
            )
        except Exception:  # pragma: no cover - defensive
            pass

