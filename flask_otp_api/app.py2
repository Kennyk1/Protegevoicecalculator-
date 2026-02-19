import os
import random
import string
from flask import Flask, request, jsonify
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from utils import create_jwt, decode_jwt

app = Flask(__name__)

print("🚀 APP STARTING...")

# Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------------------------
# Helper → generate referral code
# ------------------------------
def generate_referral_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

# ------------------------------
# Helper → get current user from JWT
# ------------------------------
def get_current_user():
    auth_header = request.headers.get("Authorization")

    if not auth_header:
        return None, "Missing token"

    try:
        token = auth_header.split(" ")[1]
        payload = decode_jwt(token)
        user_id = payload.get("user_id")

        user = supabase.table("users").select("*").eq("id", user_id).execute()

        if not user.data:
            return None, "User not found"

        return user.data[0], None

    except Exception:
        return None, "Invalid token"


# ------------------------------
# Health check
# ------------------------------
@app.route("/")
def home():
    return jsonify({"status": "APP RUNNING"})


# ------------------------------
# SIGNUP
# ------------------------------
@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.json
    phone = data.get("phone")
    name = data.get("name")
    referral_input = data.get("referral", "")
    password = data.get("password")

    if not phone or not name or not password:
        return jsonify({
            "success": False,
            "message": "Phone, name, and password are required"
        }), 400

    # Check existing
    user_check = supabase.table("users").select("*").eq("phone", phone).execute()
    if user_check.data:
        return jsonify({"success": False, "message": "User already exists"}), 400

    # Generate referral code
    my_code = generate_referral_code()

    # Validate referral
    referrer_user = None
    if referral_input:
        referrer = supabase.table("users").select("*").eq("referral_code", referral_input).execute()
        if not referrer.data:
            return jsonify({
                "success": False,
                "message": "Invalid referral code"
            }), 400
        referrer_user = referrer.data[0]

    # Create user
    new_user = supabase.table("users").insert({
        "phone": phone,
        "name": name,
        "password": password,
        "referral_code": my_code,
        "referred_by": referral_input if referral_input else None,
        "balance": 0,
        "total_referrals": 0,
        "is_verified": True
    }).execute()

    user = new_user.data[0]
    user_id = user["id"]

    # Apply referral rewards
    if referrer_user:
        supabase.table("users").update({
            "balance": float(referrer_user["balance"]) + 0.1,
            "total_referrals": referrer_user["total_referrals"] + 1
        }).eq("id", referrer_user["id"]).execute()

        supabase.table("users").update({
            "balance": 0.5
        }).eq("id", user_id).execute()

    token = create_jwt({"user_id": user_id})

    return jsonify({
        "success": True,
        "message": "Registered successfully",
        "token": token,
        "referral_code": my_code
    })


# ------------------------------
# LOGIN
# ------------------------------
@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    phone = data.get("phone")
    password = data.get("password")

    if not phone or not password:
        return jsonify({
            "success": False,
            "message": "Phone and password are required"
        }), 400

    user = supabase.table("users").select("*").eq("phone", phone).execute()
    if not user.data:
        return jsonify({"success": False, "message": "User not found"}), 404

    user = user.data[0]

    if user.get("password") != password:
        return jsonify({"success": False, "message": "Invalid password"}), 400

    token = create_jwt({"user_id": user["id"]})

    return jsonify({
        "success": True,
        "token": token
    })


# ------------------------------
# DASHBOARD
# ------------------------------
@app.route("/api/me", methods=["GET"])
def me():
    user, error = get_current_user()

    if error:
        return jsonify({"success": False, "message": error}), 401

    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "phone": user["phone"],
            "balance": user["balance"],
            "referral_code": user["referral_code"],
            "total_referrals": user["total_referrals"]
        }
    })


# ------------------------------
# BALANCE
# ------------------------------
@app.route("/api/balance", methods=["GET"])
def balance():
    user, error = get_current_user()

    if error:
        return jsonify({"success": False, "message": error}), 401

    return jsonify({
        "success": True,
        "balance": user["balance"]
    })


# ------------------------------
# LOGOUT
# ------------------------------
@app.route("/api/logout", methods=["POST"])
def logout():
    return jsonify({
        "success": True,
        "message": "Logged out successfully"
    })


# ------------------------------
# RUN
# ------------------------------
if __name__ == "__main__":
    print("✅ APP RUNNING ON PORT", os.environ.get("PORT", 5000))
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
