import os
import base64
# pyrefly: ignore [missing-import]
from py_vapid import Vapid
from cryptography.hazmat.primitives import serialization

def generate_and_save():
    # 1. Generate keys
    v = Vapid()
    v.generate_keys()
    
    pub_bytes = v.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint
    )
    priv_bytes = v.private_key.private_numbers().private_value.to_bytes(32, 'big')
    
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).decode('utf-8').rstrip('=')
    priv_b64 = base64.urlsafe_b64encode(priv_bytes).decode('utf-8').rstrip('=')
    
    print("Generated VAPID Keys:")
    print(f"Public Key (65 bytes base64url): {pub_b64}")
    print(f"Private Key (32 bytes base64url): {priv_b64}")
    
    env_path = ".env"
    if not os.path.exists(env_path):
        print(".env file not found!")
        return
        
    with open(env_path, "r") as f:
        lines = f.readlines()
        
    new_lines = []
    keys_updated = {
        'WEBPUSH_VAPID_PUBLIC_KEY': False,
        'WEBPUSH_VAPID_PRIVATE_KEY': False,
        'WEBPUSH_VAPID_SUB': False
    }
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('WEBPUSH_VAPID_PUBLIC_KEY='):
            new_lines.append(f"WEBPUSH_VAPID_PUBLIC_KEY={pub_b64}\n")
            keys_updated['WEBPUSH_VAPID_PUBLIC_KEY'] = True
        elif stripped.startswith('WEBPUSH_VAPID_PRIVATE_KEY='):
            new_lines.append(f"WEBPUSH_VAPID_PRIVATE_KEY={priv_b64}\n")
            keys_updated['WEBPUSH_VAPID_PRIVATE_KEY'] = True
        elif stripped.startswith('WEBPUSH_VAPID_SUB='):
            new_lines.append("WEBPUSH_VAPID_SUB=mailto:odamephilip966@gmail.com\n")
            keys_updated['WEBPUSH_VAPID_SUB'] = True
        else:
            new_lines.append(line)
            
    # If not present in file, append them
    if not keys_updated['WEBPUSH_VAPID_PUBLIC_KEY']:
        new_lines.append(f"WEBPUSH_VAPID_PUBLIC_KEY={pub_b64}\n")
    if not keys_updated['WEBPUSH_VAPID_PRIVATE_KEY']:
        new_lines.append(f"WEBPUSH_VAPID_PRIVATE_KEY={priv_b64}\n")
    if not keys_updated['WEBPUSH_VAPID_SUB']:
        new_lines.append("WEBPUSH_VAPID_SUB=mailto:odamephilip966@gmail.com\n")
        
    with open(env_path, "w") as f:
        f.writelines(new_lines)
        
    print("Successfully saved VAPID keys to .env!")

if __name__ == "__main__":
    generate_and_save()
