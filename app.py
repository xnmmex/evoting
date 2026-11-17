
import random
import string
import sqlite3
import socket
import secrets
import hashlib
import time
import os
import io
import qrcode

from functools import wraps
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, send_file
)

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


app = Flask(__name__)
app.secret_key = "kunci_rahasia_tps_kelurahan_2026_super_aman"

DB_NAME = os.path.join(app.root_path, "evoting_tps.db")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "3003"

VOTING_SESSION_SECONDS = 600


# ==================================================
# DATABASE
# ==================================================

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            vision TEXT,
            photo TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS voters (
            nik TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            token TEXT UNIQUE,
            is_voted INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES candidates(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS voting_sessions (
            session_hash TEXT PRIMARY KEY,
            nik TEXT UNIQUE NOT NULL,
            expires_at INTEGER NOT NULL
        )
    """)

    cursor.execute("SELECT COUNT(*) FROM candidates")

    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO candidates (name, vision, photo) VALUES (?, ?, ?)",
            ("01.ABAH KOMENG", "KAMARUNG", "calon1.jpg")
        )

        cursor.execute(
            "INSERT INTO candidates (name, vision, photo) VALUES (?, ?, ?)",
            ("02. AJAY", "KAMARUNG", "calon2.jpg")
        )

    cursor.execute("SELECT COUNT(*) FROM voters")

    if cursor.fetchone()[0] == 0:
        cursor.execute(
            "INSERT INTO voters (nik, name) VALUES (?, ?)",
            ("3201012304900001", "Siti Rahmawati")
        )

        cursor.execute(
            "INSERT INTO voters (nik, name) VALUES (?, ?)",
            ("3201011508850002", "Bambang Hermawan")
        )

        cursor.execute(
            "INSERT INTO voters (nik, name) VALUES (?, ?)",
            ("3201010101950003", "Eko Prasetyo")
        )

    conn.commit()
    conn.close()


# ==================================================
# FUNGSI PENDUKUNG
# ==================================================

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]

    except Exception:
        ip = "127.0.0.1"

    finally:
        s.close()

    return ip


def hash_session_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def create_qr_code(token):
    qr_folder = os.path.join(app.root_path, "static", "qr")
    os.makedirs(qr_folder, exist_ok=True)

    qr = qrcode.QRCode(
        version=1,
        box_size=10,
        border=4
    )

    qr.add_data(token)
    qr.make(fit=True)

    img = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    filepath = os.path.join(qr_folder, f"{token}.png")
    img.save(filepath)

    return filepath


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_logged_in"):
            flash("Anda harus login sebagai Admin Panitia!", "danger")
            return redirect(url_for("admin_login"))

        return f(*args, **kwargs)

    return decorated_function


# ==================================================
# HALAMAN AWAL PEMILIH
# ==================================================

@app.route("/")
def index():
    session.pop("voting_session", None)
    return render_template("index.html")


# ==================================================
# VERIFIKASI TOKEN
# ==================================================

@app.route("/verify-token", methods=["POST"])
def verify_token():
    token_input = request.form.get("token", "").strip().upper()

    if len(token_input) != 6:
        flash("Token harus terdiri dari 6 karakter!")
        return redirect(url_for("index"))

    conn = get_db_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        voter = conn.execute(
            """
            SELECT nik, name, token, is_voted
            FROM voters
            WHERE token = ? AND is_voted = 0
            """,
            (token_input,)
        ).fetchone()

        if not voter:
            conn.rollback()
            flash("TOKEN TIDAK VALID ATAU SUDAH DIGUNAKAN!")
            return redirect(url_for("index"))

        session_key = secrets.token_urlsafe(32)
        session_hash = hash_session_key(session_key)
        expires_at = int(time.time()) + VOTING_SESSION_SECONDS

        conn.execute(
            "DELETE FROM voting_sessions WHERE nik = ?",
            (voter["nik"],)
        )

        conn.execute(
            """
            INSERT INTO voting_sessions
            (session_hash, nik, expires_at)
            VALUES (?, ?, ?)
            """,
            (session_hash, voter["nik"], expires_at)
        )

        conn.commit()

        session["voting_session"] = session_key

        return redirect(url_for("ballot"))

    except Exception:
        conn.rollback()
        flash("Terjadi kesalahan saat memverifikasi token.")
        return redirect(url_for("index"))

    finally:
        conn.close()


# ==================================================
# HALAMAN SURAT SUARA
# ==================================================

@app.route("/ballot")
def ballot():
    session_key = session.get("voting_session")

    if not session_key:
        flash("Silakan verifikasi token terlebih dahulu.")
        return redirect(url_for("index"))

    conn = get_db_connection()

    try:
        voter_session = conn.execute(
            """
            SELECT vs.nik
            FROM voting_sessions vs
            JOIN voters v ON v.nik = vs.nik
            WHERE vs.session_hash = ?
              AND vs.expires_at > ?
              AND v.is_voted = 0
              AND v.token IS NOT NULL
            """,
            (hash_session_key(session_key), int(time.time()))
        ).fetchone()

        if not voter_session:
            session.pop("voting_session", None)
            flash("Sesi pemilihan sudah berakhir. Silakan verifikasi kembali.")
            return redirect(url_for("index"))

        candidates = conn.execute(
            "SELECT * FROM candidates ORDER BY id ASC"
        ).fetchall()

        return render_template(
            "ballot.html",
            candidates=candidates
        )

    finally:
        conn.close()


# ==================================================
# PROSES PENYIMPANAN SUARA
# ==================================================

@app.route("/vote", methods=["POST"])
def vote():
    session_key = session.get("voting_session")

    if not session_key:
        flash("Sesi pemilihan tidak ditemukan. Silakan verifikasi kembali.")
        return redirect(url_for("index"))

    candidate_id = request.form.get("candidate_id", "").strip()

    try:
        candidate_id = int(candidate_id)

    except (ValueError, TypeError):
        flash("Pilihan calon tidak valid!")
        return redirect(url_for("ballot"))

    conn = get_db_connection()

    try:
        conn.execute("BEGIN IMMEDIATE")

        voter_session = conn.execute(
            """
            SELECT vs.nik, v.token
            FROM voting_sessions vs
            JOIN voters v ON v.nik = vs.nik
            WHERE vs.session_hash = ?
              AND vs.expires_at > ?
              AND v.is_voted = 0
              AND v.token IS NOT NULL
            """,
            (hash_session_key(session_key), int(time.time()))
        ).fetchone()

        if not voter_session:
            conn.rollback()
            session.pop("voting_session", None)
            flash("Sesi tidak valid atau token sudah digunakan.")
            return redirect(url_for("index"))

        candidate = conn.execute(
            "SELECT id FROM candidates WHERE id = ?",
            (candidate_id,)
        ).fetchone()

        if not candidate:
            conn.rollback()
            flash("Kandidat tidak ditemukan!")
            return redirect(url_for("ballot"))

        # Suara disimpan tanpa NIK dan token
        conn.execute(
            "INSERT INTO votes (candidate_id) VALUES (?)",
            (candidate_id,)
        )

        # Token langsung dihanguskan setelah pemungutan suara
        updated = conn.execute(
            """
            UPDATE voters
            SET is_voted = 1, token = NULL
            WHERE nik = ?
              AND is_voted = 0
              AND token IS NOT NULL
            """,
            (voter_session["nik"],)
        )

        if updated.rowcount != 1:
            conn.rollback()
            session.pop("voting_session", None)
            flash("Status pemilih berubah. Silakan hubungi panitia.")
            return redirect(url_for("index"))

        conn.execute(
            "DELETE FROM voting_sessions WHERE session_hash = ?",
            (hash_session_key(session_key),)
        )

        conn.commit()

        session.pop("voting_session", None)

        return redirect(url_for("success"))

    except Exception:
        conn.rollback()
        flash("Terjadi kesalahan. Silakan hubungi panitia!")
        return redirect(url_for("index"))

    finally:
        conn.close()


@app.route("/success")
def success():
    return render_template("success.html")


# ==================================================
# LOGIN ADMIN
# ==================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin"))

        flash("Username atau Password Admin Salah!")

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


# ==================================================
# DASHBOARD ADMIN
# ==================================================

@app.route("/admin")
@admin_required
def admin():
    conn = get_db_connection()

    try:
        results = conn.execute("""
            SELECT c.id, c.name, COUNT(v.id) AS total_votes
            FROM candidates c
            LEFT JOIN votes v ON c.id = v.candidate_id
            GROUP BY c.id
        """).fetchall()

        voters = conn.execute(
            "SELECT * FROM voters ORDER BY name ASC"
        ).fetchall()

        total_dpt = len(voters)

        total_voted = conn.execute(
            "SELECT COUNT(*) FROM voters WHERE is_voted = 1"
        ).fetchone()[0]

        server_ip = get_local_ip()
        access_url = f"http://{server_ip}:5000"

        return render_template(
            "admin.html",
            results=results,
            voters=voters,
            total_dpt=total_dpt,
            total_voted=total_voted,
            access_url=access_url
        )

    finally:
        conn.close()


# ==================================================
# TAMBAH DPT
# ==================================================

@app.route("/admin/add-dpt", methods=["POST"])
@admin_required
def add_dpt():
    nik = request.form.get("nik", "").strip()
    name = request.form.get("name", "").strip()

    if not nik or not name:
        flash("NIK dan nama harus diisi!")
        return redirect(url_for("admin"))

    conn = get_db_connection()

    try:
        conn.execute(
            "INSERT INTO voters (nik, name) VALUES (?, ?)",
            (nik, name)
        )

        conn.commit()

        flash(f"DPT baru {name} berhasil ditambahkan!")

    except sqlite3.IntegrityError:
        flash("NIK tersebut sudah terdaftar di DPT!")

    finally:
        conn.close()

    return redirect(url_for("admin"))


# ==================================================
# GENERATE TOKEN DAN QR CODE
# ==================================================

@app.route("/admin/generate-token-nik", methods=["POST"])
@admin_required
def generate_token_nik():
    nik = request.form.get("nik", "").strip()

    conn = get_db_connection()

    try:
        voter = conn.execute(
            "SELECT * FROM voters WHERE nik = ?",
            (nik,)
        ).fetchone()

        if not voter:
            flash("NIK TIDAK TERDAFTAR DI DPT!")

        elif voter["is_voted"] == 1:
            flash(f"NIK {nik} ({voter['name']}) SUDAH MENCOBLOS!")

        else:
            token = voter["token"]

            if not token:
                token = None
                alphabet = string.ascii_uppercase + string.digits

                while not token:
                    candidate_token = "".join(
                        secrets.choice(alphabet)
                        for _ in range(6)
                    )

                    exists = conn.execute(
                        "SELECT 1 FROM voters WHERE token = ?",
                        (candidate_token,)
                    ).fetchone()

                    if not exists:
                        token = candidate_token

                conn.execute(
                    "UPDATE voters SET token = ? WHERE nik = ?",
                    (token, nik)
                )

                conn.commit()

                flash(
                    f"TOKEN UNTUK {voter['name']}: {token}"
                )

            else:
                flash(
                    f"TOKEN {voter['name']} SUDAH ADA: {token}"
                )

            create_qr_code(token)

    finally:
        conn.close()

    return redirect(url_for("admin"))


# ==================================================
# DOWNLOAD PDF TOKEN PEMILIH
# SATU PEMILIH = SATU HALAMAN A4
# ==================================================

@app.route("/admin/voter-pdf/<token>")
@admin_required
def voter_pdf(token):
    token = token.strip().upper()

    conn = get_db_connection()

    try:
        voter = conn.execute(
            """
            SELECT name, token, is_voted
            FROM voters
            WHERE token = ? AND is_voted = 0
            """,
            (token,)
        ).fetchone()

    finally:
        conn.close()

    if not voter:
        flash("Token tidak ditemukan, sudah digunakan, atau sudah tidak aktif!")
        return redirect(url_for("admin"))

    qr_path = create_qr_code(token)

    buffer = io.BytesIO()

    page_width, page_height = A4

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    pdf.setTitle("Kartu Token E-Voting")

    # Judul
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawCentredString(
        page_width / 2,
        page_height - 35 * mm,
        "KARTU TOKEN E-VOTING"
    )

    pdf.setFont("Helvetica", 11)
    pdf.drawCentredString(
        page_width / 2,
        page_height - 45 * mm,
        "SIMULASI PEMILIHAN"
    )

    # Garis pemisah
    pdf.setLineWidth(1)
    pdf.line(
        25 * mm,
        page_height - 55 * mm,
        page_width - 25 * mm,
        page_height - 55 * mm
    )

    # Identitas pemilih
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(
        30 * mm,
        page_height - 75 * mm,
        "NAMA PEMILIH"
    )

    pdf.setFont("Helvetica", 14)
    pdf.drawString(
        30 * mm,
        page_height - 85 * mm,
        voter["name"]
    )

    # Token
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawCentredString(
        page_width / 2,
        page_height - 110 * mm,
        "TOKEN PEMILIH"
    )

    pdf.setFont("Helvetica-Bold", 30)
    pdf.drawCentredString(
        page_width / 2,
        page_height - 127 * mm,
        token
    )

    # QR Code
    qr_size = 60 * mm
    qr_x = (page_width - qr_size) / 2
    qr_y = page_height - 205 * mm

    pdf.drawImage(
        qr_path,
        qr_x,
        qr_y,
        width=qr_size,
        height=qr_size
    )

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawCentredString(
        page_width / 2,
        qr_y - 12 * mm,
        "PETUNJUK PENGGUNAAN"
    )

    pdf.setFont("Helvetica", 10)

    instructions = [
        "1. Datang ke bilik pemungutan suara.",
        "2. Masukkan token secara manual atau pindai QR Code.",
        "3. Periksa surat suara dan pilih calon.",
        "4. Setiap token hanya dapat digunakan satu kali.",
        "5. Simpan kartu ini dan jangan berikan kepada orang lain."
    ]

    text_y = qr_y - 23 * mm

    for instruction in instructions:
        pdf.drawCentredString(
            page_width / 2,
            text_y,
            instruction
        )

        text_y -= 7 * mm

    # Footer
    pdf.setFont("Helvetica-Oblique", 9)
    pdf.drawCentredString(
        page_width / 2,
        20 * mm,
        "Dokumen simulasi E-Voting - Harap jaga kerahasiaan token."
    )

    pdf.showPage()
    pdf.save()

    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"kartu_token_{token}.pdf"
    )


# ==================================================
# QUICK COUNT
# ==================================================

@app.route("/admin/quick-count")
@admin_required
def quick_count():
    conn = get_db_connection()

    try:
        results = conn.execute("""
            SELECT
                c.id,
                c.name,
                c.photo,
                COUNT(v.id) AS total_votes
            FROM candidates c
            LEFT JOIN votes v ON c.id = v.candidate_id
            GROUP BY c.id, c.name, c.photo
            ORDER BY c.id ASC
        """).fetchall()

        total_votes = conn.execute(
            "SELECT COUNT(*) FROM votes"
        ).fetchone()[0]

        total_dpt = conn.execute(
            "SELECT COUNT(*) FROM voters"
        ).fetchone()[0]

        total_voted = conn.execute(
            "SELECT COUNT(*) FROM voters WHERE is_voted = 1"
        ).fetchone()[0]

        participation = (
            (total_voted / total_dpt) * 100
            if total_dpt > 0 else 0
        )

        quick_results = []

        for candidate in results:
            votes = candidate["total_votes"]

            percentage = (
                (votes / total_votes) * 100
                if total_votes > 0 else 0
            )

            quick_results.append({
                "id": candidate["id"],
                "name": candidate["name"],
                "photo": candidate["photo"],
                "total_votes": votes,
                "percentage": round(percentage, 2)
            })

        return render_template(
            "quick_count.html",
            results=quick_results,
            total_votes=total_votes,
            total_dpt=total_dpt,
            total_voted=total_voted,
            participation=round(participation, 2)
        )

    finally:
        conn.close()


# ==================================================
# JALANKAN SERVER
# ==================================================

if __name__ == "__main__":
    init_db()

    server_ip = get_local_ip()

    print("=" * 65)
    print(" SERVER TPS E-VOTING SIAP DIGUNAKAN!")
    print(f" LINK BILIK SUARA : http://{server_ip}:5000")
    print(f" LINK ADMIN       : http://localhost:5000/admin")
    print("=" * 65)

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
