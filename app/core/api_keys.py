import hashlib
import secrets


def generate_ingest_api_key() -> str:
    return f"aegis_ingest_{secrets.token_urlsafe(32)}"


def hash_ingest_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def api_key_matches(api_key: str, expected_hash: str) -> bool:
    return secrets.compare_digest(hash_ingest_api_key(api_key), expected_hash)
