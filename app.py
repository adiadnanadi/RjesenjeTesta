#!/usr/bin/env python3
"""
Rješavač Testova - Flask Server (standalone)
Render.com deployment
"""

import os
import json
import base64
import io
import urllib.request
import urllib.error
from pathlib import Path
from flask import Flask, send_file, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR    = Path(__file__).parent
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"


def get_api_key():
    key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if key:
        return key
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("MISTRAL_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ══════════════════════════════════════════════════
# STRANICA
# ══════════════════════════════════════════════════

@app.route("/")
@app.route("/index.html")
@app.route("/rjesavac-testova.html")
def index():
    return send_file(BASE_DIR / "rjesavac-testova.html")


# ══════════════════════════════════════════════════
# API — STATUS KLJUČA
# ══════════════════════════════════════════════════

@app.route("/api/key-status")
def key_status():
    key = get_api_key()
    preview = ""
    if len(key) > 14:
        preview = key[:8] + "..." + key[-4:]
    elif key:
        preview = "*" * len(key)
    return jsonify({"configured": bool(key), "preview": preview})


# ══════════════════════════════════════════════════
# API — KOMPAJLIRANJE LaTeX → PDF
# ══════════════════════════════════════════════════

@app.route("/api/compile", methods=["POST"])
def compile_latex():
    try:
        data = request.get_json()
        latex = data.get("latex", "")
        if not latex:
            return jsonify({"error": "Nema LaTeX sadrzaja"}), 400

        boundary = "----FormBoundary7MA4YWxkTrZu0gW"
        CRLF = "\r\n"

        def form_field(name, value):
            return (
                "--" + boundary + CRLF +
                'Content-Disposition: form-data; name="' + name + '"' + CRLF + CRLF +
                value + CRLF
            )

        body_str = (
            form_field("filecontents[]", latex) +
            form_field("filename[]", "document.tex") +
            form_field("engine", "pdflatex") +
            form_field("return", "pdf") +
            "--" + boundary + "--" + CRLF
        )
        body = body_str.encode("utf-8")

        req = urllib.request.Request(
            "https://texlive.net/cgi-bin/latexcgi",
            data=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=" + boundary,
                "Content-Length": str(len(body)),
            },
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            pdf_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "")

        if "pdf" in content_type:
            return Response(pdf_bytes, status=200, mimetype="application/pdf")
        else:
            return jsonify({
                "error": "LaTeX kompajliranje nije uspjelo",
                "log": pdf_bytes.decode("utf-8", errors="replace")[:500]
            }), 422

    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        return jsonify({"error": "TeXLive HTTP greska " + str(e.code), "log": err[:300]}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ══════════════════════════════════════════════════
# API — RJEŠAVANJE TESTA IZ PDF-a
# Direktno PyPDF2 + mistral-large (bez Pixtral)
# ══════════════════════════════════════════════════

@app.route("/api/solve-test", methods=["POST"])
def solve_test():
    try:
        data = request.get_json()
        pdf_base64    = data.get("pdf_base64", "")
        system_prompt = data.get("system_prompt", "")
        user_message  = data.get("user_message", "")

        if not pdf_base64:
            return jsonify({"error": {"message": "Nema PDF sadrzaja"}}), 400

        api_key = get_api_key()
        if not api_key:
            return jsonify({"error": {"message": "API kljuc nije postavljen."}}), 400

        pdf_bytes = base64.b64decode(pdf_base64)

        # Izvuci tekst iz PDF-a
        extracted_text = ""
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                extracted_text += (page.extract_text() or "") + "\n"
        except Exception as e:
            return jsonify({
                "error": {"message": f"Ne mogu čitati PDF: {e}"}
            }), 500

        if not extracted_text.strip():
            return jsonify({
                "error": {"message": "PDF je prazan ili skeniran — nema teksta za ekstrakciju."}
            }), 400

        print(f"[solve-test] Izvuceno {len(extracted_text)} znakova iz PDF-a")

        combined = (
            f"{user_message}\n\n"
            f"SADRŽAJ TESTA IZVUČEN IZ PDF-a:\n"
            f"{'='*60}\n"
            f"{extracted_text}\n"
            f"{'='*60}"
        )

        payload = {
            "model": "mistral-large-latest",
            "max_tokens": 16000,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": combined}
            ]
        }

        latex = _call_mistral(payload, api_key, timeout=280)
        return jsonify({"latex": latex})

    except Exception as e:
        print(f"[solve-test ERROR] {e}")
        return jsonify({"error": {"message": str(e)}}), 500


def _call_mistral(payload, api_key, timeout=180):
    payload_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        MISTRAL_URL,
        data=payload_bytes,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    result = json.loads(body)
    return result["choices"][0]["message"]["content"]


# ══════════════════════════════════════════════════
# START
# ══════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    key = get_api_key()
    print(f"\n  Rješavač Testova: http://localhost:{port}")
    print(f"  Kljuc: {key[:8] + '...' + key[-4:] if key else 'NIJE POSTAVLJEN'}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
