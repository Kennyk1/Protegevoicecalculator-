import os
from dotenv import load_dotenv

load_dotenv()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# OTP SMS API
OTP_API_URL = os.getenv("OTP_API_URL")  # Your dev SMS API endpoint
OTP_API_KEY = os.getenv("OTP_API_KEY")

# JWT
JWT_SECRET = os.getenv("JWT_SECRET", "supersecretkey")
JWT_ALGORITHM = "HS256"

# OTP Settings
OTP_EXPIRY_SECONDS = 300  # 5 minutes
