from flask import Flask, request, jsonify
from functools import wraps
import os, hashlib, hmac, time, uuid, requests
from datetime import datetime, timezone
from supabase import create_client

# ── Config ──────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TATUM_API_KEY = os.environ.get("TATUM_API_KEY")
WALLET_SECRET = os.environ.get("WALLET_SECRET")
TATUM_WEBHOOK_SECRET = os.environ.get("TATUM_WEBHOOK_SECRET", "")

# Your master Tron wallet address (where all deposits go)
MASTER_TRON_ADDRESS = os.environ.get("MASTER_TRON_ADDRESS", "")

TATUM_BASE = "https://api.tatum.io/v3"
TRON_NODE  = "https://tron-mainnet.gateway.tatum.io/jsonrpc/"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

wallet_app = Flask(__name__)

# ── Rate limiting (simple in-memory) ────────────────────
rate_store = {}
def rate_limit(max_calls=5, window=60):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            key = request.remote_addr + f.__name__
            now = time.time()
            calls = [t for t in rate_store.get(key, []) if now - t < window]
            if len(calls) >= max_calls:
                return jsonify({"success": False, "error": "Rate limit exceeded. Try again later."}), 429
            calls.append(now)
            rate_store[key] = calls
            return f(*args, **kwargs)
        return wrapped
    return decorator

# ── Auth middleware ──────────────────────────────────────
def wallet_auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        try:
            result = supabase.table("users").select("id,phone,balance,is_banned").eq("token", token).single().execute()
            if not result.data:
                return jsonify({"success": False, "error": "Invalid token"}), 401
            if result.data.get("is_banned"):
                return jsonify({"success": False, "error": "Account suspended"}), 403
            request.user = result.data
        except:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ── PIN helpers ──────────────────────────────────────────
def hash_pin(pin):
    return hashlib.sha256((pin + WALLET_SECRET).encode()).hexdigest()

# ── Tatum helpers ────────────────────────────────────────
def tatum_headers():
    return {"x-api-key": TATUM_API_KEY, "Content-Type": "application/json"}

def generate_tron_address(user_id):
    """Generate a unique TRON deposit address for this user via Tatum"""
    try:
        # Use Tatum's offchain virtual accounts for USDT TRC20
        # First create a virtual account
        acc_res = requests.post(f"{TATUM_BASE}/offchain/account", headers=tatum_headers(), json={
            "currency": "USDT_TRON",
            "customer": {"externalId": str(user_id)},
            "accountingCurrency": "USD"
        })
        if acc_res.status_code not in [200, 201]:
            # Fallback: generate raw Tron address
            addr_res = requests.get(f"{TATUM_BASE}/tron/account", headers=tatum_headers())
            if addr_res.status_code == 200:
                data = addr_res.json()
                return {"address": data.get("address"), "account_id": None}
            return None

        acc_data = acc_res.json()
        account_id = acc_data.get("id")

        # Generate deposit address for this virtual account
        dep_res = requests.post(
            f"{TATUM_BASE}/offchain/account/{account_id}/address",
            headers=tatum_headers()
        )
        if dep_res.status_code in [200, 201]:
            dep_data = dep_res.json()
            return {
                "address": dep_data.get("address"),
                "account_id": account_id
            }
        return None
    except Exception as e:
        print(f"Tatum address generation error: {e}")
        return None

def get_tron_usdt_balance(address):
    """Get USDT TRC20 balance for an address"""
    try:
        res = requests.get(
            f"{TATUM_BASE}/tron/account/{address}",
            headers=tatum_headers()
        )
        if res.status_code == 200:
            data = res.json()
            trc20 = data.get("trc20", [])
            for token in trc20:
                # USDT TRC20 contract: TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t
                if "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t" in token:
                    raw = token.get("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", "0")
                    return float(raw) / 1_000_000  # USDT has 6 decimals
        return 0.0
    except:
        return 0.0

# ════════════════════════════════════════════════════════
#  ROUTES
# ════════════════════════════════════════════════════════

# ── PIN Setup ────────────────────────────────────────────
@wallet_app.route("/api/wallet/pin/setup", methods=["POST"])
@wallet_auth_required
@rate_limit(max_calls=5, window=300)
def setup_pin():
    data = request.json or {}
    pin = str(data.get("pin", "")).strip()

    if len(pin) != 6 or not pin.isdigit():
        return jsonify({"success": False, "error": "PIN must be exactly 6 digits"}), 400

    user_id = request.user["id"]

    # Check if PIN already set
    existing = supabase.table("crypto_wallets").select("id,pin_hash").eq("user_id", user_id).execute()
    if existing.data and existing.data[0].get("pin_hash"):
        return jsonify({"success": False, "error": "PIN already set. Use change PIN instead."}), 400

    pin_hash = hash_pin(pin)

    if existing.data:
        supabase.table("crypto_wallets").update({"pin_hash": pin_hash}).eq("user_id", user_id).execute()
    else:
        supabase.table("crypto_wallets").insert({"user_id": user_id, "pin_hash": pin_hash}).execute()

    return jsonify({"success": True, "message": "PIN set successfully"})

# ── PIN Verify ───────────────────────────────────────────
@wallet_app.route("/api/wallet/pin/verify", methods=["POST"])
@wallet_auth_required
@rate_limit(max_calls=5, window=300)
def verify_pin():
    data = request.json or {}
    pin = str(data.get("pin", "")).strip()

    if len(pin) != 6 or not pin.isdigit():
        return jsonify({"success": False, "error": "Invalid PIN format"}), 400

    user_id = request.user["id"]
    result = supabase.table("crypto_wallets").select("pin_hash,locked_until,failed_attempts").eq("user_id", user_id).execute()

    if not result.data:
        return jsonify({"success": False, "error": "No PIN set. Please set a PIN first.", "needs_setup": True}), 400

    wallet = result.data[0]

    # Check if locked
    locked_until = wallet.get("locked_until")
    if locked_until:
        locked_dt = datetime.fromisoformat(locked_until.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) < locked_dt:
            remaining = int((locked_dt - datetime.now(timezone.utc)).total_seconds() / 60)
            return jsonify({"success": False, "error": f"Wallet locked. Try again in {remaining} minutes."}), 429

    pin_hash = hash_pin(pin)
    if wallet["pin_hash"] != pin_hash:
        # Increment failed attempts
        attempts = (wallet.get("failed_attempts") or 0) + 1
        update_data = {"failed_attempts": attempts}
        if attempts >= 5:
            # Lock for 30 minutes
            from datetime import timedelta
            lock_until = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
            update_data["locked_until"] = lock_until
        supabase.table("crypto_wallets").update(update_data).eq("user_id", user_id).execute()
        remaining_attempts = max(0, 5 - attempts)
        return jsonify({"success": False, "error": f"Wrong PIN. {remaining_attempts} attempts remaining."}), 401

    # Reset failed attempts on success
    supabase.table("crypto_wallets").update({"failed_attempts": 0, "locked_until": None}).eq("user_id", user_id).execute()

    # Generate short-lived wallet session token
    session_token = str(uuid.uuid4())
    supabase.table("crypto_wallets").update({
        "session_token": session_token,
        "session_expires": (datetime.now(timezone.utc).timestamp() + 1800)  # 30 min
    }).eq("user_id", user_id).execute()

    return jsonify({"success": True, "session_token": session_token})

# ── Get/Create Deposit Address ───────────────────────────
@wallet_app.route("/api/wallet/address", methods=["GET"])
@wallet_auth_required
def get_deposit_address():
    user_id = request.user["id"]

    # Check for existing address
    result = supabase.table("crypto_wallets").select("deposit_address,tatum_account_id").eq("user_id", user_id).execute()

    if result.data and result.data[0].get("deposit_address"):
        return jsonify({
            "success": True,
            "address": result.data[0]["deposit_address"],
            "network": "TRON (TRC20)",
            "coin": "USDT"
        })

    # Generate new address via Tatum
    addr_data = generate_tron_address(user_id)
    if not addr_data:
        return jsonify({"success": False, "error": "Failed to generate address. Try again."}), 500

    # Save to database
    update_data = {
        "deposit_address": addr_data["address"],
        "tatum_account_id": addr_data.get("account_id")
    }
    if result.data:
        supabase.table("crypto_wallets").update(update_data).eq("user_id", user_id).execute()
    else:
        supabase.table("crypto_wallets").insert({"user_id": user_id, **update_data}).execute()

    return jsonify({
        "success": True,
        "address": addr_data["address"],
        "network": "TRON (TRC20)",
        "coin": "USDT"
    })

# ── Get Wallet Balance ───────────────────────────────────
@wallet_app.route("/api/wallet/balance", methods=["GET"])
@wallet_auth_required
def get_balance():
    user_id = request.user["id"]
    result = supabase.table("crypto_wallets").select("usdt_balance,deposit_address").eq("user_id", user_id).execute()

    if not result.data:
        return jsonify({"success": True, "balance": 0.0, "coin": "USDT", "network": "TRC20"})

    wallet = result.data[0]
    balance = wallet.get("usdt_balance") or 0.0

    return jsonify({
        "success": True,
        "balance": float(balance),
        "coin": "USDT",
        "network": "TRC20"
    })

# ── Transaction History ──────────────────────────────────
@wallet_app.route("/api/wallet/transactions", methods=["GET"])
@wallet_auth_required
def get_transactions():
    user_id = request.user["id"]
    result = supabase.table("crypto_transactions") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .limit(50) \
        .execute()

    return jsonify({"success": True, "transactions": result.data or []})

# ── Withdrawal Request ───────────────────────────────────
@wallet_app.route("/api/wallet/withdraw", methods=["POST"])
@wallet_auth_required
@rate_limit(max_calls=3, window=3600)
def request_withdrawal():
    data = request.json or {}
    amount = float(data.get("amount", 0))
    withdraw_type = data.get("type", "crypto")  # "crypto" or "bank"
    destination = data.get("destination", "").strip()  # wallet address or bank details
    idempotency_key = data.get("idempotency_key", str(uuid.uuid4()))

    user_id = request.user["id"]

    if amount < 1:
        return jsonify({"success": False, "error": "Minimum withdrawal is $1 USDT"}), 400
    if not destination:
        return jsonify({"success": False, "error": "Destination is required"}), 400

    # Check idempotency — prevent duplicate requests
    existing = supabase.table("crypto_withdrawal_requests") \
        .select("id,status") \
        .eq("idempotency_key", idempotency_key) \
        .execute()
    if existing.data:
        return jsonify({"success": True, "message": "Request already submitted", "status": existing.data[0]["status"]})

    # Check balance
    wallet = supabase.table("crypto_wallets").select("usdt_balance").eq("user_id", user_id).execute()
    if not wallet.data or (wallet.data[0].get("usdt_balance") or 0) < amount:
        return jsonify({"success": False, "error": "Insufficient balance"}), 400

    # Create withdrawal request
    supabase.table("crypto_withdrawal_requests").insert({
        "user_id": user_id,
        "amount": amount,
        "type": withdraw_type,
        "destination": destination,
        "status": "pending",
        "idempotency_key": idempotency_key,
        "created_at": datetime.now(timezone.utc).isoformat()
    }).execute()

    # Deduct balance immediately (hold)
    new_balance = float(wallet.data[0]["usdt_balance"]) - amount
    supabase.table("crypto_wallets").update({"usdt_balance": new_balance}).eq("user_id", user_id).execute()

    # Log transaction
    supabase.table("crypto_transactions").insert({
        "user_id": user_id,
        "type": "withdrawal",
        "amount": amount,
        "status": "pending",
        "destination": destination,
        "withdraw_type": withdraw_type,
        "created_at": datetime.now(timezone.utc).isoformat()
    }).execute()

    return jsonify({"success": True, "message": "Withdrawal request submitted. You will be notified when processed."})

# ── Tatum Webhook — detects incoming USDT deposits ──────
@wallet_app.route("/api/wallet/webhook/tatum", methods=["POST"])
def tatum_webhook():
    # Verify webhook signature if secret is set
    if TATUM_WEBHOOK_SECRET:
        sig = request.headers.get("x-payload-hash", "")
        expected = hmac.new(TATUM_WEBHOOK_SECRET.encode(), request.data, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return jsonify({"error": "Invalid signature"}), 401

    data = request.json or {}
    print(f"Tatum webhook received: {data}")

    # Tatum sends: type, address, amount, txId, asset
    tx_type = data.get("type", "")
    address = data.get("address", "")
    amount_raw = data.get("amount", 0)
    tx_id = data.get("txId", "")
    asset = data.get("asset", "")

    # Only process USDT TRC20 incoming
    if "USDT" not in asset.upper() and "TRON" not in tx_type.upper():
        return jsonify({"status": "ignored"}), 200

    if not address or not tx_id:
        return jsonify({"status": "missing data"}), 200

    # Prevent duplicate processing
    existing_tx = supabase.table("crypto_transactions").select("id").eq("tx_hash", tx_id).execute()
    if existing_tx.data:
        return jsonify({"status": "already processed"}), 200

    # Find user by deposit address
    wallet_result = supabase.table("crypto_wallets").select("user_id,usdt_balance").eq("deposit_address", address).execute()
    if not wallet_result.data:
        print(f"No wallet found for address: {address}")
        return jsonify({"status": "address not found"}), 200

    wallet = wallet_result.data[0]
    user_id = wallet["user_id"]
    amount = float(amount_raw)

    # Update balance
    new_balance = float(wallet.get("usdt_balance") or 0) + amount
    supabase.table("crypto_wallets").update({
        "usdt_balance": new_balance,
        "last_deposit_at": datetime.now(timezone.utc).isoformat()
    }).eq("user_id", user_id).execute()

    # Log transaction
    supabase.table("crypto_transactions").insert({
        "user_id": user_id,
        "type": "deposit",
        "amount": amount,
        "status": "confirmed",
        "tx_hash": tx_id,
        "asset": asset,
        "created_at": datetime.now(timezone.utc).isoformat()
    }).execute()

    print(f"✅ Deposit confirmed: {amount} USDT for user {user_id}")
    return jsonify({"status": "ok"}), 200

# ── Admin: approve/decline withdrawal ───────────────────
@wallet_app.route("/api/wallet/admin/withdrawal/<req_id>", methods=["POST"])
def admin_handle_withdrawal(req_id):
    # Simple admin key check
    admin_key = request.headers.get("x-admin-key", "")
    if admin_key != os.environ.get("ADMIN_SECRET"):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    action = data.get("action")  # "approve" or "decline"

    if action not in ["approve", "decline"]:
        return jsonify({"error": "Invalid action"}), 400

    req_result = supabase.table("crypto_withdrawal_requests").select("*").eq("id", req_id).execute()
    if not req_result.data:
        return jsonify({"error": "Request not found"}), 404

    wr = req_result.data[0]

    if action == "approve":
        supabase.table("crypto_withdrawal_requests").update({"status": "approved"}).eq("id", req_id).execute()
        supabase.table("crypto_transactions").update({"status": "completed"}).eq("user_id", wr["user_id"]).eq("type", "withdrawal").eq("status", "pending").execute()
        return jsonify({"success": True, "message": "Withdrawal approved"})

    elif action == "decline":
        # Refund balance
        wallet = supabase.table("crypto_wallets").select("usdt_balance").eq("user_id", wr["user_id"]).execute()
        if wallet.data:
            refunded = float(wallet.data[0].get("usdt_balance") or 0) + float(wr["amount"])
            supabase.table("crypto_wallets").update({"usdt_balance": refunded}).eq("user_id", wr["user_id"]).execute()
        supabase.table("crypto_withdrawal_requests").update({"status": "declined"}).eq("id", req_id).execute()
        supabase.table("crypto_transactions").update({"status": "refunded"}).eq("user_id", wr["user_id"]).eq("type", "withdrawal").eq("status", "pending").execute()
        return jsonify({"success": True, "message": "Withdrawal declined and refunded"})

if __name__ == "__main__":
    wallet_app.run(debug=True, port=5001)
