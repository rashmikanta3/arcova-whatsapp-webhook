from datetime import datetime
import os
import sqlite3
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
import requests

# Load environment variables from .env file (for local development)
load_dotenv()

app = Flask(__name__)

# --- Environment Variables / Secret Credentials ---
TOKEN = os.getenv("TOKEN")
PHONE_ID = os.getenv("PHONE_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "arcova_secret_123")

GRAPH_URL = f"https://graph.facebook.com/v20.0/{PHONE_ID}/messages"
DB_FILE = "chats.db"


def init_db():
    """Initializes the SQLite database to store chat history."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                sender TEXT NOT NULL,  -- 'customer' or 'business'
                message_text TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """
        )
        conn.commit()


init_db()


# ---------------------------------------------------------
# Webhook Verification & Message Receiving
# ---------------------------------------------------------
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Handles Meta's handshake verification."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def receive_message():
    """Catches incoming customer messages from WhatsApp."""
    data = request.get_json()

    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})

        if "messages" in value:
            msg_obj = value["messages"][0]
            phone = msg_obj.get("from")
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Extract message text or note media type
            if msg_obj.get("type") == "text":
                text = msg_obj["text"]["body"]
            elif msg_obj.get("type") == "button":
                text = f"[Button Click]: {msg_obj['button']['text']}"
            else:
                text = f"[{msg_obj.get('type', 'MEDIA').upper()} attachment]"

            # Save inbound customer message to database
            with sqlite3.connect(DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO messages (phone, sender, message_text, timestamp)
                    VALUES (?, 'customer', ?, ?)
                """,
                    (phone, text, now_str),
                )
                conn.commit()

    except Exception as e:
        print(f"Error parsing webhook payload: {e}")

    return jsonify(status="received"), 200


# ---------------------------------------------------------
# Web Dashboard Routes
# ---------------------------------------------------------
@app.route("/")
def dashboard():
    """Renders the chat dashboard."""
    return render_template("index.html")


@app.route("/api/conversations", methods=["GET"])
def get_conversations():
    """Returns a list of unique contacts who have messaged, along with their last message."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT phone, message_text, timestamp 
            FROM messages 
            WHERE id IN (SELECT MAX(id) FROM messages GROUP BY phone)
            ORDER BY timestamp DESC
        """
        )
        rows = cursor.fetchall()

    contacts = [
        {"phone": r[0], "last_message": r[1], "timestamp": r[2]} for r in rows
    ]
    return jsonify(contacts)


@app.route("/api/messages/<phone>", methods=["GET"])
def get_messages(phone):
    """Returns all message history for a specific phone number."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT sender, message_text, timestamp 
            FROM messages 
            WHERE phone = ? 
            ORDER BY id ASC
        """,
            (phone,),
        )
        rows = cursor.fetchall()

    messages = [{"sender": r[0], "text": r[1], "timestamp": r[2]} for r in rows]
    return jsonify(messages)


@app.route("/api/send_reply", methods=["POST"])
def send_reply():
    """Sends a text reply to a customer via the WhatsApp Cloud API and stores it."""
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
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO messages (phone, sender, message_text, timestamp)
                VALUES (?, 'business', ?, ?)
            """,
                (phone, text, now_str),
            )
            conn.commit()
        return jsonify({"status": "success"})
    else:
        return jsonify({"error": resp.text}), resp.status_code


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)