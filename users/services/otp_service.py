import random
import hashlib
from django.core.cache import cache
from django.conf import settings

class OTPService:
    @staticmethod
    def generate_otp():
        return str(random.randint(100000, 999999))

    @staticmethod
    def _get_hash(otp):
        return hashlib.sha256(otp.encode()).hexdigest()

    def save_otp(self, identifier, otp):
        """
        Save hashed OTP to Redis with expiry. 
        identifier: email or phone
        """
        otp_hash = self._get_hash(otp)
        key = f"otp:{identifier}"
        cache.set(key, otp_hash, timeout=int(settings.OTP_EXPIRY_SECONDS))
        
        # Reset attempts on new OTP
        cache.delete(f"otp_attempts:{identifier}")

    def validate_otp(self, identifier, user_otp):
        """
        Validate OTP and handle retries.
        """
        key = f"otp:{identifier}"
        attempts_key = f"otp_attempts:{identifier}"
        
        stored_hash = cache.get(key)
        if not stored_hash:
            return False, "OTP expired or not found."

        attempts = cache.get(attempts_key, 0)
        if attempts >= int(settings.OTP_MAX_RETRIES):
            return False, "Maximum attempts reached. Please resend a new OTP."

        if self._get_hash(user_otp) == stored_hash:
            # Success: clean up
            cache.delete(key)
            cache.delete(attempts_key)
            return True, "Verified."
        
        # Fail: increment attempts
        cache.set(attempts_key, attempts + 1, timeout=int(settings.OTP_EXPIRY_SECONDS))
        return False, f"Invalid OTP. {int(settings.OTP_MAX_RETRIES) - (attempts + 1)} attempts remaining."

    def check_resend_lock(self, identifier):
        """
        Check if user is allowed to resend OTP.
        """
        lock_key = f"otp_resend_lock:{identifier}"
        return cache.get(lock_key) is not None

    def set_resend_lock(self, identifier):
        """
        Set a cooldown lock for resending OTP.
        """
        lock_key = f"otp_resend_lock:{identifier}"
        cache.set(lock_key, True, timeout=int(settings.OTP_RESEND_COOLDOWN))
