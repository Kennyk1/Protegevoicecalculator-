from flask import Blueprint, request, jsonify
from supabase import create_client
from utils import decode_jwt
from functools import wraps
import os
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

game_bp = Blueprint("game_server", __name__)

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
            result = supabase.table("users").select("id,name").eq("id", user_id).single().execute()
            if not result.data:
                return jsonify({"success": False, "error": "User not found"}), 401
            request.user = result.data
        except Exception as e:
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

# ── Helpers ──────────────────────────────────────────────
def get_player(room_id, user_id):
    result = supabase.table("game_players").select("*") \
        .eq("room_id", room_id).eq("user_id", user_id).execute()
    return result.data[0] if result.data else None

def get_room(room_id):
    result = supabase.table("game_rooms").select("*").eq("id", room_id).execute()
    return result.data[0] if result.data else None

def check_winner(room_id):
    """Check if only one player alive — if so end the game"""
    alive = supabase.table("game_players").select("user_id") \
        .eq("room_id", room_id).eq("status", "alive").execute()
    if alive.data and len(alive.data) == 1:
        return alive.data[0]["user_id"]
    return None

# ════════════════════════════════════════════════════════
#  MOVEMENT
# ════════════════════════════════════════════════════════

@game_bp.route("/api/game/move", methods=["POST"])
@game_auth
def move_player():
    data = request.json or {}
    room_id = data.get("room_id")
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    user_id = request.user["id"]

    if not room_id:
        return jsonify({"success": False, "error": "room_id required"}), 400

    room = get_room(room_id)
    if not room or room["status"] != "active":
        return jsonify({"success": False, "error": "Game not active"}), 400

    player = get_player(room_id, user_id)
    if not player or player["status"] != "alive":
        return jsonify({"success": False, "error": "Player not in game or dead"}), 400

    # Update position in Supabase — Realtime will broadcast to all subscribers
    supabase.table("game_players").update({
        "position_x": x,
        "position_y": y
    }).eq("room_id", room_id).eq("user_id", user_id).execute()

    return jsonify({"success": True})


# ════════════════════════════════════════════════════════
#  SHOOTING / HIT DETECTION
# ════════════════════════════════════════════════════════

@game_bp.route("/api/game/shoot", methods=["POST"])
@game_auth
def shoot():
    data = request.json or {}
    room_id   = data.get("room_id")
    target_id = data.get("target_id")  # user_id of player hit
    damage    = int(data.get("damage", 20))
    user_id   = request.user["id"]

    if not room_id or not target_id:
        return jsonify({"success": False, "error": "room_id and target_id required"}), 400

    if user_id == target_id:
        return jsonify({"success": False, "error": "Cannot shoot yourself"}), 400

    room = get_room(room_id)
    if not room or room["status"] != "active":
        return jsonify({"success": False, "error": "Game not active"}), 400

    shooter = get_player(room_id, user_id)
    if not shooter or shooter["status"] != "alive":
        return jsonify({"success": False, "error": "Shooter not alive"}), 400

    target = get_player(room_id, target_id)
    if not target or target["status"] != "alive":
        return jsonify({"success": False, "error": "Target not alive"}), 400

    # Clamp damage
    damage = max(5, min(damage, 50))

    new_health = max(0, target["health"] - damage)
    killed = new_health <= 0

    if killed:
        # Mark target as dead
        supabase.table("game_players").update({
            "status": "dead",
            "health": 0
        }).eq("room_id", room_id).eq("user_id", target_id).execute()

        # Update shooter kills
        supabase.table("game_players").update({
            "kills": shooter["kills"] + 1
        }).eq("room_id", room_id).eq("user_id", user_id).execute()

        # Check if game over
        winner_id = check_winner(room_id)
        if winner_id:
            # Auto end game and pay winner
            prize_pool = float(room.get("prize_pool", 0))
            winner_prize = 0

            if prize_pool > 0:
                platform_fee = prize_pool * 0.10
                winner_prize = prize_pool - platform_fee

                wallet = supabase.table("crypto_wallets").select("usdt_balance") \
                    .eq("user_id", winner_id).execute()
                if wallet.data:
                    new_bal = float(wallet.data[0].get("usdt_balance", 0)) + winner_prize
                    supabase.table("crypto_wallets").update({"usdt_balance": new_bal}) \
                        .eq("user_id", winner_id).execute()

            # Close room
            supabase.table("game_rooms").update({
                "status": "finished",
                "winner_id": winner_id,
                "ended_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", room_id).execute()

            return jsonify({
                "success": True,
                "hit": True,
                "killed": True,
                "game_over": True,
                "winner_id": winner_id,
                "prize_paid": winner_prize
            })

        return jsonify({
            "success": True,
            "hit": True,
            "killed": True,
            "game_over": False
        })

    else:
        # Just damage
        supabase.table("game_players").update({
            "health": new_health
        }).eq("room_id", room_id).eq("user_id", target_id).execute()

        return jsonify({
            "success": True,
            "hit": True,
            "killed": False,
            "remaining_health": new_health
        })


# ════════════════════════════════════════════════════════
#  GAME STATE
# ════════════════════════════════════════════════════════

@game_bp.route("/api/game/state/<room_id>", methods=["GET"])
@game_auth
def get_game_state(room_id):
    """Get full current state of game — positions, health, kills of all players"""
    room = get_room(room_id)
    if not room:
        return jsonify({"success": False, "error": "Room not found"}), 404

    players = supabase.table("game_players").select(
        "user_id, status, health, kills, position_x, position_y, users(name)"
    ).eq("room_id", room_id).execute()

    alive_count = len([p for p in (players.data or []) if p["status"] == "alive"])

    return jsonify({
        "success": True,
        "room": room,
        "players": players.data or [],
        "alive_count": alive_count
    })


@game_bp.route("/api/game/ping/<room_id>", methods=["POST"])
@game_auth
def ping(room_id):
    """Keep player connection alive — call every 5 seconds"""
    user_id = request.user["id"]
    player = get_player(room_id, user_id)
    if not player:
        return jsonify({"success": False, "error": "Not in room"}), 404
    return jsonify({"success": True, "status": player["status"], "health": player["health"]})
