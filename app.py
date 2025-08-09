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
Notes: the app uses flask_cors to avoid CORS problems and dnspython for MX lookups. The SMTP RCPT check uses lightweight connection; some providers block RCPT probing or require greylisting — that’s normal.

requirements.txt
nginx
Copy
Edit
flask
flask-cors
dnspython
gunicorn
(keep this exact file)

2) Create GitHub repo (in-browser) — one step at a time
Go to GitHub → New repository → name email-verifier → Initialize with README (optional) → Create repo.

In the repo, click Add file → Create new file.

Create app.py and paste the code above. Commit.

Create requirements.txt and paste its contents. Commit.

(If you prefer terminal commands, tell me and I’ll give exact git commands.)

3) Deploy to Render (free) — one step at a time
Sign in to Render (render.com). Use GitHub login so Render can access your repo.

Dashboard → New → Web Service.

Select your GitHub repo email-verifier.

For Environment, choose Python 3.

Build Command:

nginx
Copy
Edit
pip install -r requirements.txt
Start Command:

nginx
Copy
Edit
gunicorn app:app
Choose free plan, click Create Web Service.

Wait for build & deploy. When done, Render will show a URL like:

arduino
Copy
Edit
https://email-verifier-onrender-xxxxx.onrender.com
Your two endpoints will be:

https://.../verify_bulk

https://.../verify_csv

https://.../health

4) Quick test (curl) — one-step tests
Test health

bash
Copy
Edit
curl https://your-render-url.onrender.com/health
Test bulk JSON

bash
Copy
Edit
curl -X POST "https://your-render-url.onrender.com/verify_bulk" \
  -H "Content-Type: application/json" \
  -d '{"emails":["andreas@zahnarzt-karlsboeck.at","fake@example.invalid"]}'
Test CSV upload

bash
Copy
Edit
curl -F "file=@emails.csv" https://your-render-url.onrender.com/verify_csv -o verified.csv
5) Google Sheets: Apps Script to call /verify_bulk
Open your Google Sheet → Extensions → Apps Script → create new script and paste:

javascript
Copy
Edit
function verifyEmailsFromSheet() {
  var API_BASE = 'https://your-render-url.onrender.com'; // <-- set your Render URL
  var API_URL = API_BASE + '/verify_bulk';

  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var range = sheet.getActiveRange(); // select the column with emails first
  var values = range.getValues();

  var emails = [];
  for (var i = 0; i < values.length; i++) {
    var e = (values[i][0] || '').toString().trim();
    if (e) emails.push(e);
  }
  if (emails.length === 0) {
    SpreadsheetApp.getUi().alert('No emails found in selected range.');
    return;
  }

  // chunk to avoid huge payloads (optional)
  var chunkSize = 150; // adjust depending on list size / server capacity
  var outputs = [];
  for (var start = 0; start < emails.length; start += chunkSize) {
    var chunk = emails.slice(start, start + chunkSize);
    var options = {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({ emails: chunk }),
      muteHttpExceptions: true
    };
    var resp = UrlFetchApp.fetch(API_URL, options);
    if (resp.getResponseCode() !== 200) {
      Logger.log('Error from API: ' + resp.getContentText());
      throw new Error('API error: ' + resp.getResponseCode());
    }
    var json = JSON.parse(resp.getContentText());
    Array.prototype.push.apply(outputs, json);
  }

  // write statuses to the column next to selection
  var out = [];
  for (var i = 0; i < values.length; i++) {
    var email = (values[i][0] || '').toString().trim();
    var found = outputs.find(function(o){ return o.email === email; });
    out.push([ found ? found.status : 'Not checked' ]);
  }
  range.offset(0, 1).setValues(out);
  SpreadsheetApp.getUi().alert('Verification complete. Results written to next column.');
}
