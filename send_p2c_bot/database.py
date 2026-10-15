import sqlite3
import json
import time
from contextlib import contextmanager
from config import DB_PATH


def init_db():
    """Инициализация базы данных."""
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT NOT NULL UNIQUE,
                cookies TEXT DEFAULT '{}',
                access_token TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                last_balance_check REAL DEFAULT 0,
                last_balance_json TEXT DEFAULT '{}',
                cookies_updated_at REAL DEFAULT 0,
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS p2c_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                status TEXT DEFAULT 'idle',
                qr_link TEXT DEFAULT '',
                qr_sum INTEGER DEFAULT 0,
                qr_bank TEXT DEFAULT '',
                last_qr_generated REAL DEFAULT 0,
                payment_confirmed INTEGER DEFAULT 0,
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );

            CREATE TABLE IF NOT EXISTS p2c_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                account_id INTEGER,
                action TEXT,
                details TEXT DEFAULT '',
                created_at REAL DEFAULT 0
            );
        """)


@contextmanager
def get_db():
    """Контекстный менеджер для БД."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ==========================================
# CRUD для аккаунтов
# ==========================================

def create_account(name: str, phone: str, cookies: dict = None) -> int:
    """Создать аккаунт. Возвращает ID."""
    now = time.time()
    cookies_json = json.dumps(cookies or {})
    access_token = ''

    if cookies:
        for c in (cookies if isinstance(cookies, list) else []):
            if c.get("name") == "access_token":
                access_token = c.get("value", "")
                break

    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO accounts
               (name, phone, cookies, access_token, cookies_updated_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, phone, cookies_json, access_token, now, now, now)
        )
        return cursor.lastrowid


def get_account(account_id: int) -> dict | None:
    """Получить аккаунт по ID."""
    with get_db() as db:
        row = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row:
            return dict(row)
    return None


def get_all_accounts() -> list[dict]:
    """Получить все аккаунты."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM accounts ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_active_accounts() -> list[dict]:
    """Получить активные аккаунты."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM accounts WHERE is_active = 1 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def update_account(account_id: int, **kwargs):
    """Обновить поля аккаунта."""
    kwargs["updated_at"] = time.time()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [account_id]
    with get_db() as db:
        db.execute(f"UPDATE accounts SET {fields} WHERE id = ?", values)


def update_account_cookies(account_id: int, cookies: list):
    """Обновить куки аккаунта."""
    access_token = ''
    for c in cookies:
        if c.get("name") == "access_token":
            access_token = c.get("value", "")
            break

    update_account(
        account_id,
        cookies=json.dumps(cookies),
        access_token=access_token,
        cookies_updated_at=time.time(),
    )


def update_account_balance(account_id: int, balance_json: dict):
    """Обновить баланс аккаунта."""
    update_account(
        account_id,
        last_balance_json=json.dumps(balance_json),
        last_balance_check=time.time(),
    )


def delete_account(account_id: int):
    """Удалить аккаунт."""
    with get_db() as db:
        db.execute("DELETE FROM p2c_sessions WHERE account_id = ?", (account_id,))
        db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def toggle_account(account_id: int) -> bool:
    """Переключить активность. Возвращает новое состояние."""
    acc = get_account(account_id)
    if not acc:
        return False
    new_state = 0 if acc["is_active"] else 1
    update_account(account_id, is_active=new_state)
    return bool(new_state)


# ==========================================
# CRUD для P2C сессий
# ==========================================

def create_p2c_session(account_id: int, qr_link: str = "", qr_sum: int = 0) -> int:
    """Создать P2C сессию."""
    now = time.time()
    with get_db() as db:
        cursor = db.execute(
            """INSERT INTO p2c_sessions
               (account_id, status, qr_link, qr_sum, created_at, updated_at)
               VALUES (?, 'active', ?, ?, ?, ?)""",
            (account_id, qr_link, qr_sum, now, now)
        )
        return cursor.lastrowid


def get_p2c_session(session_id: int) -> dict | None:
    """Получить сессию по ID."""
    with get_db() as db:
        row = db.execute("SELECT * FROM p2c_sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None


def get_active_p2c_sessions(account_id: int = None) -> list[dict]:
    """Получить активные P2C сессии."""
    with get_db() as db:
        if account_id:
            rows = db.execute(
                "SELECT * FROM p2c_sessions WHERE account_id = ? AND status = 'active'",
                (account_id,)
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM p2c_sessions WHERE status = 'active'"
            ).fetchall()
        return [dict(r) for r in rows]


def update_p2c_session(session_id: int, **kwargs):
    """Обновить P2C сессию."""
    kwargs["updated_at"] = time.time()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [session_id]
    with get_db() as db:
        db.execute(f"UPDATE p2c_sessions SET {fields} WHERE id = ?", values)


def stop_p2c_session(session_id: int):
    """Остановить P2C сессию."""
    update_p2c_session(session_id, status="stopped")


def log_p2c_action(session_id: int, account_id: int, action: str, details: str = ""):
    """Записать действие в историю."""
    with get_db() as db:
        db.execute(
            """INSERT INTO p2c_history
               (session_id, account_id, action, details, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, account_id, action, details, time.time())
        )
