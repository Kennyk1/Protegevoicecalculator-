import os
import random
import string
from flask import Flask, request, jsonify
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from utils import create_jwt

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
# Health check route
# ------------------------------
@app.route("/")
def home():
    return jsonify({"status": "APP RUNNING"})


# ------------------------------
# Sign up → with referral rewards
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

    # Check existing user
    user_check = supabase.table("users").select("*").eq("phone", phone).execute()
    if user_check.data:
        return jsonify({"success": False, "message": "User already exists"}), 400

    # Generate unique referral code
    my_code = generate_referral_code()

    # Create user with default balance
    new_user = supabase.table("users").insert({
        "phone": phone,
        "name": name,
        "password": password,
        "referral_code": my_code,
        "referred_by": referral_input if referral_input else None,
        "balance": 0,
        "is_verified": True
    }).execute()

    user_id = new_user.data[0]["id"]

    # ------------------------------
    # Apply referral rewards
    # ------------------------------
    if referral_input:
        referrer = supabase.table("users").select("*").eq("referral_code", referral_input).execute()

        if referrer.data:
            referrer_user = referrer.data[0]

            # Add reward to referrer
            supabase.table("users").update({
                "balance": float(referrer_user["balance"]) + 0.1
            }).eq("id", referrer_user["id"]).execute()

            # Give welcome bonus to new user
            supabase.table("users").update({
                "balance": 0.5
            }).eq("id", user_id).execute()

            # Log referral
            supabase.table("referrals").insert({
                "referrer_code": referral_input,
                "referred_user_id": user_id,
                "reward": 0.1
            }).execute()

    token = create_jwt({"phone": phone})

    return jsonify({
        "success": True,
        "message": "Registered successfully",
        "token": token,
        "referral_code": my_code
    })


# ------------------------------
# Login
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

    token = create_jwt({"phone": phone})

    return jsonify({"success": True, "token": token})


# ------------------------------
# Get current user (dashboard)
# ------------------------------
@app.route("/api/me", methods=["POST"])
def me():
    data = request.json
    phone = data.get("phone")

    user = supabase.table("users").select("*").eq("phone", phone).execute()

    if not user.data:
        return jsonify({"success": False, "message": "User not found"}), 404

    user = user.data[0]

    # count referrals
    referral_count = supabase.table("referrals").select("*").eq(
        "referrer_code", user["referral_code"]
    ).execute()

    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "phone": user["phone"],
            "balance": user["balance"],
            "referral_code": user["referral_code"],
            "total_referrals": len(referral_count.data)
        }
    })


# ------------------------------
# Run server
# ------------------------------
if __name__ == "__main__":
    print("✅ APP RUNNING ON PORT", os.environ.get("PORT", 5000))
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
