import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AuthSession, User


PASSWORD_ITERATIONS = 600_000
SESSION_TOKEN_BYTES = 32
_DUMMY_SALT = bytes.fromhex("6c420341065c4893ec10a5fb1984fcce")
_DUMMY_DERIVED = hashlib.pbkdf2_hmac(
    "sha256", b"unavailable-account", _DUMMY_SALT, PASSWORD_ITERATIONS
)
DUMMY_PASSWORD_HASH = (
    f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_DUMMY_SALT.hex()}${_DUMMY_DERIVED.hex()}"
)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, raw_iterations, salt_hex, expected_hex = password_hash.split("$")
        iterations = int(raw_iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except (ValueError, TypeError):
        return False
    if algorithm != "pbkdf2_sha256" or not 1 <= iterations <= 2_000_000:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def create_session(
    db: Session, user_id: str, lifetime_days: int
) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    session = AuthSession(
        token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
        user_id=user_id,
        expires_at=datetime.now(timezone.utc)
        + timedelta(days=max(1, min(lifetime_days, 90))),
    )
    db.add(session)
    return token, session


def get_session_user(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session = db.get(AuthSession, token_hash)
    if session is None:
        return None
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        return None
    return db.get(User, session.user_id)


def revoke_session(db: Session, token: str | None) -> None:
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        session = db.get(AuthSession, token_hash)
        if session is not None:
            db.delete(session)
            db.commit()
