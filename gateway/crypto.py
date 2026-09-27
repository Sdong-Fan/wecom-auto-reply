"""WeCom message encryption/decryption (AES-256-CBC) and signature verification.

WeCom callback protocol:
- Signature: SHA1(sort([token, timestamp, nonce, echostr_or_msg]))
- Message encryption: AES-256-CBC with PKCS#7 padding
- Encrypted payload: Base64(16_bytes_random + 4_bytes_msg_len + msg + corpid)
"""

import base64
import hashlib
import struct

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


def verify_signature(signature: str, timestamp: str, nonce: str,
                     echostr: str, token: str) -> bool:
    """Verify WeCom callback signature (URL validation and message reception)."""
    params = sorted([token, timestamp, nonce, echostr])
    joined = "".join(params)
    expected = hashlib.sha1(joined.encode()).hexdigest()
    return signature == expected


def decrypt_message(encrypted: str, encoding_aes_key: str) -> tuple[str, str]:
    """Decrypt an encrypted WeCom message.

    Returns (decrypted_xml, corp_id).
    """
    aes_key = base64.b64decode(encoding_aes_key + "=")
    cipher = AES.new(aes_key, AES.MODE_CBC, iv=aes_key[:16])
    encrypted_bytes = base64.b64decode(encrypted)
    decrypted = cipher.decrypt(encrypted_bytes)

    # PKCS#7 unpad
    pad_len = decrypted[-1]
    decrypted = decrypted[:-pad_len]

    # Parse structure: 16 bytes random + 4 bytes msg_len + msg + corpid
    msg_len = struct.unpack("!I", decrypted[16:20])[0]
    msg = decrypted[20:20 + msg_len].decode("utf-8")
    corp_id = decrypted[20 + msg_len:].decode("utf-8")

    return msg, corp_id


def encrypt_message(reply_xml: str, encoding_aes_key: str, corp_id: str) -> str:
    """Encrypt a reply message for WeCom.

    Returns Base64-encoded encrypted payload.
    """
    aes_key = base64.b64decode(encoding_aes_key + "=")
    cipher = AES.new(aes_key, AES.MODE_CBC, iv=aes_key[:16])

    msg_bytes = reply_xml.encode("utf-8")
    random_prefix = get_random_bytes(16)
    msg_len = struct.pack("!I", len(msg_bytes))
    payload = random_prefix + msg_len + msg_bytes + corp_id.encode("utf-8")

    # PKCS#7 pad
    block_size = 32
    pad_len = block_size - (len(payload) % block_size)
    payload += bytes([pad_len] * pad_len)

    encrypted = cipher.encrypt(payload)
    return base64.b64encode(encrypted).decode()
