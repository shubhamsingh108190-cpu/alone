import os
import json
import base64
import secrets
import requests
from datetime import datetime, timezone, timedelta

from flask import Flask, request, jsonify
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import jwt

app = Flask(__name__)

STORAGE_TOKEN = os.environ["STORAGE_TOKEN"]
STORAGE_URL = os.environ["STORAGE_URL"]

_private_key = None


def fetch_private_key():
    global _private_key
    resp = requests.get(
        STORAGE_URL,
        params={"action": "key", "token": STORAGE_TOKEN},
        timeout=10
    )
    resp.raise_for_status()
    pem_text = resp.json()["key"]
    _private_key = serialization.load_pem_private_key(
        pem_text.encode(), password=None
    )


def get_config():
    resp = requests.get(
        STORAGE_URL,
        params={"action": "config", "token": STORAGE_TOKEN},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()


def get_devices():
    resp = requests.get(
        STORAGE_URL,
        params={"action": "devices", "token": STORAGE_TOKEN},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()


def rsa_decrypt(ciphertext_b64: str) -> bytes:
    ciphertext = base64.b64decode(ciphertext_b64)
    return _private_key.decrypt(
        ciphertext,
        asym_padding.OAEP(
            mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None
        )
    )


def aes_decrypt(key: bytes, iv_b64: str, ciphertext_b64: str) -> bytes:
    iv = base64.b64decode(iv_b64)
    ciphertext = base64.b64decode(ciphertext_b64)
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(iv, ciphertext, None)


def aes_encrypt(key: bytes, plaintext: bytes) -> dict:
    iv = secrets.token_bytes(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, plaintext, None)
    return {
        "iv": base64.b64encode(iv).decode(),
        "d": base64.b64encode(ciphertext).decode()
    }


def make_jwt(android_id: str, nonce: str, status: str, access: str) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": "auth",
        "sub": android_id,
        "iat": now,
        "exp": now + timedelta(seconds=30),
        "jti": nonce,
        "status": status,
        "access": access
    }
    private_pem = _private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )
    return jwt.encode(payload, private_pem, algorithm="RS256")


@app.route("/api", methods=["POST"])
def api():
    try:
        body = request.get_json(force=True, silent=True)
        if not body:
            return "", 400

        k_b64 = body.get("k")
        iv_b64 = body.get("iv")
        d_b64 = body.get("d")
        if not all([k_b64, iv_b64, d_b64]):
            return "", 400

        # Step 1: RSA-OAEP decrypt the AES session key
        aes_key = rsa_decrypt(k_b64)
        if len(aes_key) != 32:
            return "", 400

        # Step 2: AES-256-GCM decrypt the payload
        plaintext = aes_decrypt(aes_key, iv_b64, d_b64)
        inner = json.loads(plaintext.decode())

        android_id = inner.get("android_id")
        nonce = inner.get("nonce")
        if not android_id or not nonce:
            return "", 400

        # Step 3: Check active flag
        config = get_config()
        if not config.get("active", False):
            token = make_jwt(android_id, nonce, "inactive", "denied")
            return jsonify(aes_encrypt(aes_key, token.encode()))

        # Step 4: Check device authorisation
        devices = get_devices()
        authorised_ids = devices.get("ids", [])
        if android_id not in authorised_ids:
            token = make_jwt(android_id, nonce, "active", "denied")
            return jsonify(aes_encrypt(aes_key, token.encode()))

        # Step 5: Authorised
        token = make_jwt(android_id, nonce, "active", "granted")
        return jsonify(aes_encrypt(aes_key, token.encode()))

    except Exception:
        return "", 400


with app.app_context():
    fetch_private_key()

if __name__ == "__main__":
    app.run()
