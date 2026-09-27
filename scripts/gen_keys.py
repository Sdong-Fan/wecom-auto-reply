"""Generate Token and EncodingAESKey for WeCom callback configuration.

Usage: python scripts/gen_keys.py

Outputs values you can paste into:
  1. Your .env file (WECOM_TOKEN, WECOM_ENCODING_AES_KEY)
  2. WeCom admin console → App → Callback URL config

How the process works:
  1. Run this script → get Token + AESKey
  2. Write them into .env
  3. Start gateway + ngrok → get HTTPS URL
  4. Go to WeCom admin → fill in URL + Token + AESKey → Save
  5. WeCom sends GET verification → gateway responds → Config saved!
"""

import base64
import secrets
import string


def generate_token(length: int = 10) -> str:
    """Generate a random Token string (letters + digits, 3-32 chars)."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_aes_key() -> str:
    """Generate a random 43-character EncodingAESKey.

    WeCom requires: 43 chars, Base64-encoded 32-byte AES key (without trailing =).
    """
    aes_bytes = secrets.token_bytes(32)
    encoded = base64.b64encode(aes_bytes).decode()
    return encoded.rstrip("=")


if __name__ == "__main__":
    token = generate_token()
    aes_key = generate_aes_key()

    print("=" * 60)
    print("  WeCom Callback Keys")
    print("=" * 60)
    print()
    print(f"  Token:          {token}")
    print(f"  EncodingAESKey: {aes_key}")
    print()
    print("-" * 60)
    print("  Add to your .env file:")
    print(f"    WECOM_TOKEN={token}")
    print(f"    WECOM_ENCODING_AES_KEY={aes_key}")
    print("-" * 60)
    print()
    print("Configuration order:")
    print("  1. Copy the above 2 lines into .env")
    print("  2. Start gateway + ngrok → get your HTTPS URL")
    print("  3. WeCom admin → App → Callback → paste URL + Token + AESKey → Save")
