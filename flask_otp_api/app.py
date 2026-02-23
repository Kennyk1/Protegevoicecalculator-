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

USD_TO_NGN = 1600


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


def save_transaction(user_id, tx_type, amount, description):
    try:
        supabase.table("transactions").insert({
            "user_id": user_id,
            "type": tx_type,
            "amount": amount,
            "description": description
        }).execute()
    except Exception as e:
        print(f"⚠️ Failed to save transaction: {e}")


@app.route("/")
def home():
    return jsonify({"status": "APP RUNNING"})


@app.route("/api/rate", methods=["GET"])
def get_rate():
    return jsonify({"success": True, "usd_to_ngn": USD_TO_NGN})


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
        supabase.table("users").update({
            "balance": float(referrer_user["balance"]) + 0.1,
            "total_referrals": referrer_user["total_referrals"] + 1
        }).eq("id", referrer_user["id"]).execute()
        save_transaction(referrer_user["id"], "referral_bonus", 0.10, f"Referral bonus — {name} joined")
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
            "total_referrals": user["total_referrals"],
            "is_banned": user.get("is_banned", False)
        }
    })


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

    supabase.table("users").update({"name": new_name}).eq("id", user["id"]).execute()
    return jsonify({"success": True, "message": "Name updated successfully"})


@app.route("/api/change-password", methods=["POST"])
def change_password():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    data = request.json
    old_password = data.get("old_password", "").strip()
    new_password = data.get("new_password", "").strip()

    if not old_password or not new_password:
        return jsonify({"success": False, "message": "Old and new password required"}), 400
    if len(new_password) < 6:
        return jsonify({"success": False, "message": "New password must be at least 6 characters"}), 400
    if user["password"] != old_password:
        return jsonify({"success": False, "message": "Current password is incorrect"}), 400

    supabase.table("users").update({"password": new_password}).eq("id", user["id"]).execute()
    return jsonify({"success": True, "message": "Password changed successfully"})


@app.route("/api/set-withdrawal-pin", methods=["POST"])
def set_withdrawal_pin():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    data = request.json
    pin = str(data.get("pin", "")).strip()

    if not pin or not pin.isdigit() or len(pin) < 4 or len(pin) > 6:
        return jsonify({"success": False, "message": "PIN must be 4 to 6 digits"}), 400

    supabase.table("users").update({"withdrawal_pin": pin}).eq("id", user["id"]).execute()
    return jsonify({"success": True, "message": "Withdrawal PIN set successfully"})


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
    if not user.get("withdrawal_pin"):
        return jsonify({"success": False, "message": "No withdrawal PIN set yet. Please set one first."}), 400
    if user["withdrawal_pin"] != old_pin:
        return jsonify({"success": False, "message": "Current PIN is incorrect"}), 400

    supabase.table("users").update({"withdrawal_pin": new_pin}).eq("id", user["id"]).execute()
    return jsonify({"success": True, "message": "Withdrawal PIN changed successfully"})


@app.route("/api/withdraw", methods=["POST"])
def withdraw():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    data = request.json
    amount_raw = float(data.get("amount", 0))
    method = data.get("method", "").strip()
    address = data.get("address", "").strip()
    account_name = data.get("account_name", "").strip()
    pin = str(data.get("pin", "")).strip()
    currency = data.get("currency", "usd")

    amount_usd = amount_raw / USD_TO_NGN if currency == "ngn" else amount_raw

    if amount_usd <= 0:
        return jsonify({"success": False, "message": "Invalid amount"}), 400
    if amount_usd < 1.0:
        return jsonify({"success": False, "message": "Minimum withdrawal is $1.00 (₦{:,.0f})".format(USD_TO_NGN)}), 400
    if float(user["balance"]) < amount_usd:
        return jsonify({"success": False, "message": "Insufficient balance"}), 400

    valid_methods = ['usdt_bep20', 'usdt_trc20', 'paypal', 'ngn_opay', 'ngn_palmpay']
    if method not in valid_methods:
        return jsonify({"success": False, "message": f"Invalid withdrawal method: {method}"}), 400
    if not address:
        return jsonify({"success": False, "message": "Wallet address or account number is required"}), 400
    if method in ['ngn_opay', 'ngn_palmpay'] and not account_name:
        return jsonify({"success": False, "message": "Account name is required for NGN withdrawal"}), 400

    if not user.get("withdrawal_pin"):
        return jsonify({"success": False, "message": "Please set a withdrawal PIN in Settings before withdrawing."}), 403
    if not pin:
        return jsonify({"success": False, "message": "Withdrawal PIN is required"}), 400
    if user["withdrawal_pin"] != pin:
        return jsonify({"success": False, "message": "Incorrect withdrawal PIN"}), 403

    pending = supabase.table("withdrawal_requests").select("id").eq("user_id", user["id"]).eq("status", "pending").execute()
    if pending.data:
        return jsonify({"success": False, "message": "You already have a pending withdrawal. Please wait for it to be processed."}), 400

    fee_usd = round(amount_usd * 0.20, 4) if method in ['ngn_opay', 'ngn_palmpay'] else 0
    net_usd = amount_usd - fee_usd

    method_label = {
        'usdt_bep20': 'USDT (BEP-20)', 'usdt_trc20': 'USDT (TRC-20)',
        'paypal': 'PayPal', 'ngn_opay': 'OPay (NGN)', 'ngn_palmpay': 'PalmPay (NGN)'
    }.get(method, method)

    new_balance = float(user["balance"]) - amount_usd
    supabase.table("users").update({"balance": new_balance}).eq("id", user["id"]).execute()

    supabase.table("withdrawal_requests").insert({
        "user_id": user["id"],
        "amount": amount_usd,
        "method": method,
        "address": address,
        "account_name": account_name if account_name else None,
        "status": "pending"
    }).execute()

    desc = f"Withdrawal via {method_label} to {account_name + ' | ' if account_name else ''}{address}"
    save_transaction(user["id"], "withdraw", -amount_usd, desc)

    return jsonify({
        "success": True,
        "message": f"Withdrawal submitted! Funds arrive within 24 hours.",
        "new_balance": new_balance,
        "fee_usd": fee_usd,
        "net_usd": net_usd
    })


@app.route("/api/withdrawals", methods=["GET"])
def get_withdrawals():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401
    try:
        rows = supabase.table("withdrawal_requests").select("*").eq("user_id", user["id"]).order("created_at", desc=True).limit(20).execute()
        return jsonify({"success": True, "withdrawals": rows.data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


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

    if not user.get("withdrawal_pin"):
        return jsonify({"success": False, "message": "Please set a withdrawal PIN in Settings before transferring."}), 403
    if not pin:
        return jsonify({"success": False, "message": "Withdrawal PIN is required"}), 400
    if user["withdrawal_pin"] != pin:
        return jsonify({"success": False, "message": "Incorrect withdrawal PIN"}), 403

    recipient = supabase.table("users").select("*").eq("phone", to_phone).execute()
    if not recipient.data:
        return jsonify({"success": False, "message": "Recipient not found. Check the phone number."}), 404
    recipient = recipient.data[0]

    new_sender_balance = float(user["balance"]) - amount
    supabase.table("users").update({"balance": new_sender_balance}).eq("id", user["id"]).execute()
    save_transaction(user["id"], "transfer", -amount, f"Transfer to {recipient['name']}|{to_phone}")

    new_recipient_balance = float(recipient["balance"]) + amount
    supabase.table("users").update({"balance": new_recipient_balance}).eq("id", recipient["id"]).execute()
    save_transaction(recipient["id"], "transfer", amount, f"Transfer from {user['name']}|{user['phone']}")

    try:
        existing = supabase.table("recent_transfers").select("*").eq("user_id", user["id"]).eq("recipient_phone", to_phone).execute()
        if existing.data:
            supabase.table("recent_transfers").update({
                "last_amount": amount,
                "recipient_name": recipient["name"],
                "transfer_count": existing.data[0]["transfer_count"] + 1,
                "last_transferred_at": "now()"
            }).eq("id", existing.data[0]["id"]).execute()
        else:
            supabase.table("recent_transfers").insert({
                "user_id": user["id"],
                "recipient_phone": to_phone,
                "recipient_name": recipient["name"],
                "last_amount": amount,
                "transfer_count": 1
            }).execute()
    except Exception as e:
        print(f"⚠️ Failed to save recent transfer: {e}")

    return jsonify({
        "success": True,
        "message": f"${amount:.2f} transferred to {recipient['name']} successfully!",
        "recipient_name": recipient["name"],
        "new_balance": new_sender_balance
    })


@app.route("/api/user-by-phone", methods=["GET"])
def user_by_phone():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    phone = request.args.get("phone", "").strip()
    if not phone:
        return jsonify({"success": False, "message": "Phone number required"}), 400
    if phone == user["phone"]:
        return jsonify({"success": False, "message": "That's your own number"}), 400

    result = supabase.table("users").select("name, phone").eq("phone", phone).execute()
    if not result.data:
        return jsonify({"success": False, "message": "User not found on Protege"}), 404

    return jsonify({"success": True, "name": result.data[0]["name"], "phone": result.data[0]["phone"]})


@app.route("/api/recent-transfers", methods=["GET"])
def recent_transfers():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401
    try:
        rows = supabase.table("recent_transfers").select("*").eq("user_id", user["id"]).order("last_transferred_at", desc=True).limit(10).execute()
        return jsonify({"success": True, "recent": rows.data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/transactions", methods=["GET"])
def transactions():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401

    tx_filter = request.args.get("filter", "all")
    limit = int(request.args.get("limit", 50))

    try:
        if tx_filter == "sent":
            rows = supabase.table("transactions").select("*").eq("user_id", user["id"]).lt("amount", 0).order("created_at", desc=True).limit(limit).execute()
        elif tx_filter == "received":
            rows = supabase.table("transactions").select("*").eq("user_id", user["id"]).gt("amount", 0).order("created_at", desc=True).limit(limit).execute()
        elif tx_filter == "withdraw":
            rows = supabase.table("transactions").select("*").eq("user_id", user["id"]).eq("type", "withdraw").order("created_at", desc=True).limit(limit).execute()
        else:
            rows = supabase.table("transactions").select("*").eq("user_id", user["id"]).order("created_at", desc=True).limit(limit).execute()
        return jsonify({"success": True, "transactions": rows.data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/balance", methods=["GET"])
def balance():
    user, error = get_current_user()
    if error:
        return jsonify({"success": False, "message": error}), 401
    return jsonify({"success": True, "balance": user["balance"]})


@app.route("/api/logout", methods=["POST"])
def logout():
    return jsonify({"success": True, "message": "Logged out successfully"})


ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "protege_admin_2024")


def verify_admin():
    key = request.headers.get("X-Admin-Key")
    if key != ADMIN_SECRET:
        return False
    return True


@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        users = supabase.table("users").select("id, balance, is_banned").execute()
        withdrawals = supabase.table("withdrawal_requests").select("id, amount, status").execute()
        transactions = supabase.table("transactions").select("id, amount").execute()

        total_users = len(users.data)
        total_balance = sum(float(u.get("balance", 0)) for u in users.data)
        banned_users = sum(1 for u in users.data if u.get("is_banned"))
        pending_withdrawals = sum(1 for w in withdrawals.data if w["status"] == "pending")
        pending_amount = sum(float(w["amount"]) for w in withdrawals.data if w["status"] == "pending")
        total_paid = sum(float(w["amount"]) for w in withdrawals.data if w["status"] == "approved")

        return jsonify({
            "success": True,
            "stats": {
                "total_users": total_users,
                "total_balance": round(total_balance, 2),
                "banned_users": banned_users,
                "pending_withdrawals": pending_withdrawals,
                "pending_amount": round(pending_amount, 2),
                "total_paid": round(total_paid, 2)
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/users", methods=["GET"])
def admin_users():
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        search = request.args.get("search", "").strip()
        rows = supabase.table("users").select("*").order("created_at", desc=True).execute()
        users = rows.data
        if search:
            users = [u for u in users if search.lower() in u["name"].lower() or search in u["phone"]]
        safe = [{
            "id": u["id"], "name": u["name"], "phone": u["phone"],
            "balance": u["balance"], "referral_code": u["referral_code"],
            "total_referrals": u.get("total_referrals", 0),
            "is_banned": u.get("is_banned", False),
            "created_at": u.get("created_at")
        } for u in users]
        return jsonify({"success": True, "users": safe, "total": len(safe)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/user/<user_id>", methods=["GET"])
def admin_user_detail(user_id):
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        user = supabase.table("users").select("*").eq("id", user_id).execute()
        if not user.data:
            return jsonify({"success": False, "message": "User not found"}), 404
        u = user.data[0]
        txs = supabase.table("transactions").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(20).execute()
        wds = supabase.table("withdrawal_requests").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
        referrals = supabase.table("users").select("name, phone, created_at, balance, device_id, signup_ip, referred_by").eq("referred_by", u.get("referral_code")).execute()
        return jsonify({
            "success": True,
            "user": {
                "id": u["id"], "name": u["name"], "phone": u["phone"],
                "balance": u["balance"], "referral_code": u["referral_code"],
                "total_referrals": u.get("total_referrals", 0),
                "is_banned": u.get("is_banned", False),
                "created_at": u.get("created_at")
            },
            "transactions": txs.data,
            "withdrawals": wds.data,
            "referrals": referrals.data
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/user/<user_id>/balance", methods=["POST"])
def admin_update_balance(user_id):
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        data = request.json
        action = data.get("action")
        amount = float(data.get("amount", 0))
        note = data.get("note", "Admin adjustment")
        if amount <= 0:
            return jsonify({"success": False, "message": "Amount must be greater than 0"}), 400

        user = supabase.table("users").select("*").eq("id", user_id).execute()
        if not user.data:
            return jsonify({"success": False, "message": "User not found"}), 404
        u = user.data[0]
        current = float(u["balance"])

        if action == "add":
            new_balance = current + amount
            save_transaction(user_id, "deposit", amount, f"Admin credit: {note}")
        elif action == "deduct":
            if current < amount:
                return jsonify({"success": False, "message": "Insufficient user balance"}), 400
            new_balance = current - amount
            save_transaction(user_id, "withdraw", -amount, f"Admin debit: {note}")
        else:
            return jsonify({"success": False, "message": "Action must be add or deduct"}), 400

        supabase.table("users").update({"balance": new_balance}).eq("id", user_id).execute()
        return jsonify({"success": True, "message": f"Balance updated", "new_balance": round(new_balance, 2)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/user/<user_id>/ban", methods=["POST"])
def admin_ban_user(user_id):
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        data = request.json
        ban = data.get("ban", True)
        supabase.table("users").update({"is_banned": ban}).eq("id", user_id).execute()
        return jsonify({"success": True, "message": "Banned" if ban else "Unbanned"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/user/<user_id>/delete", methods=["DELETE"])
def admin_delete_user(user_id):
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        supabase.table("transactions").delete().eq("user_id", user_id).execute()
        supabase.table("withdrawal_requests").delete().eq("user_id", user_id).execute()
        supabase.table("recent_transfers").delete().eq("user_id", user_id).execute()
        supabase.table("users").delete().eq("id", user_id).execute()
        return jsonify({"success": True, "message": "User deleted"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/withdrawals", methods=["GET"])
def admin_withdrawals():
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        status_filter = request.args.get("status", "all")
        rows = supabase.table("withdrawal_requests").select("*, users(name, phone)").order("created_at", desc=True).execute()
        data = rows.data
        if status_filter != "all":
            data = [w for w in data if w["status"] == status_filter]
        return jsonify({"success": True, "withdrawals": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/withdrawal/<wd_id>/action", methods=["POST"])
def admin_withdrawal_action(wd_id):
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        data = request.json
        action = data.get("action")
        note = data.get("note", "")
        if action not in ["approved", "declined"]:
            return jsonify({"success": False, "message": "Action must be approved or declined"}), 400

        wd = supabase.table("withdrawal_requests").select("*").eq("id", wd_id).execute()
        if not wd.data:
            return jsonify({"success": False, "message": "Withdrawal not found"}), 404
        wd = wd.data[0]

        if wd["status"] != "pending":
            return jsonify({"success": False, "message": "Already processed"}), 400

        supabase.table("withdrawal_requests").update({
            "status": action,
            "admin_note": note
        }).eq("id", wd_id).execute()

        if action == "declined":
            user = supabase.table("users").select("*").eq("id", wd["user_id"]).execute()
            if user.data:
                u = user.data[0]
                refund = float(wd["amount"])
                supabase.table("users").update({"balance": float(u["balance"]) + refund}).eq("id", u["id"]).execute()
                save_transaction(u["id"], "deposit", refund, f"Withdrawal declined — refunded. {note}")

        return jsonify({"success": True, "message": f"Withdrawal {action}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/transactions", methods=["GET"])
def admin_transactions():
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        limit = int(request.args.get("limit", 100))
        rows = supabase.table("transactions").select("*, users(name, phone)").order("created_at", desc=True).limit(limit).execute()
        return jsonify({"success": True, "transactions": rows.data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/broadcast", methods=["POST"])
def admin_broadcast():
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        data = request.json
        message = data.get("message", "").strip()
        title = data.get("title", "Announcement").strip()
        if not message:
            return jsonify({"success": False, "message": "Message required"}), 400
        supabase.table("announcements").insert({
            "title": title,
            "message": message,
            "is_active": True
        }).execute()
        return jsonify({"success": True, "message": "Announcement sent"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/broadcasts", methods=["GET"])
def admin_get_broadcasts():
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        rows = supabase.table("announcements").select("*").order("created_at", desc=True).limit(20).execute()
        return jsonify({"success": True, "announcements": rows.data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/admin/broadcast/<ann_id>/delete", methods=["DELETE"])
def admin_delete_broadcast(ann_id):
    if not verify_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    try:
        supabase.table("announcements").delete().eq("id", ann_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/announcement", methods=["GET"])
def get_announcement():
    try:
        rows = supabase.table("announcements").select("*").eq("is_active", True).order("created_at", desc=True).limit(1).execute()
        if rows.data:
            return jsonify({"success": True, "announcement": rows.data[0]})
        return jsonify({"success": True, "announcement": None})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == "__main__":
    print("✅ APP RUNNING ON PORT", os.environ.get("PORT", 5000))
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
