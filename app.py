
import os
import random
import string
import socket
import secrets
import hashlib
import time

from functools import wraps

import psycopg2
from psycopg2 import IntegrityError
from psycopg2.extensions import connection as PGConnection
from psycopg2.extras import DictCursor

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session
)


# ==================================================
# KONFIGURASI APLIKASI
# ==================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "kunci-lokal-untuk-simulasi-ganti-saat-online"
)

DATABASE_URL = os.environ.get("DATABASE_URL")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "3003")

VOTING_SESSION_SECONDS = 600


# Membuat koneksi PostgreSQL yang mendukung conn.execute()
# dan hasil query yang bisa diakses dengan nama kolom maupun indeks.
class AppConnection(PGConnection):
    def execute(self, query, params=None):
        cursor = self.cursor()
        cursor.execute(query, params or ())
        return cursor


def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL belum diatur. "
            "Masukkan Connection String Neon ke environment."
        )

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require",
        connection_factory=AppConnection,
        cursor_factory=DictCursor
    )


# ==================================================
# MEMBUAT DATABASE DAN TABEL OTOMATIS
# ==================================================

def init_db():
    conn = get_db_connection()

    try:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candidates (
                id SERIAL PRIMARY KEY,
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
                id SERIAL PRIMARY KEY,
                candidate_id INTEGER NOT NULL,
                FOREIGN KEY (candidate_id)
                    REFERENCES candidates(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS voting_sessions (
                session_hash TEXT PRIMARY KEY,
                nik TEXT UNIQUE NOT NULL,
                expires_at BIGINT NOT NULL
            )
        """)

        cursor.execute("SELECT COUNT(*) FROM candidates")

        if cursor.fetchone()[0] == 0:
            cursor.execute(
                """
                INSERT INTO candidates (name, vision, photo)
                VALUES (%s, %s, %s)
                """,
                ("01.ABAH KOMENG", "KAMARUNG", "calon1.jpg")
            )

            cursor.execute(
                """
                INSERT INTO candidates (name, vision, photo)
                VALUES (%s, %s, %s)
                """,
                ("02. AJAY", "KAMARUNG", "calon2.jpg")
            )

        cursor.execute("SELECT COUNT(*) FROM voters")

        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "INSERT INTO voters (nik, name) VALUES (%s, %s)",
                ("3201012304900001", "Siti Rahmawati")
            )

            cursor.execute(
                "INSERT INTO voters (nik, name) VALUES (%s, %s)",
                ("3201011508850002", "Bambang Hermawan")
            )

            cursor.execute(
                "INSERT INTO voters (nik, name) VALUES (%s, %s)",
                ("3201010101950003", "Eko Prasetyo")
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
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


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_logged_in"):
            flash(
                "Anda harus login sebagai Admin Panitia!",
                "danger"
            )
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
# VERIFIKASI TOKEN PEMILIH
# ==================================================

@app.route("/verify-token", methods=["POST"])
def verify_token():
    token_input = request.form.get(
        "token", ""
    ).strip().upper()

    if len(token_input) != 6:
        flash("Token harus terdiri dari 6 karakter!")
        return redirect(url_for("index"))

    conn = get_db_connection()

    try:
        voter = conn.execute(
            """
            SELECT nik, name, token, is_voted
            FROM voters
            WHERE token = %s AND is_voted = 0
            FOR UPDATE
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
            "DELETE FROM voting_sessions WHERE nik = %s",
            (voter["nik"],)
        )

        conn.execute(
            """
            INSERT INTO voting_sessions
            (session_hash, nik, expires_at)
            VALUES (%s, %s, %s)
            """,
            (session_hash, voter["nik"], expires_at)
        )

        conn.commit()

        session["voting_session"] = session_key

        return redirect(url_for("ballot"))

    except Exception:
        conn.rollback()
        app.logger.exception("Kesalahan verifikasi token")
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
            WHERE vs.session_hash = %s
              AND vs.expires_at > %s
              AND v.is_voted = 0
              AND v.token IS NOT NULL
            """,
            (hash_session_key(session_key), int(time.time()))
        ).fetchone()

        if not voter_session:
            session.pop("voting_session", None)
            flash(
                "Sesi pemilihan sudah berakhir. "
                "Silakan verifikasi kembali."
            )
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
        flash(
            "Sesi pemilihan tidak ditemukan. "
            "Silakan verifikasi kembali."
        )
        return redirect(url_for("index"))

    candidate_id = request.form.get(
        "candidate_id", ""
    ).strip()

    try:
        candidate_id = int(candidate_id)
    except (ValueError, TypeError):
        flash("Pilihan calon tidak valid!")
        return redirect(url_for("ballot"))

    conn = get_db_connection()

    try:
        voter_session = conn.execute(
            """
            SELECT vs.nik, v.token
            FROM voting_sessions vs
            JOIN voters v ON v.nik = vs.nik
            WHERE vs.session_hash = %s
              AND vs.expires_at > %s
              AND v.is_voted = 0
              AND v.token IS NOT NULL
            FOR UPDATE OF v
            """,
            (hash_session_key(session_key), int(time.time()))
        ).fetchone()

        if not voter_session:
            conn.rollback()
            session.pop("voting_session", None)
            flash("Sesi tidak valid atau token sudah digunakan.")
            return redirect(url_for("index"))

        candidate = conn.execute(
            "SELECT id FROM candidates WHERE id = %s",
            (candidate_id,)
        ).fetchone()

        if not candidate:
            conn.rollback()
            flash("Kandidat tidak ditemukan!")
            return redirect(url_for("ballot"))

        # Suara disimpan tanpa NIK atau token pemilih.
        conn.execute(
            "INSERT INTO votes (candidate_id) VALUES (%s)",
            (candidate_id,)
        )

        updated = conn.execute(
            """
            UPDATE voters
            SET is_voted = 1, token = NULL
            WHERE nik = %s
              AND is_voted = 0
              AND token IS NOT NULL
            """,
            (voter_session["nik"],)
        )

        if updated.rowcount != 1:
            conn.rollback()
            session.pop("voting_session", None)
            flash(
                "Status pemilih berubah. "
                "Silakan hubungi panitia."
            )
            return redirect(url_for("index"))

        conn.execute(
            "DELETE FROM voting_sessions WHERE session_hash = %s",
            (hash_session_key(session_key),)
        )

        conn.commit()

        session.pop("voting_session", None)

        return redirect(url_for("success"))

    except Exception:
        conn.rollback()
        app.logger.exception("Kesalahan penyimpanan suara")
        flash("Terjadi kesalahan. Silakan hubungi panitia!")
        return redirect(url_for("index"))

    finally:
        conn.close()


@app.route("/success")
def success():
    return render_template("success.html")


# ==================================================
# LOGIN ADMIN PANITIA
# ==================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if (
            username == ADMIN_USERNAME
            and password == ADMIN_PASSWORD
        ):
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
            SELECT
                c.id,
                c.name,
                COUNT(v.id) AS total_votes
            FROM candidates c
            LEFT JOIN votes v ON c.id = v.candidate_id
            GROUP BY c.id
        """).fetchall()

        voters = conn.execute(
            "SELECT * FROM voters"
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

    if nik and name:
        conn = get_db_connection()

        try:
            conn.execute(
                """
                INSERT INTO voters (nik, name)
                VALUES (%s, %s)
                """,
                (nik, name)
            )

            conn.commit()

            flash(
                f"DPT Baru {name} ({nik}) "
                "Berhasil Ditambahkan!"
            )

        except IntegrityError:
            conn.rollback()
            flash("NIK Tersebut Sudah Terdaftar di DPT!")

        except Exception:
            conn.rollback()
            app.logger.exception("Kesalahan penambahan DPT")
            flash("Terjadi kesalahan saat menambahkan DPT.")

        finally:
            conn.close()

    return redirect(url_for("admin"))


# ==================================================
# GENERATE TOKEN
# ==================================================

@app.route("/admin/generate-token-nik", methods=["POST"])
@admin_required
def generate_token_nik():
    nik = request.form.get("nik", "").strip()

    conn = get_db_connection()

    try:
        voter = conn.execute(
            "SELECT * FROM voters WHERE nik = %s",
            (nik,)
        ).fetchone()

        if not voter:
            flash("NIK TIDAK TERDAFTAR DI DPT!")

        elif voter["is_voted"] == 1:
            flash(
                f"NIK {nik} ({voter['name']}) "
                "SUDAH MENCOBLOS!"
            )

        elif not voter["token"]:
            new_token = None

            while not new_token:
                candidate_token = "".join(
                    random.choices(
                        string.ascii_uppercase + string.digits,
                        k=6
                    )
                )

                exists = conn.execute(
                    "SELECT 1 FROM voters WHERE token = %s",
                    (candidate_token,)
                ).fetchone()

                if not exists:
                    new_token = candidate_token

            conn.execute(
                "UPDATE voters SET token = %s WHERE nik = %s",
                (new_token, nik)
            )

            conn.commit()

            flash(
                f"TOKEN UNTUK {voter['name']} "
                f"({nik}): {new_token}"
            )

        else:
            flash(
                f"TOKEN {voter['name']} "
                f"SUDAH ADA: {voter['token']}"
            )

    except Exception:
        conn.rollback()
        app.logger.exception("Kesalahan pembuatan token")
        flash("Terjadi kesalahan saat membuat token.")

    finally:
        conn.close()

    return redirect(url_for("admin"))


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
# INISIALISASI DATABASE UNTUK GUNICORN / RENDER
# ==================================================

# Dipanggil saat aplikasi dimuat oleh Gunicorn.
init_db()


# ==================================================
# JALANKAN SERVER LOKAL
# ==================================================

if __name__ == "__main__":
    server_ip = get_local_ip()

    print("=" * 65)
    print(" SERVER TPS E-VOTING SIAP DIGUNAKAN!")
    print(f" LINK BILIK SUARA : http://{server_ip}:5000")
    print(f" LINK ADMIN       : http://localhost:5000/admin")
    print("=" * 65)

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
