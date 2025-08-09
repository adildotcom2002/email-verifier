# app.py
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import smtplib
import dns.resolver
import re
import socket
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import time

app = Flask(__name__)
CORS(app)  # allow cross-origin requests (frontend / Google Sheets)

# Simple in-memory cache { email: (status, timestamp) }
cache = {}
CACHE_TTL = 60 * 60  # 1 hour cache

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def verify_email(email):
    email = email.strip().lower()
    now = time.time()

    # Check cache
    cached = cache.get(email)
    if cached and now - cached[1] < CACHE_TTL:
        return {"email": email, "status": cached[0]}

    # Syntax check
    if not EMAIL_REGEX.match(email):
        cache[email] = ("Invalid Syntax", now)
        return {"email": email, "status": "Invalid Syntax"}

    domain = email.split('@')[1]

    # MX lookup
    try:
        answers = dns.resolver.resolve(domain, 'MX', lifetime=8.0)
        # choose the first preference
        mx_record = str(sorted([(r.preference, r.exchange.to_text()) for r in answers])[0][1])
    except Exception as e:
        cache[email] = ("No MX Records", now)
        return {"email": email, "status": "No MX Records"}

    # SMTP RCPT check
    try:
        server = smtplib.SMTP(timeout=10)
        server.connect(mx_record)
        server.helo(socket.gethostname())
        server.mail('verify@example.com')  # harmless MAIL FROM
        code, resp = server.rcpt(email)
        server.quit()

        if code == 250 or code == 251:
            cache[email] = ("Valid", now)
        elif code == 550:
            cache[email] = ("Mailbox Not Found", now)
        else:
            cache[email] = (f"Unknown ({code})", now)

        return {"email": email, "status": cache[email][0]}

    except Exception as exc:
        cache[email] = (f"SMTP Error: {str(exc)}", now)
        return {"email": email, "status": cache[email][0]}


@app.route("/verify_bulk", methods=["POST"])
def verify_bulk():
    data = request.get_json(silent=True)
    if not data or "emails" not in data:
        return jsonify({"error": "POST JSON with {\"emails\": [..]} expected"}), 400

    emails = data["emails"]
    if not isinstance(emails, list):
        return jsonify({"error": "emails must be a list"}), 400

    # Threaded execution
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(verify_email, emails))

    return jsonify(results)


@app.route("/verify_csv", methods=["POST"])
def verify_csv():
    # Accepts multipart/form-data with field 'file'
    if 'file' not in request.files:
        return jsonify({"error": "Upload with form field 'file' (CSV)"}), 400

    file = request.files['file']
    try:
        stream = io.StringIO(file.stream.read().decode('utf-8', errors='ignore'))
    except Exception:
        return jsonify({"error": "Could not read uploaded file (ensure UTF-8)"}), 400

    # parse CSV, accept first column as email
    reader = csv.reader(stream)
    emails = []
    for row in reader:
        if not row: 
            continue
        emails.append(row[0].strip())

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(verify_email, emails))

    # Build CSV response
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["email", "status"])
    for r in results:
        writer.writerow([r["email"], r["status"]])

    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8')), 
                     mimetype='text/csv',
                     as_attachment=True,
                     download_name='verified_emails.csv')


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
