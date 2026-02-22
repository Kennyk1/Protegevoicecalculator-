import os
import random
import string
from flask import Flask, request, jsonify
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from utils import create_jwt, decode_jwt
from flask_cors import CORS

from chat import chat_bp                          # <-- ADD THIS

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

app.register_blueprint(chat_bp)

print("🚀 APP STARTING...")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def generate_referral_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

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

@app.route("/")
def home():
    return jsonify({"status": "APP RUNNING"})

@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.json
    phone = data.get("phone")
    name = data.get("name")
    password = data.get("password")
    referral_input = data.get("referral", "")
    device_id = data.get("device_id")
    signup_ip = request.headers.get("X-Forwarded-For", request.remote_addr)

    if not phone or not name or not password or not device_id:
        return jsonify({"success": False, "message": "Phone, name, password and device_id required"}), 400

    existing = supabase.table("users").select("id").eq("phone", phone).execute()
    if existing.data:
        return jsonify({"success": False, "message": "User already exists"}), 400

    ip_users = supabase.table("users").select("id").eq("signup_ip", signup_ip).execute()
    ip_limit_reached = len(ip_users.data) >= 3

    device_users = supabase.table("users").select("id").eq("device_id", device_id).execute()
    device_used = len(device_users.data) > 0

    referrer_user = None
    give_bonus = False
    if referral_input:
        referrer = supabase.table("users").select("*").eq("referral_code", referral_input).execute()
        if not referrer.data:
            return jsonify({"success": False, "message": "Invalid referral code"}), 400
        referrer_user = referrer.data[0]
        if not ip_limit_reached and not device_used and referrer_user["device_id"] != device_id:
            give_bonus = True

    my_code = generate_referral_code()
    new_user = supabase.table("users").insert({
        "phone": phone,
        "name": name,
        "password": password,
        "referral_code": my_code,
        "referred_by": referral_input if referral_input else None,
        "balance": 0,
        "total_referrals": 0,
        "signup_ip": signup_ip,
        "device_id": device_id,
        "is_verified": True
    }).execute()

    user = new_user.data[0]
    user_id = user["id"]

    if give_bonus and referrer_user:
        supabase.table("users").update({
            "balance": float(referrer_user["balance"]) + 0.1,
            "total_referrals": referrer_user["total_referrals"] + 1
        }).eq("id", referrer_user["id"]).execute()
        supabase.table("users").update({"balance": 0.5}).eq("id", user_id).execute()

    token = create_jwt({"user_id": user_id})

    return jsonify({
        "success": True,
        "message": "Registered successfully",
        "token": token,
        "referral_code": my_code,
        "bonus_applied": give_bonus
    })

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    phone = data.get("phone")
    password = data.get("password")
    if not phone or not password:
        return jsonify({"success": False, "message": "Phone and password required"}), 400
    user = supabase.table("users").select("*").eq("phone", phone).execute()
    if not user.data:
        return jsonify({"success": False, "message": "User not found"}), 404
    user = user.data[0]
    if user["password"] != password:
        return jsonify({"success": False, "message": "Invalid password"}), 400
    token = create_jwt({"user_id": user["id"]})
    return jsonify({"success": True, "token": token})

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

@app.route("/api/balance", methods=["GET"])
def balance():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401
    return jsonify({"success": True, "balance": user["balance"]})

@app.route("/api/logout", methods=["POST"])
def logout():
    return jsonify({"success": True, "message": "Logged out successfully"})

if __name__ == "__main__":
    print("✅ APP RUNNING ON PORT", os.environ.get("PORT", 5000))
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
  
