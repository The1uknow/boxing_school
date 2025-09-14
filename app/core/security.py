import secrets
from itsdangerous import URLSafeSerializer
from app.core.config import settings

def generate_token(n: int = 24) -> str:
    return secrets.token_urlsafe(n)

def sign_payload(payload: dict) -> str:
    s = URLSafeSerializer(settings.SECRET_KEY)
    return s.dumps(payload)

def unsign_payload(token: str) -> dict:
    s = URLSafeSerializer(settings.SECRET_KEY)
    return s.loads(token)