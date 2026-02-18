from flask import Flask, request, jsonify
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY, JWT_SECRET, JWT_ALGORITHM
from utils import hash_password, verify_password, create_jwt

app = Flask(__name__)

# Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------------------------
# Sign up → instant registration
# ------------------------------
@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.json
    phone = data.get("phone")
    name = data.get("name")
    referral = data.get("referral", "")
    password = data.get("password")

    # Require all fields
    if not phone or not name or not password:
        return jsonify({"success": False, "message": "Phone, name, and password are required"}), 400

    # Check if user exists
    user_check = supabase.table("users").select("*").eq("phone", phone).execute()
    if user_check.data:
        return jsonify({"success": False, "message": "User already exists"}), 400

    # Hash password safely
    try:
        hashed_password = hash_password(password)
    except Exception as e:
        return jsonify({"success": False, "message": "Password hashing failed", "error": str(e)}), 500

    # Insert user into database
    supabase.table("users").insert({
        "phone": phone,
        "name": name,
        "referral": referral,
        "password": hashed_password,
        "is_verified": True  # no OTP needed
    }).execute()

    # Generate token
    token = create_jwt({"phone": phone})

    return jsonify({"success": True, "message": "Registered successfully", "token": token})


# ------------------------------
# Login
# ------------------------------
@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    phone = data.get("phone")
    password = data.get("password")

    # Require both phone and password
    if not phone or not password:
        return jsonify({"success": False, "message": "Phone and password are required"}), 400

    # Get user
    user = supabase.table("users").select("*").eq("phone", phone).execute()
    if not user.data:
        return jsonify({"success": False, "message": "User not found"}), 404

    user = user.data[0]

    # Check password
    if not user.get("password") or not verify_password(password, user["password"]):
        return jsonify({"success": False, "message": "Invalid password"}), 400

    # Generate token
    token = create_jwt({"phone": phone})

    return jsonify({"success": True, "token": token})


# ------------------------------
# Run server
# ------------------------------
if __name__ == "__main__":
    app.run(debug=True)
