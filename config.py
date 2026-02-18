import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# ------------------------------
# Supabase
# ------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ------------------------------
# OTP SMS API
# ------------------------------
OTP_API_URL = os.getenv("OTP_API_URL")  # e.g., https://api.otp.dev/v1/verifications
OTP_API_KEY = os.getenv("OTP_API_KEY")
OTP_SENDER_ID = os.getenv("OTP_SENDER_ID")
OTP_TEMPLATE_ID = os.getenv("OTP_TEMPLATE_ID", "")  # Optional, default empty
OTP_CODE_LENGTH = int(os.getenv("OTP_CODE_LENGTH", 4))
OTP_EXPIRY_SECONDS = int(os.getenv("OTP_EXPIRY_SECONDS", 300))  # default 5 minutes

# ------------------------------
# JWT
# ------------------------------
JWT_SECRET = os.getenv("JWT_SECRET", "supersecretkey")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
