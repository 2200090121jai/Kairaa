import sqlite3, hashlib, hmac, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kairaa.db")


def _conn():
    c = sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS users("
        "username TEXT PRIMARY KEY, salt BLOB, pw BLOB, role TEXT, plan TEXT DEFAULT 'free')"
    )
    return c


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)


def create_user(username: str, password: str, role: str):
    salt = os.urandom(16)
    c = _conn()
    with c:
        c.execute(
            "INSERT INTO users(username, salt, pw, role) VALUES(?,?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET salt=excluded.salt, pw=excluded.pw, role=excluded.role",
            (username.lower(), salt, _hash(password, salt), role),
        )
    c.close()


def verify(username: str, password: str):
    """Returns the role if login is correct, else None."""
    c = _conn()
    row = c.execute("SELECT salt, pw, role FROM users WHERE username=?", (username.lower(),)).fetchone()
    c.close()
    if not row:
        _hash(password, b"0" * 16)  # keeps timing similar
        return None
    return row[2] if hmac.compare_digest(_hash(password, row[0]), row[1]) else None
