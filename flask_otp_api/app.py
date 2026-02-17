from config import SUPABASE_URL, SUPABASE_KEY, OTP_API_URL, OTP_API_KEY, OTP_EXPIRY_SECONDS
from utils import generate_otp, hash_password, verify_password, create_jwt
app = Flask(__name__)

# Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------------------------
# Sign up → send OTP
# ------------------------------
@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.json
    phone = data.get("phone")
    referral = data.get("referral", "")

    if not phone:
        return jsonify({"success": False, "message": "Phone required"}), 400

    # Check if user exists
    user_check = supabase.table("users").select("*").eq("phone", phone).execute()
    if user_check.data:
        return jsonify({"success": False, "message": "User already exists"}), 400

    # Insert user with is_verified=False
    supabase.table("users").insert({"phone": phone, "referral": referral, "is_verified": False}).execute()

    # Generate OTP
    otp_code = generate_otp()
    expiry = int(time.time()) + OTP_EXPIRY_SECONDS

    # Store OTP in table
    supabase.table("otp").insert({"phone": phone, "otp_code": otp_code, "expires_at": expiry}).execute()

    # Call your SMS API
    requests.post(OTP_API_URL, json={"phone": phone, "otp": otp_code, "api_key": OTP_API_KEY})

    return jsonify({"success": True, "message": "OTP sent"})


# ------------------------------
# Verify OTP
# ------------------------------
@app.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    data = request.json
    phone = data.get("phone")
    otp = data.get("otp")

    if not phone or not otp:
        return jsonify({"success": False, "message": "Phone and OTP required"}), 400

    # Get OTP from DB
    otp_record = supabase.table("otp").select("*").eq("phone", phone).eq("otp_code", otp).execute()
    if not otp_record.data:
        return jsonify({"success": False, "message": "Invalid OTP"}), 400

    # Check expiry
    if otp_record.data[0]["expires_at"] < int(time.time()):
        return jsonify({"success": False, "message": "OTP expired"}), 400

    # Mark user as verified
    supabase.table("users").update({"is_verified": True}).eq("phone", phone).execute()

    # Create token
    token = create_jwt({"phone": phone})

    return jsonify({"success": True, "message": "Verified successfully", "token": token})


# ------------------------------
# Login
# ------------------------------
@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    phone = data.get("phone")
    password = data.get("password")

    if not phone or not password:
        return jsonify({"success": False, "message": "Phone and password required"}), 400

    # Get user
    user = supabase.table("users").select("*").eq("phone", phone).execute()
    if not user.data:
        return jsonify({"success": False, "message": "User not found"}), 404

    user = user.data[0]

    if not user["is_verified"]:
        return jsonify({"success": False, "message": "User not verified"}), 400

    # Check password (if stored)
    if "password" in user and not verify_password(password, user["password"]):
        return jsonify({"success": False, "message": "Invalid password"}), 400

    token = create_jwt({"phone": phone})

    return jsonify({"success": True, "token": token})


# ------------------------------
# Run server
# ------------------------------
if __name__ == "__main__":
    app.run(debug=True)
