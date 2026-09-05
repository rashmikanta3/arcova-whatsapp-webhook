from datetime import datetime
import io
import os
import time
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
import pandas as pd
import requests
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv(
    "FLASK_SECRET_KEY", "arcova_super_secret_session_key"
)

# --- Configuration ---
TOKEN = os.getenv("TOKEN")
PHONE_ID = os.getenv("PHONE_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "arcova_secret_123")
GRAPH_URL = f"https://graph.facebook.com/v20.0/{PHONE_ID}/messages"

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- Existing Webhook & Chat Dashboard Routes ---
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

            supabase.table("messages").insert(
                {
                    "phone": str(phone),
                    "sender": "customer",
                    "message_text": text,
                    "timestamp": now_str,
                }
            ).execute()
    except Exception as e:
        print(f"Webhook error: {e}")
    return jsonify(status="received"), 200


@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/api/conversations", methods=["GET"])
def get_conversations():
    try:
        res = (
            supabase.table("messages")
            .select("phone, message_text, timestamp")
            .order("id", desc=True)
            .execute()
        )
        seen_phones = set()
        contacts = []
        for msg in res.data:
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
        return jsonify({"error": "Phone and text required"}), 400

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
        supabase.table("messages").insert(
            {
                "phone": str(phone),
                "sender": "business",
                "message_text": text,
                "timestamp": now_str,
            }
        ).execute()
        return jsonify({"status": "success"})
    return jsonify({"error": resp.text}), resp.status_code


# ---------------------------------------------------------
# Dynamic Bulk Broadcast (Row-by-Row Template & Image)
# ---------------------------------------------------------
@app.route("/broadcast", methods=["GET", "POST"])
def broadcast():
    if request.method == "POST":
        uploaded_file = request.files.get("file")

        if not uploaded_file or uploaded_file.filename == "":
            flash("Please choose an Excel file to upload.", "error")
            return redirect(url_for("broadcast"))

        try:
            df = pd.read_excel(uploaded_file, dtype=str)

            # Validate required columns
            required_cols = {"Phone", "Template_Name"}
            if not required_cols.issubset(df.columns):
                flash(
                    "Excel must contain at least 'Phone' and 'Template_Name' columns.",
                    "error",
                )
                return redirect(url_for("broadcast"))

            success_count = 0
            fail_count = 0
            headers = {
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            }

            for _, row in df.iterrows():
                raw_phone = row.get("Phone")
                template_name = str(row.get("Template_Name", "")).strip()
                lang_code = (
                    str(row.get("Language_Code", "en")).strip()
                    if pd.notna(row.get("Language_Code"))
                    else "en"
                )
                image_url = (
                    str(row.get("Image_URL", "")).strip()
                    if pd.notna(row.get("Image_URL"))
                    else ""
                )

                if pd.isna(raw_phone) or not template_name or template_name.lower() == "nan":
                    continue

                phone = "".join(c for c in str(raw_phone) if c.isdigit())
                if len(phone) == 10:
                    phone = "91" + phone

                if len(phone) < 12:
                    fail_count += 1
                    continue

                # Build template components if an image URL is specified
                components = []
                if image_url and image_url.startswith("http"):
                    components.append(
                        {
                            "type": "header",
                            "parameters": [
                                {"type": "image", "image": {"link": image_url}}
                            ],
                        }
                    )

                payload = {
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "template",
                    "template": {
                        "name": template_name,
                        "language": {"code": lang_code},
                    },
                }
                if components:
                    payload["template"]["components"] = components

                res = requests.post(GRAPH_URL, headers=headers, json=payload)
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if res.status_code == 200:
                    success_count += 1
                    # Record the sent message in Supabase
                    supabase.table("messages").insert(
                        {
                            "phone": str(phone),
                            "sender": "business",
                            "message_text": f"[Template: {template_name}]",
                            "timestamp": now_str,
                        }
                    ).execute()
                else:
                    fail_count += 1

                time.sleep(0.5)  # Pause to respect rate limits

            flash(
                f"Campaign finished! Successfully Sent: {success_count} | Failed/Skipped: {fail_count}",
                "success",
            )
            return redirect(url_for("broadcast"))

        except Exception as e:
            flash(f"Error processing file: {str(e)}", "error")
            return redirect(url_for("broadcast"))

    return render_template("broadcast.html")


@app.route("/download-sample")
def download_sample():
    """Generates a downloadable sample Excel sheet with template and image columns."""
    sample_data = {
        "Phone": ["919556681223", "919937780774"],
        "Template_Name": ["janmastami", "janmastami"],
        "Language_Code": ["en", "en"],
        "Image_URL(download_able": [
            "https://lh3.googleusercontent.com/d/10oUnRNZPxE-gBs2I-l6NL0ES9dckObCP",
            "https://lh3.googleusercontent.com/d/10oUnRNZPxE-gBs2I-l6NL0ES9dckObCP",
        ],
    }
    df = pd.DataFrame(sample_data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Broadcast_Sample")
    output.seek(0)

    return send_file(
        output,
        download_name="sample_whatsapp_broadcast.xlsx",
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
