import logging
import os
# pyre-ignore[missing-module]
import requests
# pyre-ignore[missing-module]
from django.conf import settings
# pyre-ignore[missing-module]
from django.core.cache import cache
# pyre-ignore[missing-module]
from jose import jwt, jwk
# pyre-ignore[missing-module]
from jose.utils import base64url_decode
# pyre-ignore[missing-module]
from rest_framework import authentication, exceptions
# pyre-ignore[missing-module]
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)

class ClerkJWKSClient:
    """Handles fetching and caching of Clerk JWKS."""
    
    CACHE_KEY = 'clerk_jwks'
    CACHE_TIMEOUT = 3600  # 1 hour

    def get_jwks(self):
        jwks = cache.get(self.CACHE_KEY)
        if not jwks:
            jwks = self.fetch_jwks()
            cache.set(self.CACHE_KEY, jwks, self.CACHE_TIMEOUT)
        return jwks

    def fetch_jwks(self):
        url = settings.CLERK_JWKS_URL
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch Clerk JWKS from {url}: {e}")
            raise exceptions.AuthenticationFailed("Could not verify authentication token.")

    def get_public_key(self, token):
        try:
            unverified_header = jwt.get_unverified_header(token)
            kid = unverified_header.get('kid')
        except Exception as e:
            logger.error(f"Failed to parse JWT header: {e}")
            raise exceptions.AuthenticationFailed("Invalid token header format.")

        jwks = self.get_jwks()
        
        available_kids = [k.get('kid') for k in jwks.get('keys', [])]
        logger.info(f"Checking for kid: {kid} in available kids: {available_kids}")

        for key in jwks.get('keys', []):
            if key.get('kid') == kid:
                return key
        
        # If kid not found, try refreshing cache once
        logger.info("Kid not found in cache, fetching fresh JWKS...")
        jwks = self.fetch_jwks()
        cache.set(self.CACHE_KEY, jwks, self.CACHE_TIMEOUT)
        
        available_kids = [k.get('kid') for k in jwks.get('keys', [])]
        logger.info(f"Fresh JWKS kids: {available_kids}")

        for key in jwks.get('keys', []):
            if key.get('kid') == kid:
                return key
                
        logger.error(f"Token kid '{kid}' not found in Clerk JWKS. Available kids: {available_kids}")
        raise exceptions.AuthenticationFailed(f"Invalid token signature: Key '{kid}' not found.")

class ClerkAuthentication(authentication.BaseAuthentication):
    """
    DRF authentication class for Clerk JWTs.
    Verify signature against Clerk JWKS and sync user data.
    """
    
    def __init__(self):
        self.jwks_client = ClerkJWKSClient()

    def authenticate(self, request):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None

        token = auth_header.split(' ')[1]
        payload = self.verify_token(token)
        
        user = self.get_or_create_user(payload)
        return (user, token)

    def verify_token(self, token):
        if not token.startswith('eyJ'):
            raise exceptions.AuthenticationFailed("Invalid token format: Not a base64-encoded JWT (should start with 'ey'). This usually means you copied the wrong token from Clerk.")

        try:
            public_key = self.jwks_client.get_public_key(token)
            payload = jwt.decode(
                token,
                public_key,
                algorithms=['RS256'],
                audience=settings.CLERK_AUDIENCE,
                issuer=settings.CLERK_ISSUER
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed("Token has expired.")
        except jwt.JWTClaimsError as e:
            logger.error(f"Clerk JWT claims error: {e}")
            raise exceptions.AuthenticationFailed(f"Invalid token claims: {e}")
        except Exception as e:
            # Get header and payload to help debug
            try:
                header = jwt.get_unverified_header(token)
                unverified_payload = jwt.get_unverified_claims(token)
                iss = unverified_payload.get('iss')
                aud = unverified_payload.get('aud')
                
                error_msg = f"Invalid authentication token: {e}"
                if iss != settings.CLERK_ISSUER:
                    error_msg += f" (PROJECT MISMATCH: Token is from '{iss}' but backend is configured for '{settings.CLERK_ISSUER}')"
                elif aud != settings.CLERK_AUDIENCE:
                    error_msg += f" (AUDIENCE MISMATCH: Token audience is '{aud}' but backend expects '{settings.CLERK_AUDIENCE}')"
                
                logger.error(f"Clerk JWT verification error. Issuer: {iss}, Expected: {settings.CLERK_ISSUER}. Error: {e}")
                raise exceptions.AuthenticationFailed(error_msg)
            except exceptions.AuthenticationFailed:
                raise
            except Exception as debug_e:
                logger.error(f"Clerk JWT verification error: {e}")
                raise exceptions.AuthenticationFailed(f"Invalid authentication token: {e}")

    def fetch_user_from_clerk(self, user_id):
        """Fetches full user details from Clerk Backend API."""
        secret_key = os.getenv('CLERK_SECRET_KEY')
        if not secret_key:
            logger.warning("CLERK_SECRET_KEY not found in environment. Cannot fetch full user details.")
            return None
            
        url = f"https://api.clerk.com/v1/users/{user_id}"
        headers = {
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch user {user_id} from Clerk API: {e}")
            return None

    def get_or_create_user(self, payload):
        logger.info(f"Clerk sync payload: {payload}")
        clerk_id = payload.get('sub')
        
        # Clerk can provide email in various fields depending on session claims config
        email = (
            payload.get('email') or 
            payload.get('primary_email_address') or 
            payload.get('email_address') or
            payload.get('primary_email')
        )
        
        if not clerk_id:
            raise exceptions.AuthenticationFailed("Token missing user identifier ('sub').")

        if not email:
            logger.info(f"Email missing from token for user {clerk_id}. Attempting to fetch from Clerk API...")
            clerk_user = self.fetch_user_from_clerk(clerk_id)
            if clerk_user:
                email_objs = clerk_user.get('email_addresses', [])
                if email_objs:
                    # Try to find primary email first
                    primary_id = clerk_user.get('primary_email_address_id')
                    for e_obj in email_objs:
                        if e_obj.get('id') == primary_id:
                            email = e_obj.get('email_address')
                            break
                    if not email:
                        email = email_objs[0].get('email_address')
                
                # Also try to get names if missing
                if not payload.get('first_name'):
                    payload['first_name'] = clerk_user.get('first_name', '')
                if not payload.get('last_name'):
                    payload['last_name'] = clerk_user.get('last_name', '')
                if not payload.get('phone_number'):
                    # Search through phone_numbers
                    phone_objs = clerk_user.get('phone_numbers', [])
                    if phone_objs:
                        primary_phone_id = clerk_user.get('primary_phone_number_id')
                        for p_obj in phone_objs:
                            if p_obj.get('id') == primary_phone_id:
                                payload['phone_number'] = p_obj.get('phone_number')
                                break
                        if not payload.get('phone_number'):
                            payload['phone_number'] = phone_objs[0].get('phone_number')

        if not email:
            logger.error(f"Clerk token and API both missing email for {clerk_id}. Payload: {payload}")
            raise exceptions.AuthenticationFailed(
                "Clerk token missing email. Please ensure 'email' is added to 'Session Token' claims in your Clerk Dashboard, or provide CLERK_SECRET_KEY."
            )

        # Try to find user by clerk_id first
        user = User.objects.filter(clerk_id=clerk_id).first()
        
        if not user and email:
            # Fallback to email if clerk_id not set (e.g. migration flow)
            user = User.objects.filter(email=email).first()
            if user:
                user.clerk_id = clerk_id
                user.save(update_fields=['clerk_id'])

        if not user:
            # Create new user
            first_name = payload.get('first_name', '')
            last_name = payload.get('last_name', '')
            # For Clerk users, we might not have a phone initially if it's social login
            # But our User model requires phone. Clerk payload might have phone_number.
            phone = payload.get('phone_number', f"clerk_{clerk_id[:10]}") # Placeholder if missing
            
            user = User.objects.create_user(
                email=email,
                phone=phone,
                first_name=first_name,
                last_name=last_name,
                clerk_id=clerk_id,
                is_verified=True, # Clerk handles verification
                role=User.Role.CUSTOMER # Default role
            )
        else:
            # Sync fields
            updated = False
            if email and user.email != email:
                user.email = email
                updated = True
            if payload.get('first_name') and user.first_name != payload.get('first_name'):
                user.first_name = payload.get('first_name')
                updated = True
            if payload.get('last_name') and user.last_name != payload.get('last_name'):
                user.last_name = payload.get('last_name')
                updated = True
            
            if updated:
                user.save()
                
        return user
