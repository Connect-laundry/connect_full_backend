# pyre-ignore[missing-module]
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle, AnonRateThrottle

class BurstUserThrottle(UserRateThrottle):
    scope = 'burst_user'

class SustainedUserThrottle(UserRateThrottle):
    scope = 'sustained_user'

class AuthThrottle(AnonRateThrottle):
    """Throttle for sensitive auth endpoints (Login, Register)."""
    scope = 'auth'

class ReviewThrottle(SimpleRateThrottle):
    """Throttle for review creation."""
    scope = 'review'
    
    def get_cache_key(self, request, view):
        if request.user.is_authenticated:
            ident = request.user.id
        else:
            ident = self.get_ident(request)
            
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }

class FeedbackThrottle(SimpleRateThrottle):
    """Throttle for feedback submission."""
    scope = 'feedback'
    
    def get_cache_key(self, request, view):
        if request.user.is_authenticated:
            ident = request.user.id
        else:
            ident = self.get_ident(request)
            
        return self.cache_format % {
            'scope': self.scope,
            'ident': ident
        }
