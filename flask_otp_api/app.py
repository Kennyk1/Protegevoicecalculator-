import os
import random
import string
from flask import Flask, request, jsonify
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from utils import create_jwt, decode_jwt
from flask_cors import CORS
from chat import chat_bp

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})
app.register_blueprint(chat_bp)

print("🚀 APP STARTING...")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ══════════════════════════════
# HELPERS
# ══════════════════════════════

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

def get_request_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)

def verify_device_or_ip(user):
    """
    Returns True if the request comes from the same device OR same IP as signup.
    This protects sensitive actions like change-password, change-name.
    """
    request_ip = get_request_ip()
    request_device = request.json.get("device_id") if request.json else None

    ip_match = user.get("signup_ip") and user["signup_ip"] == request_ip
    device_match = request_device and user.get("device_id") and user["device_id"] == request_device

    return ip_match or device_match

def save_transaction(user_id, tx_type, amount, description):
    """Save a transaction record to Supabase."""
    try:
        supabase.table("transactions").insert({
            "user_id": user_id,
            "type": tx_type,
            "amount": amount,
            "description": description
        }).execute()
    except Exception as e:
        print(f"⚠️ Failed to save transaction: {e}")

# ══════════════════════════════
# HEALTH CHECK
# ══════════════════════════════

@app.route("/")
def home():
    return jsonify({"status": "APP RUNNING"})

# ══════════════════════════════
# SIGNUP
# ══════════════════════════════

@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.json
    phone = data.get("phone")
    name = data.get("name")
    password = data.get("password")
    referral_input = data.get("referral", "")
    device_id = data.get("device_id")
    signup_ip = get_request_ip()

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
        # Give referrer $0.10
        supabase.table("users").update({
            "balance": float(referrer_user["balance"]) + 0.1,
            "total_referrals": referrer_user["total_referrals"] + 1
        }).eq("id", referrer_user["id"]).execute()
        save_transaction(referrer_user["id"], "referral_bonus", 0.10, f"Referral bonus — {name} joined")

        # Give new user $0.50 signup bonus
        supabase.table("users").update({"balance": 0.5}).eq("id", user_id).execute()
        save_transaction(user_id, "signup_bonus", 0.50, f"Welcome bonus — joined with referral code {referral_input}")

    token = create_jwt({"user_id": user_id})

    return jsonify({
        "success": True,
        "message": "Registered successfully",
        "token": token,
        "referral_code": my_code,
        "bonus_applied": give_bonus
    })

# ══════════════════════════════
# LOGIN
# ══════════════════════════════

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

# ══════════════════════════════
# ME / PROFILE
# ══════════════════════════════

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

# ══════════════════════════════
# CHANGE NAME
# Body: { "name": "New Name", "device_id": "..." }
# Security: must match device_id OR signup_ip
# ══════════════════════════════

@app.route("/api/change-name", methods=["POST"])
def change_name():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    data = request.json
    new_name = data.get("name", "").strip()

    if not new_name or len(new_name) < 2:
        return jsonify({"success": False, "message": "Name must be at least 2 characters"}), 400

    if len(new_name) > 50:
        return jsonify({"success": False, "message": "Name too long (max 50 characters)"}), 400

    # Security check — must be same device or IP
    if not verify_device_or_ip(user):
        return jsonify({
            "success": False,
            "message": "Security check failed. This action must be done from your original device."
        }), 403

    supabase.table("users").update({"name": new_name}).eq("id", user["id"]).execute()

    return jsonify({"success": True, "message": "Name updated successfully", "name": new_name})

# ══════════════════════════════
# CHANGE PASSWORD
# Body: { "old_password": "...", "new_password": "...", "device_id": "..." }
# Security: must match device_id OR signup_ip + must know old password
# ══════════════════════════════

@app.route("/api/change-password", methods=["POST"])
def change_password():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    data = request.json
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")

    if not old_password or not new_password:
        return jsonify({"success": False, "message": "Old and new password required"}), 400

    if len(new_password) < 8:
        return jsonify({"success": False, "message": "New password must be at least 8 characters"}), 400

    if old_password == new_password:
        return jsonify({"success": False, "message": "New password must be different from old password"}), 400

    # Security check 1 — must be same device or IP
    if not verify_device_or_ip(user):
        return jsonify({
            "success": False,
            "message": "Security check failed. Password can only be changed from your original device."
        }), 403

    # Security check 2 — must know old password
    if user["password"] != old_password:
        return jsonify({"success": False, "message": "Current password is incorrect"}), 400

    supabase.table("users").update({"password": new_password}).eq("id", user["id"]).execute()

    return jsonify({"success": True, "message": "Password changed successfully"})

# ══════════════════════════════
# TRANSACTIONS
# Returns full transaction history for user
# ══════════════════════════════

@app.route("/api/transactions", methods=["GET"])
def transactions():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    try:
        rows = (
            supabase.table("transactions")
            .select("*")
            .eq("user_id", user["id"])
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return jsonify({"success": True, "transactions": rows.data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

# ══════════════════════════════
# SET WITHDRAWAL PIN
# Body: { "pin": "1234", "device_id": "..." }
# Sets a 4-6 digit PIN used to authorize withdrawals and transfers
# Security: must match device_id OR signup_ip
# ══════════════════════════════

@app.route("/api/set-withdrawal-pin", methods=["POST"])
def set_withdrawal_pin():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    data = request.json
    pin = str(data.get("pin", "")).strip()

    if not pin:
        return jsonify({"success": False, "message": "PIN is required"}), 400

    if not pin.isdigit():
        return jsonify({"success": False, "message": "PIN must be numbers only"}), 400

    if len(pin) < 4 or len(pin) > 6:
        return jsonify({"success": False, "message": "PIN must be 4 to 6 digits"}), 400

    # Security check — must be same device or IP
    if not verify_device_or_ip(user):
        return jsonify({
            "success": False,
            "message": "Security check failed. PIN can only be set from your original device."
        }), 403

    supabase.table("users").update({"withdrawal_pin": pin}).eq("id", user["id"]).execute()

    return jsonify({"success": True, "message": "Withdrawal PIN set successfully"})


# ══════════════════════════════
# CHANGE WITHDRAWAL PIN
# Body: { "old_pin": "1234", "new_pin": "5678", "device_id": "..." }
# ══════════════════════════════

@app.route("/api/change-withdrawal-pin", methods=["POST"])
def change_withdrawal_pin():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    data = request.json
    old_pin = str(data.get("old_pin", "")).strip()
    new_pin = str(data.get("new_pin", "")).strip()

    if not old_pin or not new_pin:
        return jsonify({"success": False, "message": "Old and new PIN required"}), 400

    if not new_pin.isdigit() or len(new_pin) < 4 or len(new_pin) > 6:
        return jsonify({"success": False, "message": "New PIN must be 4 to 6 digits"}), 400

    if old_pin == new_pin:
        return jsonify({"success": False, "message": "New PIN must be different from old PIN"}), 400

    # Security check
    if not verify_device_or_ip(user):
        return jsonify({
            "success": False,
            "message": "Security check failed. PIN can only be changed from your original device."
        }), 403

    # Verify old PIN
    if not user.get("withdrawal_pin"):
        return jsonify({"success": False, "message": "No withdrawal PIN set yet. Please set one first."}), 400

    if user["withdrawal_pin"] != old_pin:
        return jsonify({"success": False, "message": "Current PIN is incorrect"}), 400

    supabase.table("users").update({"withdrawal_pin": new_pin}).eq("id", user["id"]).execute()

    return jsonify({"success": True, "message": "Withdrawal PIN changed successfully"})


# ══════════════════════════════
# WITHDRAW
# Body: { "amount": 5.00, "method": "usdt_bep20|usdt_trc20|paypal",
#          "address": "wallet or paypal email", "pin": "1234", "device_id": "..." }
# Flow: deduct balance immediately → save withdrawal_request as pending
#       Admin sends money manually → marks completed or rejected
#       If rejected → balance refunded automatically
# ══════════════════════════════

@app.route("/api/withdraw", methods=["POST"])
def withdraw():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    data = request.json
    amount = float(data.get("amount", 0))
    method = data.get("method", "").strip()
    address = data.get("address", "").strip()
    pin = str(data.get("pin", "")).strip()

    # ── Validations ──
    if amount <= 0:
        return jsonify({"success": False, "message": "Invalid amount"}), 400

    if amount < 1.00:
        return jsonify({"success": False, "message": "Minimum withdrawal is $1.00"}), 400

    if float(user["balance"]) < amount:
        return jsonify({"success": False, "message": "Insufficient balance"}), 400

    valid_methods = ['usdt_bep20', 'usdt_trc20', 'paypal']
    if method not in valid_methods:
        return jsonify({"success": False, "message": "Invalid withdrawal method"}), 400

    if not address:
        return jsonify({"success": False, "message": "Wallet address or PayPal email is required"}), 400

    # ── Security check 1 — device or IP ──
    if not verify_device_or_ip(user):
        return jsonify({
            "success": False,
            "message": "Security check failed. Withdrawal must be done from your registered device."
        }), 403

    # ── Security check 2 — withdrawal PIN ──
    if not user.get("withdrawal_pin"):
        return jsonify({
            "success": False,
            "message": "Please set a withdrawal PIN in Settings before withdrawing."
        }), 403

    if not pin:
        return jsonify({"success": False, "message": "Withdrawal PIN is required"}), 400

    if user["withdrawal_pin"] != pin:
        return jsonify({"success": False, "message": "Incorrect withdrawal PIN"}), 403

    # ── Check no other pending request exists ──
    pending = supabase.table("withdrawal_requests") \
        .select("id") \
        .eq("user_id", user["id"]) \
        .eq("status", "pending") \
        .execute()
    if pending.data:
        return jsonify({
            "success": False,
            "message": "You already have a pending withdrawal. Please wait for it to be processed."
        }), 400

    # ── Deduct balance immediately ──
    new_balance = float(user["balance"]) - amount
    supabase.table("users").update({"balance": new_balance}).eq("id", user["id"]).execute()

    # ── Save to withdrawal_requests table (admin will process this) ──
    method_label = {
        'usdt_bep20': 'USDT (BEP-20)',
        'usdt_trc20': 'USDT (TRC-20)',
        'paypal': 'PayPal'
    }.get(method, method)

    supabase.table("withdrawal_requests").insert({
        "user_id": user["id"],
        "amount": amount,
        "method": method,
        "address": address,
        "status": "pending"
    }).execute()

    # ── Save to transaction history ──
    save_transaction(
        user["id"],
        "withdraw",
        -amount,
        f"Withdrawal via {method_label} to {address} — pending"
    )

    return jsonify({
        "success": True,
        "message": f"Withdrawal request submitted! ${amount:.2f} will be sent to your {method_label} address within 24 hours.",
        "new_balance": new_balance
    })


# ══════════════════════════════
# WITHDRAWAL REQUESTS — GET USER'S OWN
# ══════════════════════════════

@app.route("/api/withdrawals", methods=["GET"])
def get_withdrawals():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    try:
        rows = supabase.table("withdrawal_requests") \
            .select("*") \
            .eq("user_id", user["id"]) \
            .order("created_at", desc=True) \
            .limit(20) \
            .execute()
        return jsonify({"success": True, "withdrawals": rows.data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ══════════════════════════════
# TRANSFER
# Body: { "to_phone": "+2348012345678", "amount": 1.00, "pin": "1234", "device_id": "..." }
# ══════════════════════════════

@app.route("/api/transfer", methods=["POST"])
def transfer():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    data = request.json
    to_phone = data.get("to_phone", "").strip()
    amount = float(data.get("amount", 0))
    pin = str(data.get("pin", "")).strip()

    if amount <= 0:
        return jsonify({"success": False, "message": "Invalid amount"}), 400

    if float(user["balance"]) < amount:
        return jsonify({"success": False, "message": "Insufficient balance"}), 400

    if not to_phone:
        return jsonify({"success": False, "message": "Recipient phone number required"}), 400

    if to_phone == user["phone"]:
        return jsonify({"success": False, "message": "Cannot transfer to yourself"}), 400

    # Security check 1 — device or IP
    if not verify_device_or_ip(user):
        return jsonify({
            "success": False,
            "message": "Security check failed. Transfer must be done from your original device."
        }), 403

    # Security check 2 — withdrawal PIN
    if not user.get("withdrawal_pin"):
        return jsonify({
            "success": False,
            "message": "Please set a withdrawal PIN in Settings before transferring."
        }), 403

    if not pin:
        return jsonify({"success": False, "message": "Withdrawal PIN is required"}), 400

    if user["withdrawal_pin"] != pin:
        return jsonify({"success": False, "message": "Incorrect withdrawal PIN"}), 403

    # Find recipient
    recipient = supabase.table("users").select("*").eq("phone", to_phone).execute()
    if not recipient.data:
        return jsonify({"success": False, "message": "Recipient not found. Check the phone number."}), 404
    recipient = recipient.data[0]

    # Deduct from sender
    new_sender_balance = float(user["balance"]) - amount
    supabase.table("users").update({"balance": new_sender_balance}).eq("id", user["id"]).execute()
    save_transaction(user["id"], "transfer", -amount, f"Transfer to {recipient['name']} ({to_phone})")

    # Add to recipient
    new_recipient_balance = float(recipient["balance"]) + amount
    supabase.table("users").update({"balance": new_recipient_balance}).eq("id", recipient["id"]).execute()
    save_transaction(recipient["id"], "transfer", amount, f"Transfer from {user['name']} ({user['phone']})")

    return jsonify({
        "success": True,
        "message": f"${amount:.2f} transferred to {recipient['name']}",
        "new_balance": new_sender_balance
    })

# ══════════════════════════════
# BALANCE
# ══════════════════════════════

@app.route("/api/balance", methods=["GET"])
def balance():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401
    return jsonify({"success": True, "balance": user["balance"]})

# ══════════════════════════════
# LOGOUT
# ══════════════════════════════

@app.route("/api/logout", methods=["POST"])
def logout():
    return jsonify({"success": True, "message": "Logged out successfully"})

# ══════════════════════════════
# RUN
# ══════════════════════════════

if __name__ == "__main__":
    print("✅ APP RUNNING ON PORT", os.environ.get("PORT", 5000))
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
