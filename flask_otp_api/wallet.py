from flask import Blueprint, request, jsonify
from functools import wraps
import os, hashlib, hmac, time, uuid, requests
from datetime import datetime, timezone, timedelta
from supabase import create_client

# ── Config from environment ──────────────────────────────
SUPABASE_URL         = os.environ.get("SUPABASE_URL")
SUPABASE_KEY         = os.environ.get("SUPABASE_KEY")
NOWPAY_API_KEY       = os.environ.get("NOWPAY_API_KEY")
NOWPAY_IPN_SECRET    = os.environ.get("NOWPAY_IPN_SECRET")
WALLET_SECRET        = os.environ.get("WALLET_SECRET")
ADMIN_SECRET         = os.environ.get("ADMIN_SECRET")

NOWPAY_BASE = "https://api.nowpayments.io/v1"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
wallet = Blueprint("wallet", __name__)

# ── Rate limiting ────────────────────────────────────────
rate_store = {}
def rate_limit(max_calls=5, window=60):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            key = request.remote_addr + f.__name__
            now = time.time()
            calls = [t for t in rate_store.get(key, []) if now - t < window]
            if len(calls) >= max_calls:
                return jsonify({"success": False, "error": "Too many requests. Try again later."}), 429
            calls.append(now)
            rate_store[key] = calls
            return f(*args, **kwargs)
        return wrapped
    return decorator

from utils import decode_jwt

# ── Auth middleware ──────────────────────────────────────
def wallet_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        try:
            payload = decode_jwt(token)
            user_id = payload.get("user_id")
            if not user_id:
                return jsonify({"success": False, "error": "Invalid token"}), 401
            result = supabase.table("users").select("id,phone,is_banned").eq("id", user_id).single().execute()
            if not result.data:
                return jsonify({"success": False, "error": "User not found"}), 401
            if result.data.get("is_banned"):
                return jsonify({"success": False, "error": "Account suspended"}), 403
            request.user = result.data
        except Exception as e:
            print(f"Auth error: {e}")
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ── PIN helpers ──────────────────────────────────────────
def hash_pin(pin):
    return hashlib.sha256((pin + WALLET_SECRET).encode()).hexdigest()

# ── NOWPayments helpers ──────────────────────────────────
def nowpay_headers():
    return {"x-api-key": NOWPAY_API_KEY, "Content-Type": "application/json"}

def create_nowpay_deposit(user_id, amount_usd=5):
    """Create a NOWPayments payment for USDT TRC20 deposit"""
    try:
        payload = {
            "price_amount": amount_usd,
            "price_currency": "usd",
            "pay_currency": "usdttrc20",
            "order_id": str(user_id),
            "order_description": f"Protege Vault deposit for user {user_id}",
            "ipn_callback_url": f"{os.environ.get('APP_URL', 'https://sample-api-1-ryj7.onrender.com')}/api/wallet/webhook/nowpayments"
        }
        print(f"NOWPayments request: {payload}")
        res = requests.post(f"{NOWPAY_BASE}/payment", headers=nowpay_headers(), json=payload)
        print(f"NOWPayments response {res.status_code}: {res.text}")
        if res.status_code in [200, 201]:
            return res.json()
        return None
    except Exception as e:
        print(f"NOWPayments deposit exception: {e}")
        return None

def create_nowpay_payout(address, amount_usdt):
    """Send USDT TRC20 to a wallet address via NOWPayments custody"""
    try:
        withdrawal_id = str(uuid.uuid4())
        res = requests.post(f"{NOWPAY_BASE}/payout", headers=nowpay_headers(), json={
            "withdrawals": [{
                "address": address,
                "currency": "usdttrc20",
                "amount": str(amount_usdt),
                "ipn_callback_url": f"{os.environ.get('APP_URL', '')}/api/wallet/webhook/nowpayments",
                "unique_external_id": withdrawal_id
            }]
        })
        if res.status_code in [200, 201]:
            data = res.json()
            return {"success": True, "data": data, "withdrawal_id": withdrawal_id}
        print(f"NOWPayments payout error: {res.text}")
        return {"success": False, "error": res.text}
    except Exception as e:
        print(f"NOWPayments payout exception: {e}")
        return {"success": False, "error": str(e)}

def verify_ipn_signature(request_body, signature):
    """Verify NOWPayments IPN webhook signature"""
    try:
        expected = hmac.new(
            NOWPAY_IPN_SECRET.encode(),
            request_body,
            hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except:
        return False

# ════════════════════════════════════════════════════════
#  PIN ROUTES
# ════════════════════════════════════════════════════════

@wallet.route("/api/wallet/pin/setup", methods=["POST"])
@wallet_auth
@rate_limit(max_calls=5, window=300)
def setup_pin():
    data = request.json or {}
    pin = str(data.get("pin", "")).strip()

    if len(pin) != 6 or not pin.isdigit():
        return jsonify({"success": False, "error": "PIN must be exactly 6 digits"}), 400

    user_id = request.user["id"]
    existing = supabase.table("crypto_wallets").select("id,pin_hash").eq("user_id", user_id).execute()

    if existing.data and existing.data[0].get("pin_hash"):
        return jsonify({"success": False, "error": "PIN already set"}), 400

    pin_hash = hash_pin(pin)
    if existing.data:
        supabase.table("crypto_wallets").update({"pin_hash": pin_hash}).eq("user_id", user_id).execute()
    else:
        supabase.table("crypto_wallets").insert({"user_id": user_id, "pin_hash": pin_hash}).execute()

    return jsonify({"success": True, "message": "PIN created successfully"})


@wallet.route("/api/wallet/pin/verify", methods=["POST"])
@wallet_auth
@rate_limit(max_calls=5, window=300)
def verify_pin():
    data = request.json or {}
    pin = str(data.get("pin", "")).strip()

    if len(pin) != 6 or not pin.isdigit():
        return jsonify({"success": False, "error": "Invalid PIN format"}), 400

    user_id = request.user["id"]
    result = supabase.table("crypto_wallets").select(
        "pin_hash,locked_until,failed_attempts"
    ).eq("user_id", user_id).execute()

    if not result.data or not result.data[0].get("pin_hash"):
        return jsonify({"success": False, "error": "No PIN set", "needs_setup": True}), 400

    wallet_row = result.data[0]

    # Check lockout
    locked_until = wallet_row.get("locked_until")
    if locked_until:
        locked_dt = datetime.fromisoformat(locked_until.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) < locked_dt:
            mins = int((locked_dt - datetime.now(timezone.utc)).total_seconds() / 60)
            return jsonify({"success": False, "error": f"Wallet locked. Try again in {mins} minutes."}), 429

    if wallet_row["pin_hash"] != hash_pin(pin):
        attempts = (wallet_row.get("failed_attempts") or 0) + 1
        update = {"failed_attempts": attempts}
        if attempts >= 5:
            update["locked_until"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        supabase.table("crypto_wallets").update(update).eq("user_id", user_id).execute()
        left = max(0, 5 - attempts)
        return jsonify({"success": False, "error": f"Wrong PIN. {left} attempts left."}), 401

    # Reset on success + issue session token
    session_token = str(uuid.uuid4())
    supabase.table("crypto_wallets").update({
        "failed_attempts": 0,
        "locked_until": None,
        "session_token": session_token,
        "session_expires": int((datetime.now(timezone.utc) + timedelta(minutes=30)).timestamp())
    }).eq("user_id", user_id).execute()

    return jsonify({"success": True, "session_token": session_token})

# ════════════════════════════════════════════════════════
#  DEPOSIT ROUTES
# ════════════════════════════════════════════════════════

@wallet.route("/api/wallet/deposit/create", methods=["POST"])
@wallet_auth
@rate_limit(max_calls=10, window=60)
def create_deposit():
    data = request.json or {}
    amount = float(data.get("amount", 5))

    if amount < 10:
        return jsonify({"success": False, "error": "Minimum deposit is $10 USDT"}), 400

    user_id = request.user["id"]

    # Create NOWPayments payment
    payment = create_nowpay_deposit(user_id, amount)
    if not payment:
        return jsonify({"success": False, "error": "NOWPayments API failed. Check Render logs for details."}), 500

    payment_id = payment.get("payment_id")
    pay_address = payment.get("pay_address")
    pay_amount  = payment.get("pay_amount")

    # Save pending deposit
    supabase.table("crypto_transactions").insert({
        "user_id": user_id,
        "type": "deposit",
        "amount": amount,
        "status": "pending",
        "tx_hash": str(payment_id),
        "asset": "USDT_TRC20",
        "destination": pay_address,
        "created_at": datetime.now(timezone.utc).isoformat()
    }).execute()

    # Save address to wallet row
    existing = supabase.table("crypto_wallets").select("id").eq("user_id", user_id).execute()
    if existing.data:
        supabase.table("crypto_wallets").update({"deposit_address": pay_address}).eq("user_id", user_id).execute()
    else:
        supabase.table("crypto_wallets").insert({"user_id": user_id, "deposit_address": pay_address}).execute()

    return jsonify({
        "success": True,
        "payment_id": payment_id,
        "address": pay_address,
        "amount": pay_amount,
        "currency": "USDT TRC20",
        "network": "TRON"
    })


@wallet.route("/api/wallet/balance", methods=["GET"])
@wallet_auth
def get_balance():
    user_id = request.user["id"]
    result = supabase.table("crypto_wallets").select("usdt_balance,deposit_address").eq("user_id", user_id).execute()
    if not result.data:
        return jsonify({"success": True, "balance": 0.0, "coin": "USDT", "network": "TRC20"})
    return jsonify({
        "success": True,
        "balance": float(result.data[0].get("usdt_balance") or 0),
        "address": result.data[0].get("deposit_address"),
        "coin": "USDT",
        "network": "TRC20"
    })


@wallet.route("/api/wallet/transactions", methods=["GET"])
@wallet_auth
def get_transactions():
    user_id = request.user["id"]
    result = supabase.table("crypto_transactions") \
        .select("*").eq("user_id", user_id) \
        .order("created_at", desc=True).limit(50).execute()
    return jsonify({"success": True, "transactions": result.data or []})

# ════════════════════════════════════════════════════════
#  WITHDRAWAL ROUTES
# ════════════════════════════════════════════════════════

@wallet.route("/api/wallet/withdraw", methods=["POST"])
@wallet_auth
@rate_limit(max_calls=3, window=3600)
def request_withdrawal():
    data = request.json or {}
    amount          = float(data.get("amount", 0))
    withdraw_type   = data.get("type", "crypto")
    destination     = data.get("destination", "").strip()
    idempotency_key = data.get("idempotency_key", str(uuid.uuid4()))

    user_id = request.user["id"]

    if amount < 1:
        return jsonify({"success": False, "error": "Minimum withdrawal is $1 USDT"}), 400
    if not destination:
        return jsonify({"success": False, "error": "Destination is required"}), 400

    # Idempotency check
    existing = supabase.table("crypto_withdrawal_requests") \
        .select("id,status").eq("idempotency_key", idempotency_key).execute()
    if existing.data:
        return jsonify({"success": True, "message": "Already submitted", "status": existing.data[0]["status"]})

    # Balance check
    wallet_row = supabase.table("crypto_wallets").select("usdt_balance").eq("user_id", user_id).execute()
    if not wallet_row.data or float(wallet_row.data[0].get("usdt_balance") or 0) < amount:
        return jsonify({"success": False, "error": "Insufficient balance"}), 400

    # Deduct balance immediately
    new_balance = float(wallet_row.data[0]["usdt_balance"]) - amount
    supabase.table("crypto_wallets").update({"usdt_balance": new_balance}).eq("user_id", user_id).execute()

    payout_result = None

    # Auto payout for crypto withdrawals via NOWPayments
    if withdraw_type == "crypto":
        payout_result = create_nowpay_payout(destination, amount)

    # Save withdrawal request
    supabase.table("crypto_withdrawal_requests").insert({
        "user_id": user_id,
        "amount": amount,
        "type": withdraw_type,
        "destination": destination,
        "status": "processing" if (payout_result and payout_result.get("success")) else "pending",
        "idempotency_key": idempotency_key,
        "nowpay_withdrawal_id": payout_result.get("withdrawal_id") if payout_result else None,
        "created_at": datetime.now(timezone.utc).isoformat()
    }).execute()

    # Log transaction
    supabase.table("crypto_transactions").insert({
        "user_id": user_id,
        "type": "withdrawal",
        "amount": amount,
        "status": "processing" if (payout_result and payout_result.get("success")) else "pending",
        "destination": destination,
        "withdraw_type": withdraw_type,
        "created_at": datetime.now(timezone.utc).isoformat()
    }).execute()

    if withdraw_type == "crypto" and payout_result and payout_result.get("success"):
        return jsonify({"success": True, "message": "Withdrawal processing! USDT will arrive in your wallet shortly."})
    elif withdraw_type == "bank":
        return jsonify({"success": True, "message": "Bank withdrawal submitted! You will receive NGN within 24 hours."})
    else:
        return jsonify({"success": True, "message": "Withdrawal submitted and will be processed shortly."})

# ════════════════════════════════════════════════════════
#  NOWPAYMENTS WEBHOOK
# ════════════════════════════════════════════════════════

@wallet.route("/api/wallet/webhook/nowpayments", methods=["POST"])
def nowpay_webhook():
    # Verify signature
    sig = request.headers.get("x-nowpayments-sig", "")
    if NOWPAY_IPN_SECRET and sig:
        if not verify_ipn_signature(request.data, sig):
            print("Invalid NOWPayments webhook signature")
            return jsonify({"error": "Invalid signature"}), 401

    data = request.json or {}
    print(f"NOWPayments webhook: {data}")

    payment_status  = data.get("payment_status", "")
    payment_id      = str(data.get("payment_id", ""))
    order_id        = data.get("order_id", "")  # this is user_id
    actually_paid   = float(data.get("actually_paid", 0))
    pay_currency    = data.get("pay_currency", "")

    # Only process confirmed/finished deposits
    if payment_status in ["finished", "confirmed", "partially_paid"]:
        if not order_id or actually_paid <= 0:
            return jsonify({"status": "ignored"}), 200

        # Prevent duplicate processing
        existing = supabase.table("crypto_transactions") \
            .select("id").eq("tx_hash", payment_id).eq("status", "confirmed").execute()
        if existing.data:
            return jsonify({"status": "already processed"}), 200

        user_id = order_id

        # Credit balance
        wallet_row = supabase.table("crypto_wallets").select("usdt_balance").eq("user_id", user_id).execute()
        if wallet_row.data:
            new_bal = float(wallet_row.data[0].get("usdt_balance") or 0) + actually_paid
            supabase.table("crypto_wallets").update({
                "usdt_balance": new_bal,
                "last_deposit_at": datetime.now(timezone.utc).isoformat()
            }).eq("user_id", user_id).execute()
        else:
            supabase.table("crypto_wallets").insert({
                "user_id": user_id,
                "usdt_balance": actually_paid,
                "last_deposit_at": datetime.now(timezone.utc).isoformat()
            }).execute()

        # Update transaction status
        supabase.table("crypto_transactions").update({
            "status": "confirmed",
            "amount": actually_paid
        }).eq("tx_hash", payment_id).execute()

        print(f"Deposit confirmed: {actually_paid} USDT for user {user_id}")

    # Handle payout/withdrawal webhook
    elif payment_status in ["payout_completed"]:
        withdrawal_id = data.get("unique_external_id", "")
        if withdrawal_id:
            supabase.table("crypto_withdrawal_requests").update({
                "status": "approved"
            }).eq("nowpay_withdrawal_id", withdrawal_id).execute()
            supabase.table("crypto_transactions").update({
                "status": "completed"
            }).eq("withdraw_type", "crypto").eq("status", "processing").execute()

    return jsonify({"status": "ok"}), 200

# ════════════════════════════════════════════════════════
#  ADMIN ROUTE
# ════════════════════════════════════════════════════════

@wallet.route("/api/wallet/admin/withdrawal/<req_id>", methods=["POST"])
def admin_withdrawal(req_id):
    if request.headers.get("x-admin-key") != ADMIN_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    data   = request.json or {}
    action = data.get("action")

    if action not in ["approve", "decline"]:
        return jsonify({"error": "Invalid action"}), 400

    req = supabase.table("crypto_withdrawal_requests").select("*").eq("id", req_id).execute()
    if not req.data:
        return jsonify({"error": "Not found"}), 404

    wr = req.data[0]

    if action == "approve":
        supabase.table("crypto_withdrawal_requests").update({"status": "approved"}).eq("id", req_id).execute()
        supabase.table("crypto_transactions").update({"status": "completed"}) \
            .eq("user_id", wr["user_id"]).eq("type", "withdrawal").eq("status", "pending").execute()
        return jsonify({"success": True, "message": "Approved"})

    elif action == "decline":
        # Refund
        w = supabase.table("crypto_wallets").select("usdt_balance").eq("user_id", wr["user_id"]).execute()
        if w.data:
            refunded = float(w.data[0].get("usdt_balance") or 0) + float(wr["amount"])
            supabase.table("crypto_wallets").update({"usdt_balance": refunded}).eq("user_id", wr["user_id"]).execute()
        supabase.table("crypto_withdrawal_requests").update({"status": "declined"}).eq("id", req_id).execute()
        supabase.table("crypto_transactions").update({"status": "refunded"}) \
            .eq("user_id", wr["user_id"]).eq("type", "withdrawal").eq("status", "pending").execute()
        return jsonify({"success": True, "message": "Declined and refunded"})
