import os
import sys
import logging

# Paths to scan
PROJECT_PATH = "c:/P/connect_full_backend/config"
SETTINGS_FILE = os.path.join(PROJECT_PATH, "settings.py")

logging.basicConfig(level=logging.INFO)

def check_debug():
    """Verify that DEBUG is False in a production-like environment."""
    # Note: In CI, we usually set DEBUG=True for tests, 
    # but we want to ensure the base settings.py doesn't default to True 
    # without environment variable override.
    
    if not os.path.exists(SETTINGS_FILE):
        logging.error(f"Settings file not found at {SETTINGS_FILE}")
        return False
        
    with open(SETTINGS_FILE, 'r') as f:
        content = f.read()
        
    # Check if DEBUG is set to True explicitly without environment lookup
    if "DEBUG = True" in content and "os.environ.get" not in content and "getenv" not in content:
        logging.error("CRITICAL: DEBUG = True is hardcoded in settings.py. Use environment variables.")
        return False
        
    logging.info("[OK] DEBUG safety check passed.")
    return True

def check_secrets():
    """Verify no common secrets are hardcoded in settings.py."""
    with open(SETTINGS_FILE, 'r') as f:
        lines = f.readlines()
        
    hardcoded_keys = ["SECRET_KEY", "DATABASE_URL", "PAYSTACK_SECRET_KEY"]
    for line in lines:
        for key in hardcoded_keys:
            if f"{key} =" in line and "os.environ" not in line and "getenv" not in line and "token" not in line:
                # Basic check for hardcoded strings
                if '"' in line or "'" in line:
                    logging.error(f"CRITICAL: Potential hardcoded secret found for {key} in settings.py.")
                    return False
    
    logging.info("[OK] Secrets safety check passed.")
    return True

if __name__ == "__main__":
    success = True
    if not check_debug():
        success = False
    if not check_secrets():
        success = False
        
    if not success:
        sys.exit(1)
    
    sys.exit(0)
