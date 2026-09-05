from datetime import datetime
from functools import wraps
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
    session,
    url_for,
)
import pandas as pd
import requests
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "arcova_super_secret_session_key")

# --- Meta API Configuration ---
TOKEN = os.getenv("TOKEN")
PHONE_ID = os.getenv("PHONE_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "arcova_secret_123")
GRAPH_URL = f"https://graph.facebook.com/v20.0/{PHONE_ID}/messages"

# --- Supabase Configuration ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("WARNING: SUPABASE_URL or SUPABASE_KEY environment variables are missing!")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ---------------------------------------------------------
# Authentication Guard Decorator
# ---------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------
# Public Legal Pages (Required for Meta App Live Review)
# ---------------------------------------------------------
@app.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy_policy.html")


# ---------------------------------------------------------
# Authentication Endpoints
# ---------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_input = request.form.get("username", "").strip()
        pwd_input = request.form.get("password", "").strip()

        if not user_input or not pwd_input:
            flash("Please enter both ID and password.", "login_error")
            return redirect(url_for("login"))

        try:
            response = (
                supabase.table("admins")
                .select("username")
                .eq("username", user_input)
                .eq("password", pwd_input)
                .limit(1)
                .execute()
            )

            if response.data and len(response.data) > 0:
                session["logged_in"] = True
                session["user"] = response.data[0]["username"]
                return redirect(url_for("dashboard"))
            else:
                flash("Invalid ID or password.", "login_error")
                return redirect(url_for("login"))

        except Exception as e:
            print(f"Supabase login error: {e}")
            flash("Database connection error. Try again.", "login_error")
            return redirect(url_for("login"))

    if session.get("logged_in"):
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------
# Webhook Verification & Unified Event Handling (Public)
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

        # 1. Delivery & Read Status Updates
        if "statuses" in value:
            status_obj = value["statuses"][0]
            wamid = status_obj.get("id")
            status = status_obj.get("status")  # 'sent', 'delivered', 'read', 'failed'
            recipient_id = status_obj.get("recipient_id")

            update_payload = {"status": status}

            if status == "failed":
                errors = status_obj.get("errors", [{}])
                err_title = (
                    errors[0].get("title", "Delivery Failed")
                    if errors
                    else "Delivery Failed"
                )
                err_code = errors[0].get("code", "") if errors else ""
                update_payload["error_reason"] = f"{err_title} (Code: {err_code})"
                print(f"FAILED DELIVERY for {recipient_id}: {err_title} (Code: {err_code})")
            else:
                print(f"STATUS UPDATE -> Recipient: {recipient_id} | Status: {status}")

            if wamid:
                supabase.table("messages").update(update_payload).eq("wamid", wamid).execute()

        # 2. Inbound Customer Messages
        if "messages" in value:
            msg_obj = value["messages"][0]
            phone = msg_obj.get("from")
            wamid = msg_obj.get("id")
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
                    "wamid": wamid,
                    "status": "received",
                }
            ).execute()

    except Exception as e:
        print(f"Error handling webhook payload: {e}")

    return jsonify(status="received"), 200


# ---------------------------------------------------------
# Protected Dashboard & Chat API Routes
# ---------------------------------------------------------
@app.route("/")
@login_required
def dashboard():
    return render_template("index.html")


@app.route("/api/conversations", methods=["GET"])
@login_required
def get_conversations():
    try:
        res = (
            supabase.table("messages")
            .select("phone, message_text, timestamp")
            .order("id", desc=True)
            .execute()
        )
        all_msgs = res.data

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
@login_required
def get_messages(phone):
    try:
        res = (
            supabase.table("messages")
            .select("sender, message_text, timestamp, status, error_reason")
            .eq("phone", phone)
            .order("id", desc=False)
            .execute()
        )

        messages = [
            {
                "sender": m.get("sender"),
                "text": m.get("message_text"),
                "timestamp": m.get("timestamp"),
                "status": m.get("status", "sent"),
                "error_reason": m.get("error_reason", ""),
            }
            for m in res.data
        ]
        return jsonify(messages)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/send_reply", methods=["POST"])
@login_required
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
        wamid = resp.json().get("messages", [{}])[0].get("id")

        supabase.table("messages").insert(
            {
                "phone": str(phone),
                "sender": "business",
                "message_text": text,
                "timestamp": now_str,
                "wamid": wamid,
                "status": "sent",
            }
        ).execute()

        return jsonify({"status": "success"})
    else:
        return jsonify({"error": resp.text}), resp.status_code


@app.route("/api/start_new_chat", methods=["POST"])
@login_required
def start_new_chat():
    req_data = request.get_json()
    raw_phone = req_data.get("phone", "").strip()
    text = req_data.get("text", "").strip()

    if not raw_phone or not text:
        return jsonify({"error": "Phone number and message text are required."}), 400

    phone = "".join(c for c in str(raw_phone) if c.isdigit())
    if len(phone) == 10:
        phone = "91" + phone

    if len(phone) < 12:
        return jsonify({"error": "Phone number must have at least 10 digits."}), 400

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
        wamid = resp.json().get("messages", [{}])[0].get("id")

        supabase.table("messages").insert(
            {
                "phone": str(phone),
                "sender": "business",
                "message_text": text,
                "timestamp": now_str,
                "wamid": wamid,
                "status": "sent",
            }
        ).execute()

        return jsonify({"status": "success", "phone": phone})
    else:
        err_msg = resp.json().get("error", {}).get("message", resp.text)
        return jsonify({"error": err_msg}), resp.status_code


# ---------------------------------------------------------
# Dynamic Bulk Broadcast Route (Protected)
# ---------------------------------------------------------
@app.route("/broadcast", methods=["GET", "POST"])
@login_required
def broadcast():
    if request.method == "POST":
        uploaded_file = request.files.get("file")

        if not uploaded_file or uploaded_file.filename == "":
            flash("Please choose an Excel file to upload.", "error")
            return redirect(url_for("broadcast"))

        try:
            df = pd.read_excel(uploaded_file, dtype=str)

            required_cols = {"Phone", "Template_Name"}
            if not required_cols.issubset(df.columns):
                flash(
                    "Excel must contain at least 'Phone' and 'Template_Name' columns.",
                    "error",
                )
                return redirect(url_for("broadcast"))

            success_count = 0
            fail_count = 0
            error_details = []
            headers = {
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            }

            for idx, row in df.iterrows():
                raw_phone = row.get("Phone")
                template_name = str(row.get("Template_Name", "")).strip()
                lang_code = (
                    str(row.get("Language_Code", "en")).strip()
                    if pd.notna(row.get("Language_Code"))
                    else "en"
                )

                if (
                    pd.isna(raw_phone)
                    or not template_name
                    or template_name.lower() == "nan"
                ):
                    fail_count += 1
                    error_details.append(
                        f"Row {idx + 2}: Empty phone number or template name."
                    )
                    continue

                phone = "".join(c for c in str(raw_phone) if c.isdigit())
                if len(phone) == 10:
                    phone = "91" + phone

                if len(phone) < 12:
                    fail_count += 1
                    error_details.append(
                        f"Row {idx + 2}: Phone number '{raw_phone}' must have at least 10 digits."
                    )
                    continue

                components = []

                # 1. Header Media Component (Image)
                image_url = (
                    str(row.get("Image_URL", "")).strip()
                    if pd.notna(row.get("Image_URL"))
                    else ""
                )
                if (
                    image_url
                    and image_url.lower() != "nan"
                    and image_url.startswith("http")
                ):
                    components.append(
                        {
                            "type": "header",
                            "parameters": [
                                {"type": "image", "image": {"link": image_url}}
                            ],
                        }
                    )

                # 2. Dynamic Body Variables (Var1 to Var5)
                body_params = []
                for i in range(1, 6):
                    col_name = f"Var{i}"
                    if col_name in df.columns:
                        val = row.get(col_name)
                        if pd.notna(val):
                            text_val = str(val).strip()
                            if text_val and text_val.lower() != "nan":
                                body_params.append(
                                    {"type": "text", "text": text_val}
                                )

                if body_params:
                    components.append({"type": "body", "parameters": body_params})

                # 3. Payload Construction
                payload = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": phone,
                    "type": "template",
                    "template": {
                        "name": template_name,
                        "language": {"code": lang_code},
                    },
                }

                if components:
                    payload["template"]["components"] = components

                # 4. API Request
                res = requests.post(GRAPH_URL, headers=headers, json=payload)
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if res.status_code == 200:
                    success_count += 1
                    wamid = res.json().get("messages", [{}])[0].get("id")

                    supabase.table("messages").insert(
                        {
                            "phone": str(phone),
                            "sender": "business",
                            "message_text": f"[Template: {template_name}]",
                            "timestamp": now_str,
                            "wamid": wamid,
                            "status": "sent",
                        }
                    ).execute()
                else:
                    fail_count += 1
                    err_msg = res.json().get("error", {}).get("message", res.text)
                    error_details.append(f"Row {idx + 2} ({phone}): {err_msg}")
                    print(
                        f"Failed sending to {phone} [{res.status_code}]: {res.text}"
                    )

                time.sleep(0.4)

            if fail_count > 0:
                summary_msg = f"Campaign run completed.<br>Sent: {success_count} | Failed: {fail_count}<br><br><strong>Rejection Details:</strong><br>" + "<br>".join(
                    error_details
                )
                flash(summary_msg, "error")
            else:
                flash(
                    f"Campaign completed successfully! All {success_count} messages were sent.",
                    "success",
                )

            return redirect(url_for("broadcast"))

        except Exception as e:
            flash(f"Error reading file: {str(e)}", "error")
            return redirect(url_for("broadcast"))

    return render_template("broadcast.html")


# ---------------------------------------------------------
# Dynamic Excel Sample Generator (Protected)
# ---------------------------------------------------------
@app.route("/download-sample")
@login_required
def download_sample():
    sample_data = {
        "Phone": ["919556681223", "919937780774", "917008973622"],
        "Template_Name": [
            "image_only_template",
            "text_with_vars_template",
            "image_and_vars_template",
        ],
        "Language_Code": ["en", "en", "en"],
        "Image_URL": [
            "https://lh3.googleusercontent.com/d/10oUnRNZPxE-gBs2I-l6NL0ES9dckObCP",
            "",
            "https://lh3.googleusercontent.com/d/10oUnRNZPxE-gBs2I-l6NL0ES9dckObCP",
        ],
        "Var1": ["", "Valued Client", "Valued Client"],
        "Var2": ["", "Arcova Homes", "Arcova Homes"],
        "Var3": ["", "", "Phase 2 Launch"],
        "Var4": ["", "", "Saturday"],
        "Var5": ["", "", ""],
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
