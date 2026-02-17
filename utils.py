import random
import time
import jwt
from passlib.hash import bcrypt
from config import JWT_SECRET, JWT_ALGORITHM

# Generate 4-digit OTP
def generate_otp():
    return str(random.randint(1000, 9999))

# Hash password
def hash_password(password):
    return bcrypt.hash(password)

# Verify password
def verify_password(password, hashed):
    return bcrypt.verify(password, hashed)

# Create JWT token
def create_jwt(payload):
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
