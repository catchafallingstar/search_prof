import os
import re
import smtplib
from email.message import EmailMessage
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
# Enable CORS for the Streamlit app origin if set, otherwise allow all (development)
allowed = os.getenv("ALLOWED_ORIGIN")
if allowed:
    CORS(app, origins=[allowed])
else:
    CORS(app)


def valid_email(addr: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr))


@app.route("/send_contact", methods=["POST"])
def send_contact():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not email:
        return jsonify({"ok": False, "error": "name and email are required"}), 400
    if not valid_email(email):
        return jsonify({"ok": False, "error": "invalid email address"}), 400

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    dest = os.getenv("CONTACT_EMAIL", "phdfighting@gmail.com")

    if not smtp_host or not smtp_user or not smtp_pass:
        return jsonify({"ok": False, "error": "SMTP server not configured on server"}), 500

    msg = EmailMessage()
    msg["Subject"] = f"ScholarRadar contact from {name}"
    msg["From"] = smtp_user
    msg["To"] = dest
    body = f"Name: {name}\nEmail: {email}\n\nMessage:\n{message}\n"
    msg.set_content(body)

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as s:
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as s:
                s.ehlo()
                s.starttls()
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.getenv("CONTACT_SERVER_PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
