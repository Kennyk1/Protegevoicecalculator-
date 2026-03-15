from flask import Blueprint, request, jsonify
from supabase import create_client
from utils import decode_jwt
from functools import wraps
import os, random, string
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

leader_bp = Blueprint("leader", __name__)

# ── Auth ─────────────────────────────────────────────────
def game_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        try:
            payload = decode_jwt(token)
            user_id = payload.get("user_id")
            result = supabase.table("users").select("id,name,is_banned").eq("id", user_id).single().execute()
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

# ── Helpers ──────────────────────────────────────────────
def generate_room_code():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        existing = supabase.table("game_rooms").select("id").eq("room_code", code).execute()
        if not existing.data:
            return code

def update_leaderboard(user_id, wins=0, kills=0, games=0, earnings=0):
    try:
        existing = supabase.table("game_leaderboard").select("*").eq("user_id", user_id).execute()
        if existing.data:
            row = existing.data[0]
            supabase.table("game_leaderboard").update({
                "total_wins": row["total_wins"] + wins,
                "total_kills": row["total_kills"] + kills,
                "total_games": row["total_games"] + games,
                "total_earnings": float(row["total_earnings"]) + earnings,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("user_id", user_id).execute()
        else:
            supabase.table("game_leaderboard").insert({
                "user_id": user_id,
                "total_wins": wins,
                "total_kills": kills,
                "total_games": games,
                "total_earnings": earnings
            }).execute()
    except Exception as e:
        print(f"Leaderboard update error: {e}")

# ════════════════════════════════════════════════════════
#  ROOM ROUTES
# ════════════════════════════════════════════════════════

@leader_bp.route("/api/game/room/create", methods=["POST"])
@game_auth
def create_room():
    data = request.json or {}
    max_players = int(data.get("max_players", 10))
    entry_fee   = float(data.get("entry_fee", 0))
    user_id     = request.user["id"]

    if max_players < 2 or max_players > 20:
        return jsonify({"success": False, "error": "Max players must be between 2 and 20"}), 400

    # Check entry fee — deduct from wallet if set
    if entry_fee > 0:
        wallet = supabase.table("crypto_wallets").select("usdt_balance").eq("user_id", user_id).execute()
        if not wallet.data or float(wallet.data[0].get("usdt_balance", 0)) < entry_fee:
            return jsonify({"success": False, "error": "Insufficient wallet balance"}), 400
        new_bal = float(wallet.data[0]["usdt_balance"]) - entry_fee
        supabase.table("crypto_wallets").update({"usdt_balance": new_bal}).eq("user_id", user_id).execute()

    room_code = generate_room_code()
    room = supabase.table("game_rooms").insert({
        "room_code": room_code,
        "max_players": max_players,
        "current_players": 1,
        "entry_fee": entry_fee,
        "prize_pool": entry_fee,
        "created_by": user_id,
        "status": "waiting"
    }).execute()

    room_id = room.data[0]["id"]

    # Add creator as first player
    supabase.table("game_players").insert({
        "room_id": room_id,
        "user_id": user_id,
        "status": "alive",
        "health": 100,
        "position_x": 100,
        "position_y": 100
    }).execute()

    return jsonify({
        "success": True,
        "room_id": room_id,
        "room_code": room_code,
        "max_players": max_players,
        "entry_fee": entry_fee
    })


@leader_bp.route("/api/game/room/join", methods=["POST"])
@game_auth
def join_room():
    data = request.json or {}
    room_code = data.get("room_code", "").upper().strip()
    user_id   = request.user["id"]

    if not room_code:
        return jsonify({"success": False, "error": "Room code required"}), 400

    # Find room
    room = supabase.table("game_rooms").select("*").eq("room_code", room_code).execute()
    if not room.data:
        return jsonify({"success": False, "error": "Room not found"}), 404

    room = room.data[0]

    if room["status"] != "waiting":
        return jsonify({"success": False, "error": "Game already started"}), 400
    if room["current_players"] >= room["max_players"]:
        return jsonify({"success": False, "error": "Room is full"}), 400

    # Check if already in room
    already = supabase.table("game_players").select("id").eq("room_id", room["id"]).eq("user_id", user_id).execute()
    if already.data:
        return jsonify({"success": False, "error": "Already in this room"}), 400

    # Entry fee
    entry_fee = float(room.get("entry_fee", 0))
    if entry_fee > 0:
        wallet = supabase.table("crypto_wallets").select("usdt_balance").eq("user_id", user_id).execute()
        if not wallet.data or float(wallet.data[0].get("usdt_balance", 0)) < entry_fee:
            return jsonify({"success": False, "error": "Insufficient wallet balance"}), 400
        new_bal = float(wallet.data[0]["usdt_balance"]) - entry_fee
        supabase.table("crypto_wallets").update({"usdt_balance": new_bal}).eq("user_id", user_id).execute()

    # Add player
    supabase.table("game_players").insert({
        "room_id": room["id"],
        "user_id": user_id,
        "status": "alive",
        "health": 100,
        "position_x": random.randint(50, 700),
        "position_y": random.randint(50, 700)
    }).execute()

    # Update room
    new_pool = float(room["prize_pool"]) + entry_fee
    new_count = room["current_players"] + 1
    supabase.table("game_rooms").update({
        "current_players": new_count,
        "prize_pool": new_pool
    }).eq("id", room["id"]).execute()

    return jsonify({
        "success": True,
        "room_id": room["id"],
        "room_code": room_code,
        "current_players": new_count,
        "prize_pool": new_pool
    })


@leader_bp.route("/api/game/room/<room_id>", methods=["GET"])
@game_auth
def get_room(room_id):
    room = supabase.table("game_rooms").select("*").eq("id", room_id).execute()
    if not room.data:
        return jsonify({"success": False, "error": "Room not found"}), 404

    players = supabase.table("game_players").select(
        "*, users(name)"
    ).eq("room_id", room_id).execute()

    return jsonify({
        "success": True,
        "room": room.data[0],
        "players": players.data or []
    })


@leader_bp.route("/api/game/room/<room_id>/start", methods=["POST"])
@game_auth
def start_room(room_id):
    user_id = request.user["id"]
    room = supabase.table("game_rooms").select("*").eq("id", room_id).execute()
    if not room.data:
        return jsonify({"success": False, "error": "Room not found"}), 404

    room = room.data[0]
    if room["created_by"] != user_id:
        return jsonify({"success": False, "error": "Only room creator can start"}), 403
    if room["current_players"] < 2:
        return jsonify({"success": False, "error": "Need at least 2 players"}), 400

    supabase.table("game_rooms").update({
        "status": "active",
        "started_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", room_id).execute()

    return jsonify({"success": True, "message": "Game started!"})


@leader_bp.route("/api/game/room/<room_id>/leave", methods=["POST"])
@game_auth
def leave_room(room_id):
    user_id = request.user["id"]

    supabase.table("game_players").update({
        "status": "disconnected"
    }).eq("room_id", room_id).eq("user_id", user_id).execute()

    room = supabase.table("game_rooms").select("current_players").eq("id", room_id).execute()
    if room.data:
        new_count = max(0, room.data[0]["current_players"] - 1)
        supabase.table("game_rooms").update({"current_players": new_count}).eq("id", room_id).execute()

    return jsonify({"success": True, "message": "Left room"})


@leader_bp.route("/api/game/room/<room_id>/end", methods=["POST"])
@game_auth
def end_game(room_id):
    data = request.json or {}
    winner_id = data.get("winner_id")

    room = supabase.table("game_rooms").select("*").eq("id", room_id).execute()
    if not room.data:
        return jsonify({"success": False, "error": "Room not found"}), 404

    room = room.data[0]
    if room["status"] != "active":
        return jsonify({"success": False, "error": "Game not active"}), 400

    prize_pool = float(room.get("prize_pool", 0))

    # Pay winner — keep 10% as platform fee
    if winner_id and prize_pool > 0:
        platform_fee = prize_pool * 0.10
        winner_prize = prize_pool - platform_fee

        wallet = supabase.table("crypto_wallets").select("usdt_balance").eq("user_id", winner_id).execute()
        if wallet.data:
            new_bal = float(wallet.data[0].get("usdt_balance", 0)) + winner_prize
            supabase.table("crypto_wallets").update({"usdt_balance": new_bal}).eq("user_id", winner_id).execute()

        update_leaderboard(winner_id, wins=1, games=1, earnings=winner_prize)

    # Update all players leaderboard
    players = supabase.table("game_players").select("user_id,kills").eq("room_id", room_id).execute()
    for p in (players.data or []):
        if p["user_id"] != winner_id:
            update_leaderboard(p["user_id"], kills=p.get("kills", 0), games=1)
        else:
            update_leaderboard(p["user_id"], kills=p.get("kills", 0))

    # Close room
    supabase.table("game_rooms").update({
        "status": "finished",
        "winner_id": winner_id,
        "ended_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", room_id).execute()

    return jsonify({
        "success": True,
        "winner_id": winner_id,
        "prize_paid": winner_prize if prize_pool > 0 else 0,
        "message": "Game ended successfully"
    })

# ════════════════════════════════════════════════════════
#  LEADERBOARD ROUTES
# ════════════════════════════════════════════════════════

@leader_bp.route("/api/game/leaderboard", methods=["GET"])
def get_leaderboard():
    try:
        rows = supabase.table("game_leaderboard").select(
            "*, users(name)"
        ).order("total_wins", desc=True).limit(50).execute()
        return jsonify({"success": True, "leaderboard": rows.data or []})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@leader_bp.route("/api/game/stats", methods=["GET"])
@game_auth
def get_stats():
    user_id = request.user["id"]
    stats = supabase.table("game_leaderboard").select("*").eq("user_id", user_id).execute()
    if not stats.data:
        return jsonify({"success": True, "stats": {
            "total_wins": 0, "total_kills": 0,
            "total_games": 0, "total_earnings": 0
        }})
    return jsonify({"success": True, "stats": stats.data[0]})


@leader_bp.route("/api/game/rooms/available", methods=["GET"])
@game_auth
def available_rooms():
    rooms = supabase.table("game_rooms").select("*").eq("status", "waiting").order("created_at", desc=True).limit(20).execute()
    return jsonify({"success": True, "rooms": rooms.data or []})
