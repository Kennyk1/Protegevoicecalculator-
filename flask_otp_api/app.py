import os
from flask import Flask, request, jsonify
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY, JWT_SECRET, JWT_ALGORITHM
from utils import create_jwt  # only JWT needed

app = Flask(__name__)

print("🚀 APP STARTING...")

# Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------------------------
# Health check route (important for Render)
# ------------------------------
@app.route("/")
def home():
    return jsonify({"status": "APP RUNNING"})


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
        return jsonify({
            "success": False,
            "message": "Phone, name, and password are required"
        }), 400

    # Check if user exists
    user_check = supabase.table("users").select("*").eq("phone", phone).execute()
    if user_check.data:
        return jsonify({"success": False, "message": "User already exists"}), 400

    # Insert user (plain password for now)
    supabase.table("users").insert({
        "phone": phone,
        "name": name,
        "referral": referral,
        "password": password,
        "is_verified": True
    }).execute()

    # Generate token
    token = create_jwt({"phone": phone})

    return jsonify({
        "success": True,
        "message": "Registered successfully",
        "token": token
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

    # Get user
    user = supabase.table("users").select("*").eq("phone", phone).execute()
    if not user.data:
        return jsonify({"success": False, "message": "User not found"}), 404

    user = user.data[0]

    # Plain password check
    if user.get("password") != password:
        return jsonify({"success": False, "message": "Invalid password"}), 400

    token = create_jwt({"phone": phone})

    return jsonify({"success": True, "token": token})


# ------------------------------
# Run server
# ------------------------------
if __name__ == "__main__":
    print("✅ APP RUNNING ON PORT", os.environ.get("PORT", 5000))
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
