import csv
import hashlib
import html
import http.cookies
import io
import mimetypes
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


SAMPLE_STUDENTS = [
    {"student_name": "Amina Yusuf", "attendance": 94, "math": 91, "science": 88, "english": 86, "study_hours": 7.5},
    {"student_name": "Brian Otieno", "attendance": 72, "math": 68, "science": 63, "english": 71, "study_hours": 3.0},
    {"student_name": "Chloe Kamau", "attendance": 88, "math": 82, "science": 79, "english": 85, "study_hours": 5.5},
    {"student_name": "David Mwangi", "attendance": 65, "math": 61, "science": 58, "english": 64, "study_hours": 2.2},
    {"student_name": "Elena Njeri", "attendance": 96, "math": 94, "science": 92, "english": 90, "study_hours": 8.0},
    {"student_name": "Farah Ali", "attendance": 81, "math": 77, "science": 74, "english": 79, "study_hours": 4.5},
    {"student_name": "George Maina", "attendance": 74, "math": 65, "science": 69, "english": 72, "study_hours": 3.5},
    {"student_name": "Hannah Wanjiku", "attendance": 91, "math": 89, "science": 86, "english": 84, "study_hours": 6.8},
    {"student_name": "Ibrahim Noor", "attendance": 58, "math": 54, "science": 51, "english": 57, "study_hours": 1.5},
    {"student_name": "Joyce Atieno", "attendance": 84, "math": 80, "science": 77, "english": 82, "study_hours": 5.0},
    {"student_name": "Kevin Ouma", "attendance": 69, "math": 60, "science": 64, "english": 66, "study_hours": 2.8},
    {"student_name": "Lilian Achieng", "attendance": 98, "math": 95, "science": 93, "english": 97, "study_hours": 8.5},
]

DATABASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "students.db")
MATERIALS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learning_materials")
PROFILE_PICTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile_pictures")
ALLOWED_MATERIAL_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".doc", ".docx", ".txt", ".zip"}
MAX_MATERIAL_SIZE = 20 * 1024 * 1024
MAX_PROFILE_PICTURE_SIZE = 5 * 1024 * 1024
os.makedirs(MATERIALS_DIR, exist_ok=True)
os.makedirs(PROFILE_PICTURES_DIR, exist_ok=True)


@contextmanager
def get_database():
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database():
    with get_database() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT,
                student_name TEXT NOT NULL,
                attendance REAL NOT NULL,
                math REAL NOT NULL,
                science REAL NOT NULL,
                english REAL NOT NULL,
                study_hours REAL NOT NULL
            )
            """
        )
        student_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(students)").fetchall()
        }
        if "student_id" not in student_columns:
            connection.execute("ALTER TABLE students ADD COLUMN student_id TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                subject TEXT NOT NULL,
                category TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS student_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                profile_picture TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        account_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(student_accounts)").fetchall()
        }
        if "profile_picture" not in account_columns:
            connection.execute("ALTER TABLE student_accounts ADD COLUMN profile_picture TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS login_sessions (
                token_hash TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY (account_id) REFERENCES student_accounts(id) ON DELETE CASCADE
            )
            """
        )
        count = connection.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        if count == 0:
            connection.executemany(
                """
                INSERT INTO students
                    (student_id, student_name, attendance, math, science, english, study_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        f"STU-{index:04d}",
                        student["student_name"],
                        student["attendance"],
                        student["math"],
                        student["science"],
                        student["english"],
                        student["study_hours"],
                    )
                    for index, student in enumerate(SAMPLE_STUDENTS, start=1)
                ],
            )
        existing_ids = {
            row["student_id"].casefold()
            for row in connection.execute(
                "SELECT student_id FROM students WHERE student_id IS NOT NULL AND student_id != ''"
            ).fetchall()
        }
        missing_ids = connection.execute(
            "SELECT id FROM students WHERE student_id IS NULL OR student_id = '' ORDER BY id"
        ).fetchall()
        for row in missing_ids:
            candidate = f"STU-{row['id']:04d}"
            suffix = 1
            while candidate.casefold() in existing_ids:
                candidate = f"STU-{row['id']:04d}-{suffix}"
                suffix += 1
            connection.execute(
                "UPDATE students SET student_id = ? WHERE id = ?",
                (candidate, row["id"]),
            )
            existing_ids.add(candidate.casefold())
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_students_student_id ON students(student_id COLLATE NOCASE)"
        )


def get_students():
    with get_database() as connection:
        rows = connection.execute(
            """
            SELECT student_id, student_name, attendance, math, science, english, study_hours
            FROM students
            ORDER BY id
            """
        ).fetchall()
    return [prepare_student(dict(row)) for row in rows]


def add_student(student):
    prepared = prepare_student(student)
    try:
        with get_database() as connection:
            connection.execute(
                """
                INSERT INTO students
                    (student_id, student_name, attendance, math, science, english, study_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prepared["student_id"],
                    prepared["student_name"],
                    prepared["attendance"],
                    prepared["math"],
                    prepared["science"],
                    prepared["english"],
                    prepared["study_hours"],
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("That student ID is already in use.") from exc


def replace_students(students):
    with get_database() as connection:
        connection.execute("DELETE FROM students")
        connection.executemany(
            """
            INSERT INTO students
                (student_id, student_name, attendance, math, science, english, study_hours)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    student["student_id"],
                    student["student_name"],
                    student["attendance"],
                    student["math"],
                    student["science"],
                    student["english"],
                    student["study_hours"],
                )
                for student in students
            ],
        )


PASSWORD_ITERATIONS = 310_000
SESSION_LIFETIME = 60 * 60 * 24 * 7


def hash_password(password, salt=None):
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, PASSWORD_ITERATIONS
    )
    return salt_bytes.hex(), password_hash.hex()


def create_session(account_id):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    expires_at = int(time.time()) + SESSION_LIFETIME
    with get_database() as connection:
        connection.execute(
            "INSERT INTO login_sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, account_id, expires_at),
        )
    return token


def get_account_for_session(token):
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = int(time.time())
    with get_database() as connection:
        connection.execute("DELETE FROM login_sessions WHERE expires_at <= ?", (now,))
        row = connection.execute(
            """
            SELECT a.id AS account_id, a.username, s.student_id, s.student_name,
                   s.attendance, s.math, s.science, s.english, s.study_hours
            FROM login_sessions AS session
            JOIN student_accounts AS a ON a.id = session.account_id
            JOIN students AS s ON s.student_id = a.student_id COLLATE NOCASE
            WHERE session.token_hash = ? AND session.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
    return prepare_student(dict(row)) if row else None


def register_account(student_id, student_name, username, password):
    student_id = student_id.strip()
    student_name = student_name.strip()
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", username):
        raise ValueError("Username must be 3-30 letters, numbers, dots, underscores, or hyphens.")
    if len(password) < 10 or len(password) > 128:
        raise ValueError("Password must be between 10 and 128 characters.")

    with get_database() as connection:
        student = connection.execute(
            """
            SELECT student_id, student_name FROM students
            WHERE student_id = ? COLLATE NOCASE
            """,
            (student_id,),
        ).fetchone()
        if not student or student["student_name"].strip().casefold() != student_name.casefold():
            raise ValueError("Student ID and full name do not match the student roster.")
        if connection.execute(
            "SELECT 1 FROM student_accounts WHERE student_id = ? COLLATE NOCASE",
            (student_id,),
        ).fetchone():
            raise ValueError("An account already exists for this student. Please sign in.")
        salt, password_hash = hash_password(password)
        try:
            cursor = connection.execute(
                """
                INSERT INTO student_accounts (student_id, username, password_salt, password_hash)
                VALUES (?, ?, ?, ?)
                """,
                (student["student_id"], username, salt, password_hash),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("That username is already in use. Choose another.") from exc
        return cursor.lastrowid


def authenticate_account(username, password):
    with get_database() as connection:
        account = connection.execute(
            "SELECT id, password_salt, password_hash FROM student_accounts WHERE username = ?",
            (username.strip(),),
        ).fetchone()
    if not account:
        return None
    _, supplied_hash = hash_password(password, account["password_salt"])
    return account["id"] if secrets.compare_digest(supplied_hash, account["password_hash"]) else None


def delete_session(token):
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    with get_database() as connection:
        connection.execute("DELETE FROM login_sessions WHERE token_hash = ?", (token_hash,))


def save_profile_picture(account_id, original_name, content):
    extension = os.path.splitext(os.path.basename(original_name))[1].lower()
    signatures = {
        ".png": (b"\x89PNG\r\n\x1a\n", "image/png"),
        ".jpg": (b"\xff\xd8\xff", "image/jpeg"),
        ".jpeg": (b"\xff\xd8\xff", "image/jpeg"),
    }
    if extension not in signatures:
        raise ValueError("Choose a PNG or JPEG image.")
    if not content or len(content) > MAX_PROFILE_PICTURE_SIZE:
        raise ValueError("The profile picture must be between 1 byte and 5 MB.")
    signature, content_type = signatures[extension]
    if not content.startswith(signature):
        raise ValueError("The file contents do not match a PNG or JPEG image.")

    stored_name = f"{uuid.uuid4().hex}{extension}"
    stored_path = os.path.join(PROFILE_PICTURES_DIR, stored_name)
    with open(stored_path, "wb") as picture_file:
        picture_file.write(content)
    try:
        with get_database() as connection:
            row = connection.execute(
                "SELECT profile_picture FROM student_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if not row:
                raise ValueError("Student account was not found.")
            old_name = row["profile_picture"]
            connection.execute(
                "UPDATE student_accounts SET profile_picture = ? WHERE id = ?",
                (stored_name, account_id),
            )
    except Exception:
        if os.path.exists(stored_path):
            os.remove(stored_path)
        raise

    if old_name:
        old_path = os.path.join(PROFILE_PICTURES_DIR, old_name)
        if os.path.isfile(old_path):
            os.remove(old_path)


def get_profile_picture(account_id):
    with get_database() as connection:
        row = connection.execute(
            "SELECT profile_picture FROM student_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
    if not row or not row["profile_picture"]:
        return None
    stored_name = row["profile_picture"]
    path = os.path.join(PROFILE_PICTURES_DIR, stored_name)
    if not os.path.isfile(path):
        return None
    extension = os.path.splitext(stored_name)[1].lower()
    content_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(extension)
    if not content_type:
        return None
    return path, content_type


def get_learning_materials():
    with get_database() as connection:
        rows = connection.execute(
            """
            SELECT id, title, subject, category, original_name, file_size, uploaded_at
            FROM learning_materials
            ORDER BY id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_learning_material(material_id):
    with get_database() as connection:
        row = connection.execute(
            """
            SELECT id, original_name, stored_name
            FROM learning_materials
            WHERE id = ?
            """,
            (material_id,),
        ).fetchone()
    return dict(row) if row else None


def add_learning_material(title, subject, category, original_name, content):
    if not title.strip() or not subject.strip():
        raise ValueError("Enter a title and subject.")
    if len(content) > MAX_MATERIAL_SIZE:
        raise ValueError("The file exceeds the 20 MB upload limit.")

    extension = os.path.splitext(original_name)[1].lower()
    if extension not in ALLOWED_MATERIAL_EXTENSIONS:
        raise ValueError("Allowed file types: PDF, PPT, PPTX, DOC, DOCX, TXT, and ZIP.")

    safe_original_name = os.path.basename(original_name)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    stored_path = os.path.join(MATERIALS_DIR, stored_name)
    with open(stored_path, "wb") as material_file:
        material_file.write(content)

    try:
        with get_database() as connection:
            connection.execute(
                """
                INSERT INTO learning_materials
                    (title, subject, category, original_name, stored_name, file_size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (title.strip(), subject.strip(), category, safe_original_name, stored_name, len(content)),
            )
    except Exception:
        if os.path.exists(stored_path):
            os.remove(stored_path)
        raise


def calculate_grade(score):
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 50:
        return "D"
    return "F"


def prepare_student(row):
    student_id = str(row.get("student_id") or "").strip()
    if not student_id:
        raise ValueError("Student ID is required.")
    numeric_fields = ("attendance", "math", "science", "english", "study_hours")
    try:
        normalized = {field: float(row[field]) for field in numeric_fields}
    except (KeyError, TypeError, ValueError):
        raise ValueError("All numeric fields must contain valid numbers.")

    average = sum(normalized[field] for field in ("attendance", "math", "science", "english")) / 4
    if average < 60 or normalized["attendance"] < 70:
        risk = "At Risk"
    elif average < 75 or normalized["attendance"] < 85:
        risk = "Needs Attention"
    else:
        risk = "On Track"

    return {
        "student_id": student_id,
        "account_id": row.get("account_id"),
        "student_name": str(row.get("student_name") or "Unknown student").strip(),
        **normalized,
        "average_score": round(average, 1),
        "grade": calculate_grade(average),
        "risk_level": risk,
    }


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("The CSV file has no header row.")

    normalized_headers = {
        str(header).strip().lower().replace(" ", "_"): header
        for header in reader.fieldnames
    }
    required = ("student_id", "student_name", "attendance", "math", "science", "english", "study_hours")
    missing = [field for field in required if field not in normalized_headers]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))

    students = []
    seen_student_ids = set()
    for raw_row in reader:
        row = {
            required_field: raw_row.get(normalized_headers[required_field])
            for required_field in required
        }
        try:
            student = prepare_student(row)
        except ValueError:
            continue
        normalized_id = student["student_id"].casefold()
        if normalized_id in seen_student_ids:
            raise ValueError("Student IDs in the CSV must be unique.")
        seen_student_ids.add(normalized_id)
        students.append(student)
    if not students:
        raise ValueError("The CSV has no valid student records.")
    return students


def parse_multipart(content_type, body):
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii", "ignore")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    fields = {}
    uploaded_file = None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        if filename:
            uploaded_file = (filename, payload or b"")
        elif name:
            fields[name] = (payload or b"").decode("utf-8", "replace").strip()
    return fields, uploaded_file


def format_file_size(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def material_table_rows(materials):
    if not materials:
        return '<tr><td colspan="5" class="empty">No learning materials uploaded yet.</td></tr>'
    rows = []
    for material in materials:
        rows.append(
            "<tr>"
            f'<td><strong>{esc(material["title"])}</strong><div class="file-name">{esc(material["original_name"])}</div></td>'
            f'<td>{esc(material["subject"])}</td>'
            f'<td>{esc(material["category"])}</td>'
            f'<td>{format_file_size(material["file_size"])}</td>'
            f'<td><a class="material-download" href="/materials/download/{material["id"]}">Download</a></td>'
            "</tr>"
        )
    return "\n".join(rows)


def esc(value):
    return html.escape(str(value))


def table_rows(students):
    if not students:
        return '<tr><td colspan="7" class="empty">No students match these filters.</td></tr>'

    rows = []
    for student in sorted(students, key=lambda item: item["average_score"], reverse=True):
        risk_class = student["risk_level"].lower().replace(" ", "-")
        rows.append(
            "<tr>"
            f'<td>{esc(student["student_id"])}</td>'
            f'<td><strong>{esc(student["student_name"])}</strong></td>'
            f'<td>{student["average_score"]:.1f}%</td>'
            f'<td><span class="grade">{esc(student["grade"])}</span></td>'
            f'<td>{student["attendance"]:.1f}%</td>'
            f'<td>{student["study_hours"]:.1f} hrs</td>'
            f'<td><span class="risk {risk_class}">{esc(student["risk_level"])}</span></td>'
            "</tr>"
        )
    return "\n".join(rows)


def average(students, field):
    if not students:
        return 0
    return sum(student[field] for student in students) / len(students)


def subject_bars(students):
    bars = []
    for label, field, color in (
        ("Math", "math", "#5577cc"),
        ("Science", "science", "#25a18e"),
        ("English", "english", "#f59e0b"),
    ):
        score = average(students, field)
        bars.append(
            f'<div class="bar-row"><span>{label}</span>'
            f'<div class="bar-track"><div class="bar" style="width:{score:.1f}%;background:{color}"></div></div>'
            f'<strong>{score:.1f}%</strong></div>'
        )
    return "\n".join(bars)


def risk_bars(students):
    bars = []
    colors = {"On Track": "#25a18e", "Needs Attention": "#f59e0b", "At Risk": "#dc4c64"}
    for risk in ("On Track", "Needs Attention", "At Risk"):
        count = sum(student["risk_level"] == risk for student in students)
        width = (count / len(students) * 100) if students else 0
        bars.append(
            f'<div class="bar-row"><span>{risk}</span>'
            f'<div class="bar-track"><div class="bar" style="width:{width:.1f}%;background:{colors[risk]}"></div></div>'
            f'<strong>{count}</strong></div>'
        )
    return "\n".join(bars)


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="A student performance dashboard created by Denis Emoru Epur Disii for monitoring grades, attendance, study habits, and academic risk.">
  <title>Student Performance Dashboard</title>
  <style>
    :root { --ink:#17233c; --muted:#667085; --line:#e8ecf3; --panel:#fff; --bg:#f4f6fb; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Segoe UI,Arial,sans-serif; background:var(--bg); color:var(--ink); }
    .shell { display:grid; grid-template-columns:250px minmax(0,1fr); min-height:100vh; }
    aside { background:#17233c; color:white; padding:28px 22px; }
    aside h2 { margin:8px 0 24px; font-size:20px; }
    .logo { width:42px;height:42px;border-radius:12px;background:#5577cc;display:grid;place-items:center;font-size:24px; }
    aside label, aside legend { display:block; margin:14px 0 7px; font-size:13px; color:#cad3e6; }
    input, select { width:100%; padding:10px 12px; border:1px solid #dfe4ed; border-radius:8px; background:white; }
    aside input { border:0; }
    aside form { margin-top:20px; }
    aside .range { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    button { border:0;border-radius:8px;padding:10px 14px;font-weight:700;cursor:pointer;background:#5577cc;color:white; }
    aside button { width:100%; margin-top:14px; }
    main { padding:30px; min-width:0; }
    h1 { margin:0 0 4px; font-size:28px; }
    .subtitle { color:var(--muted); margin:0 0 24px; }
    .filters { display:flex; gap:12px; margin:18px 0; }
    .filters label { color:var(--muted); font-size:13px; }
    .filters select { width:170px; margin-top:5px; }
    .metrics { display:grid; grid-template-columns:repeat(4,minmax(140px,1fr)); gap:14px; }
    .metric,.card { background:var(--panel); border:1px solid var(--line); border-radius:14px; box-shadow:0 8px 24px rgba(23,35,60,.04); }
    .metric { padding:18px; }
    .metric span { color:var(--muted); font-size:13px; }
    .metric strong { display:block; margin-top:7px; font-size:26px; }
    .grid { display:grid; grid-template-columns:2fr 1fr; gap:14px; margin:14px 0; }
    .card { padding:18px; }
    .card h3 { margin:0 0 16px; font-size:16px; }
    .bar-row { display:grid; grid-template-columns:110px 1fr 55px; align-items:center; gap:10px; margin:12px 0; font-size:13px; }
    .bar-track { height:9px; background:#edf0f5; border-radius:99px; overflow:hidden; }
    .bar { height:100%; border-radius:99px; }
    table { width:100%; border-collapse:collapse; background:white; border-radius:14px; overflow:hidden; }
    th,td { padding:13px 14px; border-bottom:1px solid var(--line); text-align:left; font-size:14px; }
    th { color:var(--muted); font-size:12px; text-transform:uppercase; }
    tr:last-child td { border-bottom:0; }
    .grade { display:inline-grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#edf2ff;color:#4160ad;font-weight:bold; }
    .risk { padding:5px 8px;border-radius:99px;font-size:12px;font-weight:700; }
    .on-track { background:#ddf5ef;color:#197a68; }.needs-attention { background:#fff1d6;color:#9a6700; }.at-risk { background:#ffe3e7;color:#b52e43; }
    .empty { text-align:center; color:var(--muted); }
    .error { margin:14px 0;padding:10px 12px;border-radius:8px;background:#ffe3e7;color:#8d2435; }
    .download { display:inline-block;margin-top:14px;text-decoration:none; }
    .description-card { margin-bottom:14px;line-height:1.6;color:var(--muted); }
    .description-card strong { color:var(--ink); }
    .account-links { display:flex;justify-content:flex-end;gap:12px;margin-bottom:16px; }
    .account-links a,.portal-signout { color:#4160ad;font-weight:700;text-decoration:none; }
    .materials-card { margin-top:14px;padding:0;overflow-x:auto; }
    .materials-card h3 { padding:18px 18px 0; }
    .file-name { color:var(--muted);font-size:12px;margin-top:3px; }
    .material-download { color:#4160ad;font-weight:700;text-decoration:none; }
    footer { margin-top:22px;text-align:center;color:var(--muted);font-size:13px; }
    @media (max-width:900px) {
      .shell { grid-template-columns:1fr; }.metrics { grid-template-columns:repeat(2,1fr); }.grid { grid-template-columns:1fr; }
      aside { min-height:auto; } main { padding:20px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="logo">S</div>
      <h2>Student Data</h2>
      <form method="post" action="/upload" enctype="multipart/form-data">
        <label for="csv">Upload CSV</label>
        <input id="csv" name="csv" type="file" accept=".csv,text/csv" required>
        <button type="submit">Use CSV</button>
      </form>
      <form method="post" action="/add">
        <legend>Add a student</legend>
        <label>Student ID</label><input name="student_id" placeholder="STU-0013" required>
        <label>Name</label><input name="student_name" required>
        <label>Attendance (%)</label><input name="attendance" type="number" min="0" max="100" value="85" required>
        <div class="range">
          <div><label>Math</label><input name="math" type="number" min="0" max="100" value="75" required></div>
          <div><label>Science</label><input name="science" type="number" min="0" max="100" value="75" required></div>
        </div>
        <label>English (%)</label><input name="english" type="number" min="0" max="100" value="75" required>
        <label>Study hours/day</label><input name="study_hours" type="number" min="0" max="24" step="0.5" value="4" required>
        <button type="submit">Add student</button>
      </form>
      <form method="post" action="/materials/upload" enctype="multipart/form-data">
        <legend>Learning materials</legend>
        <label>Title</label><input name="title" placeholder="Revision notes" required>
        <label>Subject</label><input name="subject" placeholder="Mathematics" required>
        <label>For</label>
        <select name="category" required>
          <option>Assignments</option>
          <option>CATs</option>
          <option>Exams</option>
          <option>General study</option>
        </select>
        <label for="material">File</label>
        <input id="material" name="material" type="file"
          accept=".pdf,.ppt,.pptx,.doc,.docx,.txt,.zip" required>
        <button type="submit">Upload material</button>
      </form>
    </aside>
    <main>
      <nav class="account-links">
        <a href="/account?mode=register">Create student account</a>
        <a href="/account?mode=login">Student sign in</a>
      </nav>
      <h1>Student Performance Dashboard</h1>
      <p class="subtitle">A complete view of academic performance, attendance, study habits, grades, and student support needs.</p>
      <div class="card description-card">
        <strong>About this project:</strong> This dashboard helps teachers and academic teams identify
        progress and at-risk students through clear scores, attendance data, subject trends, and CSV reporting.
      </div>
      __ERROR__
      <form class="filters" method="get">
        <label>Grade
          <select name="grade" onchange="this.form.submit()">
            __GRADE_OPTIONS__
          </select>
        </label>
        <label>Risk level
          <select name="risk" onchange="this.form.submit()">
            __RISK_OPTIONS__
          </select>
        </label>
      </form>
      <section class="metrics">
        <div class="metric"><span>Students</span><strong>__STUDENT_COUNT__</strong></div>
        <div class="metric"><span>Average score</span><strong>__AVERAGE_SCORE__%</strong></div>
        <div class="metric"><span>Average attendance</span><strong>__AVERAGE_ATTENDANCE__%</strong></div>
        <div class="metric"><span>At-risk students</span><strong>__AT_RISK_COUNT__</strong></div>
      </section>
      <section class="grid">
        <div class="card"><h3>Subject performance</h3>__SUBJECT_BARS__</div>
        <div class="card"><h3>Risk distribution</h3>__RISK_BARS__</div>
      </section>
      <div class="card" style="padding:0;overflow-x:auto">
        <table>
          <thead><tr><th>Student ID</th><th>Student</th><th>Average</th><th>Grade</th><th>Attendance</th><th>Study time</th><th>Risk</th></tr></thead>
          <tbody>__TABLE_ROWS__</tbody>
        </table>
      </div>
      <div class="card materials-card">
        <h3>Learning materials for CATs, assignments, and exams</h3>
        <table>
          <thead><tr><th>Material</th><th>Subject</th><th>Purpose</th><th>Size</th><th>Action</th></tr></thead>
          <tbody>__MATERIAL_ROWS__</tbody>
        </table>
      </div>
      <a class="download" href="/download?grade=__GRADE_PARAM__&risk=__RISK_PARAM__"><button type="button">Download filtered CSV</button></a>
      <footer>Written and created by <strong>Denis Emoru Epur Disii</strong></footer>
    </main>
  </div>
</body>
</html>"""


ACCOUNT_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Student account | Student Performance Dashboard</title>
  <style>
    body{margin:0;background:#f4f6fb;color:#17233c;font:16px Segoe UI,Arial,sans-serif}
    main{max-width:480px;margin:7vh auto;padding:30px;background:white;border:1px solid #e8ecf3;border-radius:16px}
    h1{margin-top:0;font-size:26px}p{color:#667085;line-height:1.55}
    label{display:block;margin:14px 0 6px;font-size:14px}
    input{box-sizing:border-box;width:100%;padding:11px;border:1px solid #dfe4ed;border-radius:8px}
    button{margin-top:20px;padding:11px 16px;border:0;border-radius:8px;background:#5577cc;color:white;font-weight:700;cursor:pointer}
    .error{padding:10px 12px;border-radius:8px;background:#ffe3e7;color:#8d2435}
    a{color:#4160ad}footer{margin-top:22px;color:#667085;font-size:13px}
  </style>
</head>
<body><main>
  <h1>__TITLE__</h1>
  <p>__DESCRIPTION__</p>
  __ERROR__
  <form method="post" action="/account/__ACTION__">
    __REGISTER_FIELDS__
    <label for="username">Username</label>
    <input id="username" name="username" autocomplete="username" minlength="3" maxlength="30" required>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="__AUTOCOMPLETE__" minlength="10" maxlength="128" required>
    <button type="submit">__BUTTON__</button>
  </form>
  <p>__SWITCH__</p>
  <p><a href="/">Back to dashboard</a></p>
  <footer>Written and created by <strong>Denis Emoru Epur Disii</strong></footer>
</main></body></html>"""


PORTAL_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Student Portal</title>
  <style>
    body{margin:0;background:#f4f6fb;color:#17233c;font:16px Segoe UI,Arial,sans-serif}
    main{max-width:1000px;margin:32px auto;padding:24px}
    .top,.grid{display:flex;justify-content:space-between;align-items:center;gap:14px}
    .grid{align-items:stretch;margin:20px 0}
    .card{flex:1;padding:20px;background:white;border:1px solid #e8ecf3;border-radius:14px}
    .muted{color:#667085}.score{font-size:26px;font-weight:700}
    table{width:100%;border-collapse:collapse;background:white}
    th,td{padding:12px;border-bottom:1px solid #e8ecf3;text-align:left}
    th{color:#667085;font-size:12px;text-transform:uppercase}
    a{color:#4160ad;font-weight:700;text-decoration:none}
    button{border:0;border-radius:8px;padding:10px 14px;background:#5577cc;color:white;font-weight:700;cursor:pointer}
    .signout{margin:0}.risk{font-weight:700}
    .profile{display:flex;align-items:center;gap:14px}
    .profile img,.avatar{width:72px;height:72px;border-radius:50%;object-fit:cover;background:#e8ecf3}
    .profile-form{margin:18px 0}.profile-form input{margin:8px 0}
    .error{padding:10px 12px;border-radius:8px;background:#ffe3e7;color:#8d2435}
    @media(max-width:650px){main{padding:14px}.top,.grid{display:block}.grid .card{margin:10px 0}}
  </style>
</head>
<body><main>
  <header class="top">
    <div class="profile">__PROFILE_IMAGE__<div><h1>Student Portal</h1><p class="muted">Welcome, __STUDENT_NAME__</p></div></div>
    <form class="signout" method="post" action="/logout"><button type="submit">Sign out</button></form>
  </header>
  __ERROR__
  <form class="profile-form" method="post" action="/portal/profile-picture" enctype="multipart/form-data">
    <label for="profile_picture">Profile picture (PNG or JPEG, max 5 MB)</label>
    <input id="profile_picture" name="profile_picture" type="file" accept="image/png,image/jpeg" required>
    <button type="submit">Upload picture</button>
  </form>
  <div class="grid">
    <section class="card"><span class="muted">Student ID</span><div class="score">__STUDENT_ID__</div></section>
    <section class="card"><span class="muted">Average score</span><div class="score">__AVERAGE__%</div><span>Grade __GRADE__</span></section>
    <section class="card"><span class="muted">Attendance</span><div class="score">__ATTENDANCE__%</div><span class="risk">__RISK__</span></section>
  </div>
  <section class="card"><h2>Your subject scores</h2>
    <table><thead><tr><th>Math</th><th>Science</th><th>English</th><th>Study hours/day</th></tr></thead>
    <tbody><tr><td>__MATH__%</td><td>__SCIENCE__%</td><td>__ENGLISH__%</td><td>__STUDY_HOURS__</td></tr></tbody></table>
  </section>
  <section class="card" style="margin-top:20px"><h2>Learning materials</h2>
    <p class="muted">Study resources for CATs, assignments, exams, and general revision.</p>
    <table><thead><tr><th>Material</th><th>Subject</th><th>Purpose</th><th>File size</th><th></th></tr></thead>
    <tbody>__MATERIAL_ROWS__</tbody></table>
  </section>
  <p class="muted">Written and created by Denis Emoru Epur Disii</p>
</main></body></html>"""


def render_account_page(mode, error=""):
    registering = mode == "register"
    if registering:
        title = "Create your student account"
        description = "Use the student ID and full name shown on the class roster to connect your account to your own grades."
        action = "register"
        button = "Create account"
        autocomplete = "new-password"
        register_fields = (
            '<label for="student_id">Student ID</label>'
            '<input id="student_id" name="student_id" autocomplete="off" required>'
            '<label for="student_name">Full name on the roster</label>'
            '<input id="student_name" name="student_name" autocomplete="name" required>'
        )
        switch = 'Already have an account? <a href="/account?mode=login">Sign in</a>'
    else:
        title = "Student sign in"
        description = "Sign in to view your grades and learning materials."
        action = "login"
        button = "Sign in"
        autocomplete = "current-password"
        register_fields = ""
        switch = 'New student? <a href="/account?mode=register">Create an account</a>'
    page = ACCOUNT_PAGE
    replacements = {
        "__TITLE__": title,
        "__DESCRIPTION__": description,
        "__ERROR__": f'<p class="error">{esc(error)}</p>' if error else "",
        "__ACTION__": action,
        "__REGISTER_FIELDS__": register_fields,
        "__AUTOCOMPLETE__": autocomplete,
        "__BUTTON__": button,
        "__SWITCH__": switch,
    }
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    return page


def render_student_portal(student, error=""):
    picture = get_profile_picture(student["account_id"])
    profile_image = (
        '<img src="/profile-picture" alt="Your profile picture">'
        if picture
        else '<div class="avatar" aria-hidden="true"></div>'
    )
    replacements = {
        "__PROFILE_IMAGE__": profile_image,
        "__ERROR__": f'<p class="error">{esc(error)}</p>' if error else "",
        "__STUDENT_NAME__": esc(student["student_name"]),
        "__STUDENT_ID__": esc(student["student_id"]),
        "__AVERAGE__": f'{student["average_score"]:.1f}',
        "__GRADE__": esc(student["grade"]),
        "__ATTENDANCE__": f'{student["attendance"]:.1f}',
        "__RISK__": esc(student["risk_level"]),
        "__MATH__": f'{student["math"]:.1f}',
        "__SCIENCE__": f'{student["science"]:.1f}',
        "__ENGLISH__": f'{student["english"]:.1f}',
        "__STUDY_HOURS__": f'{student["study_hours"]:.1f}',
        "__MATERIAL_ROWS__": material_table_rows(get_learning_materials()),
    }
    page = PORTAL_PAGE
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    return page


class DashboardHandler(BaseHTTPRequestHandler):
    def send_html(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def session_token(self):
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return None
        morsel = cookie.get("student_session")
        return morsel.value if morsel else None

    def redirect(self, location, token=None, clear_session=False):
        self.send_response(303)
        if token:
            self.send_header(
                "Set-Cookie",
                f"student_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_LIFETIME}",
            )
        elif clear_session:
            self.send_header(
                "Set-Cookie",
                "student_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
            )
        self.send_header("Location", location)
        self.end_headers()

    def read_form(self):
        content_length = int(self.headers.get("Content-Length", 0))
        return parse_qs(self.rfile.read(content_length).decode("utf-8"), keep_blank_values=True)

    def page(self, error=""):
        query = parse_qs(urlparse(self.path).query)
        grade_filter = query.get("grade", ["All"])[0]
        risk_filter = query.get("risk", ["All"])[0]

        all_students = get_students()
        filtered = all_students
        if grade_filter != "All":
            filtered = [student for student in filtered if student["grade"] == grade_filter]
        if risk_filter != "All":
            filtered = [student for student in filtered if student["risk_level"] == risk_filter]

        grade_options = "".join(
            f'<option{" selected" if value == grade_filter else ""}>{esc(value)}</option>'
            for value in ["All"] + sorted({student["grade"] for student in all_students})
        )
        risk_options = "".join(
            f'<option{" selected" if value == risk_filter else ""}>{esc(value)}</option>'
            for value in ["All", "On Track", "Needs Attention", "At Risk"]
        )
        at_risk = sum(student["risk_level"] == "At Risk" for student in filtered)

        page = PAGE_TEMPLATE
        replacements = {
            "__ERROR__": f'<div class="error">{esc(error)}</div>' if error else "",
            "__GRADE_OPTIONS__": grade_options,
            "__RISK_OPTIONS__": risk_options,
            "__STUDENT_COUNT__": str(len(filtered)),
            "__AVERAGE_SCORE__": f"{average(filtered, 'average_score'):.1f}",
            "__AVERAGE_ATTENDANCE__": f"{average(filtered, 'attendance'):.1f}",
            "__AT_RISK_COUNT__": str(at_risk),
            "__SUBJECT_BARS__": subject_bars(filtered),
            "__RISK_BARS__": risk_bars(filtered),
            "__TABLE_ROWS__": table_rows(filtered),
            "__MATERIAL_ROWS__": material_table_rows(get_learning_materials()),
            "__GRADE_PARAM__": esc(grade_filter),
            "__RISK_PARAM__": esc(risk_filter),
        }
        for marker, value in replacements.items():
            page = page.replace(marker, value)
        self.send_html(page)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.page()
            return
        if parsed.path == "/account":
            query = parse_qs(parsed.query)
            mode = "register" if query.get("mode", ["login"])[0] == "register" else "login"
            self.send_html(render_account_page(mode))
            return
        if parsed.path == "/portal":
            student = get_account_for_session(self.session_token())
            if not student:
                self.redirect("/account?mode=login")
                return
            self.send_html(render_student_portal(student))
            return
        if parsed.path == "/profile-picture":
            student = get_account_for_session(self.session_token())
            if not student:
                self.send_html("Sign in to view this profile picture.", 401)
                return
            picture = get_profile_picture(student["account_id"])
            if not picture:
                self.send_html("Profile picture not found.", 404)
                return
            path, content_type = picture
            with open(path, "rb") as picture_file:
                data = picture_file.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path.startswith("/materials/download/"):
            try:
                material_id = int(parsed.path.rsplit("/", 1)[1])
            except ValueError:
                self.send_html("Not found", 404)
                return
            material = get_learning_material(material_id)
            if not material:
                self.send_html("Learning material not found", 404)
                return
            stored_path = os.path.join(MATERIALS_DIR, material["stored_name"])
            if not os.path.isfile(stored_path):
                self.send_html("Learning material file not found", 404)
                return
            with open(stored_path, "rb") as material_file:
                data = material_file.read()
            content_type = mimetypes.guess_type(material["original_name"])[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"{material['original_name'].replace(chr(34), '')}\"",
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/download":
            query = parse_qs(parsed.query)
            grade_filter = query.get("grade", ["All"])[0]
            risk_filter = query.get("risk", ["All"])[0]
            students = get_students()
            if grade_filter != "All":
                students = [student for student in students if student["grade"] == grade_filter]
            if risk_filter != "All":
                students = [student for student in students if student["risk_level"] == risk_filter]

            output = io.StringIO()
            fields = ["student_id", "student_name", "attendance", "math", "science", "english", "study_hours", "average_score", "grade", "risk_level"]
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(students)
            data = output.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=student_performance.csv")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_html("Not found", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        if parsed.path == "/account/register":
            form = self.read_form()
            values = {key: items[0] for key, items in form.items()}
            try:
                account_id = register_account(
                    values.get("student_id", ""),
                    values.get("student_name", ""),
                    values.get("username", ""),
                    values.get("password", ""),
                )
                self.redirect("/portal", token=create_session(account_id))
            except ValueError as exc:
                self.send_html(render_account_page("register", str(exc)))
            return

        if parsed.path == "/account/login":
            form = self.read_form()
            username = form.get("username", [""])[0]
            password = form.get("password", [""])[0]
            account_id = authenticate_account(username, password)
            if not account_id:
                self.send_html(render_account_page("login", "Username or password is incorrect."))
                return
            self.redirect("/portal", token=create_session(account_id))
            return

        if parsed.path == "/logout":
            delete_session(self.session_token())
            self.redirect("/", clear_session=True)
            return

        if parsed.path == "/portal/profile-picture":
            student = get_account_for_session(self.session_token())
            if not student:
                self.redirect("/account?mode=login")
                return
            if content_length > MAX_PROFILE_PICTURE_SIZE + 64 * 1024:
                self.send_html(render_student_portal(student, "The profile picture must be no larger than 5 MB."), 413)
                return
            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(content_length)
            if "multipart/form-data" not in content_type:
                self.send_html(render_student_portal(student, "Choose a PNG or JPEG image."))
                return
            try:
                _, uploaded_file = parse_multipart(content_type, body)
                if not uploaded_file:
                    raise ValueError("Choose a profile picture to upload.")
                original_name, content = uploaded_file
                save_profile_picture(student["account_id"], original_name, content)
                self.redirect("/portal")
            except ValueError as exc:
                self.send_html(render_student_portal(student, str(exc)))
            return

        if parsed.path == "/add":
            form = self.read_form()
            row = {key: values[0] for key, values in form.items()}
            try:
                add_student(row)
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
            except (ValueError, sqlite3.IntegrityError) as exc:
                self.page(str(exc))
            return

        if parsed.path == "/upload":
            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(content_length)
            marker = b"\r\n\r\n"
            if marker not in body:
                self.page("The uploaded CSV could not be read.")
                return
            file_part = body.split(marker, 1)[1].rsplit(b"\r\n--", 1)[0]
            try:
                replace_students(parse_csv(file_part.decode("utf-8-sig")))
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
            except (UnicodeDecodeError, ValueError, sqlite3.IntegrityError) as exc:
                self.page(str(exc))
            return

        if parsed.path == "/materials/upload":
            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(content_length)
            if "multipart/form-data" not in content_type:
                self.page("The learning material upload must use a file field.")
                return
            try:
                fields, uploaded_file = parse_multipart(content_type, body)
                if not uploaded_file:
                    raise ValueError("Choose a learning material file.")
                original_name, file_content = uploaded_file
                add_learning_material(
                    fields.get("title", ""),
                    fields.get("subject", ""),
                    fields.get("category", "General study"),
                    original_name,
                    file_content,
                )
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
            except ValueError as exc:
                self.page(str(exc))
            return

        self.send_html("Not found", 404)

    def log_message(self, format, *args):
        print("%s - %s" % (self.address_string(), format % args))


if __name__ == "__main__":
    initialize_database()
    port = int(os.environ.get("PORT", "8501"))
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Student Performance Dashboard running at http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
