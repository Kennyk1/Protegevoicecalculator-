import os
import requests
from flask import Blueprint, request, jsonify
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY
from utils import decode_jwt

chat_bp = Blueprint("chat", __name__)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-flash-1.5"   # fast + free tier — swap to "openai/gpt-4o" anytime

SYSTEM_PROMPT = """You are Protege, an AI-powered voice calculator and academic assistant. 
You specialise in:
- Mathematics (basic arithmetic, algebra, calculus, statistics, further maths)
- Physics (mechanics, waves, electricity, thermodynamics, quantum)
- Chemistry (organic, inorganic, equations, stoichiometry, periodic table)
- General calculations (percentages, unit conversions, financial math)

Personality:
- Smart, clear, and concise
- Show step-by-step working when solving problems
- Use proper mathematical notation where helpful
- Be encouraging — students are your main users
- If something is outside your scope, say so honestly

Always format equations and steps clearly. When solving, show:
1. What is given
2. Formula/method used
3. Step-by-step working
4. Final answer (highlighted)
"""
def get_user_from_token():
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None, "Missing token"
    try:
        token = auth_header.split(" ")[1]
        payload = decode_jwt(token)
        user_id = payload.get("user_id")
        user = supabase.table("users").select("id, name").eq("id", user_id).execute()
        if not user.data:
            return None, "User not found"
        return user.data[0], None
    except Exception:
        return None, "Invalid token"

def get_history(user_id, limit=20):
    try:
        rows = (
            supabase.table("conversations")
            .select("role, content")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        
        messages = list(reversed(rows.data))
        return messages
    except Exception:
        return []

def save_message(user_id, role, content):
    try:
        supabase.table("conversations").insert({
            "user_id": user_id,
            "role": role,
            "content": content
        }).execute()
    except Exception as e:
        print(f"⚠️ Failed to save message: {e}")

def call_ai(messages):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://protege-app.com",   # optional but good practice
        "X-Title": "Protege AI Calculator"
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.3
    }
    response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]

@chat_bp.route("/api/chat", methods=["POST"])
def chat():
    user, error = get_user_from_token()
    if error:
        return jsonify({"success": False, "message": error}), 401

    data = request.json
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"success": False, "message": "Message is required"}), 400

    if len(user_message) > 2000:
        return jsonify({"success": False, "message": "Message too long (max 2000 chars)"}), 400

    user_id = user["id"]
    
    save_message(user_id, "user", user_message)

    history = get_history(user_id, limit=20)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history:
        messages.append({"role": h["role"], "content": h["content"]})

    try:
        reply = call_ai(messages)
    except requests.exceptions.Timeout:
        return jsonify({"success": False, "message": "AI took too long to respond. Try again."}), 504
    except requests.exceptions.HTTPError as e:
    print(f"❌ OpenRouter error: {e}")
    print(f"❌ Response body: {e.response.text}")
    return jsonify({"success": False, "message": "AI service error. Try again shortly."}), 502
    except Exception as e:
        print(f"❌ Unexpected AI error: {e}")
        return jsonify({"success": False, "message": "Something went wrong. Please try again."}), 500

    save_message(user_id, "assistant", reply)

    return jsonify({
        "success": True,
        "reply": reply
    })


# ══════════════════════════════
# GET /api/chat/history
# Returns last 50 messages for the user
# ══════════════════════════════
@chat_bp.route("/api/chat/history", methods=["GET"])
def chat_history():
    user, error = get_user_from_token()
    if error:
        return jsonify({"success": False, "message": error}), 401

    try:
        rows = (
            supabase.table("conversations")
            .select("role, content, created_at")
            .eq("user_id", user["id"])
            .order("created_at", desc=False)
            .limit(50)
            .execute()
        )
        return jsonify({"success": True, "history": rows.data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ══════════════════════════════
# DELETE /api/chat/clear
# Clears all chat history for the user
# ══════════════════════════════
@chat_bp.route("/api/chat/clear", methods=["DELETE"])
def clear_history():
    user, error = get_user_from_token()
    if error:
        return jsonify({"success": False, "message": error}), 401

    try:
        supabase.table("conversations").delete().eq("user_id", user["id"]).execute()
        return jsonify({"success": True, "message": "Chat history cleared"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
