from datetime import datetime
import os
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
import requests
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)

# --- Meta API Configuration ---
TOKEN = os.getenv("TOKEN")
PHONE_ID = os.getenv("PHONE_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "arcova_secret_123")
GRAPH_URL = f"https://graph.facebook.com/v20.0/{PHONE_ID}/messages"

# --- Supabase Database Configuration ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print(
        "WARNING: SUPABASE_URL or SUPABASE_KEY environment variables are missing!"
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ---------------------------------------------------------
# Webhook Verification & Message Receiving
# ---------------------------------------------------------
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()

    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})

        if "messages" in value:
            msg_obj = value["messages"][0]
            phone = msg_obj.get("from")
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if msg_obj.get("type") == "text":
                text = msg_obj["text"]["body"]
            elif msg_obj.get("type") == "button":
                text = f"[Button Click]: {msg_obj['button']['text']}"
            else:
                text = f"[{msg_obj.get('type', 'MEDIA').upper()} attachment]"

            # Insert customer message directly into Supabase
            supabase.table("messages").insert(
                {
                    "phone": str(phone),
                    "sender": "customer",
                    "message_text": text,
                    "timestamp": now_str,
                }
            ).execute()

    except Exception as e:
        print(f"Error handling webhook payload: {e}")

    return jsonify(status="received"), 200


# ---------------------------------------------------------
# Web Dashboard Routes
# ---------------------------------------------------------
@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/api/conversations", methods=["GET"])
def get_conversations():
    try:
        # Fetch all messages ordered by newest first
        res = (
            supabase.table("messages")
            .select("phone, message_text, timestamp")
            .order("id", desc=True)
            .execute()
        )
        all_msgs = res.data

        # Filter out unique latest message per phone number
        seen_phones = set()
        contacts = []
        for msg in all_msgs:
            phone = msg["phone"]
            if phone not in seen_phones:
                seen_phones.add(phone)
                contacts.append(
                    {
                        "phone": phone,
                        "last_message": msg["message_text"],
                        "timestamp": msg["timestamp"],
                    }
                )

        return jsonify(contacts)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/messages/<phone>", methods=["GET"])
def get_messages(phone):
    try:
        # Fetch conversation history in chronological order
        res = (
            supabase.table("messages")
            .select("sender, message_text, timestamp")
            .eq("phone", phone)
            .order("id", desc=False)
            .execute()
        )

        messages = [
            {"sender": m["sender"], "text": m["message_text"], "timestamp": m["timestamp"]}
            for m in res.data
        ]
        return jsonify(messages)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/send_reply", methods=["POST"])
def send_reply():
    req_data = request.get_json()
    phone = req_data.get("phone")
    text = req_data.get("text", "").strip()

    if not phone or not text:
        return jsonify({"error": "Phone and text are required"}), 400

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text},
    }

    resp = requests.post(GRAPH_URL, headers=headers, json=payload)
    if resp.status_code == 200:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Save outgoing business message to Supabase
        supabase.table("messages").insert(
            {
                "phone": str(phone),
                "sender": "business",
                "message_text": text,
                "timestamp": now_str,
            }
        ).execute()

        return jsonify({"status": "success"})
    else:
        return jsonify({"error": resp.text}), resp.status_code


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)