import random
import jwt
from datetime import datetime, timedelta
from passlib.hash import bcrypt
from config import JWT_SECRET, JWT_ALGORITHM

# ------------------------------
# Generate 4-digit OTP
# ------------------------------
def generate_otp():
    return str(random.randint(1000, 9999))


# ------------------------------
# Password hashing
# ------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.verify(password, hashed)


# ------------------------------
# JWT creation
# ------------------------------
def create_jwt(payload: dict, expires_in: int = 7):
    """
    expires_in → days (default 7 days)
    """
    payload_copy = payload.copy()
    payload_copy["exp"] = datetime.utcnow() + timedelta(days=expires_in)
    payload_copy["iat"] = datetime.utcnow()

    token = jwt.encode(payload_copy, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token


# ------------------------------
# JWT decoding
# ------------------------------
def decode_jwt(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise Exception("Token expired")
    except jwt.InvalidTokenError:
        raise Exception("Invalid token")
