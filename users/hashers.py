import os

from django.contrib.auth.hashers import PBKDF2PasswordHasher


class TunedPBKDF2PasswordHasher(PBKDF2PasswordHasher):
    """PBKDF2-SHA256 at OWASP's recommended 600,000 iterations.

    Django 6.0 defaults to 1,200,000, which took ~0.6s per hash on a desktop
    core and several times that on the small production instance, on every
    login and signup. The algorithm name is unchanged, so hashes stored at
    any iteration count keep verifying, and Django re-hashes them to this
    count on the next successful login. PASSWORD_PBKDF2_ITERATIONS overrides.
    """

    iterations = int(os.getenv('PASSWORD_PBKDF2_ITERATIONS', '600000'))
