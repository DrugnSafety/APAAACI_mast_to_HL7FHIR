"""Store Service — 사용자·세션·상담 대화·메일 발송 기록과 언어별 산출물을 SQLite 에 보관한다.

DB 는 두 개다.
  - 본 DB(APP_DB_PATH)      : users / sessions / session_allergens / chat_messages / email_deliveries
  - 산출물 DB(I18N_DB_PATH) : localized_outputs — 세션 id + 언어(ko/en/zh)별 리포트·카드뉴스

세션 id 하나가 입력한 검사결과, 만들어진 산출물, 상담 대화를 묶는다. 이메일을 주지 않은 요청은
user_id 가 없는 익명 세션으로 남는다.

건강정보를 담으므로
  - DB 파일은 git 에 올리지 않는다(.gitignore 의 data/store/).
  - 이 모듈은 결과 내용을 로그에 남기지 않는다(세션 id·건수만).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config.settings import settings

logger = logging.getLogger(__name__)

LANGS = ("ko", "en", "zh")
OUTPUT_FIELDS = ("report_markdown", "report_html", "report_document_html", "cardnews_html")
# 산출물 상태: pending(대기) → running(생성 중) → ready | failed | skipped(번역 엔진 없음)
STATUSES = ("pending", "running", "ready", "failed", "skipped")
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")

_MAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'ko',
    ui TEXT NOT NULL DEFAULT 'quest',
    test_type TEXT,
    patient_name TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    positive_count INTEGER NOT NULL DEFAULT 0,
    has_outputs INTEGER NOT NULL DEFAULT 0,
    input_json TEXT NOT NULL,
    summary_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at);
CREATE TABLE IF NOT EXISTS session_allergens (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    allergen_name TEXT NOT NULL,
    korean_name TEXT,
    category TEXT,
    relevance TEXT,
    class_value TEXT
);
CREATE INDEX IF NOT EXISTS idx_sa_session ON session_allergens(session_id);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    lang TEXT,
    source TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id, id);
CREATE TABLE IF NOT EXISTS email_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    to_email TEXT NOT NULL,
    lang TEXT,
    status TEXT NOT NULL,
    error TEXT,
    attachments TEXT,
    created_at TEXT NOT NULL,
    ip_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_email_session ON email_deliveries(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_email_to ON email_deliveries(to_email, created_at);
CREATE INDEX IF NOT EXISTS idx_email_created ON email_deliveries(created_at);
CREATE TABLE IF NOT EXISTS app_secrets (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_revoked_tokens (
    jti TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL
);
"""
# 이미 만들어진 DB 에 나중에 추가된 열: (테이블, 열, 정의)
_MAIN_MIGRATIONS = (("email_deliveries", "ip_hash", "TEXT"),)
# 발송 예약(pending)이 이 시간보다 오래되면 죽은 요청으로 보고 한도·수신자 고정 계산에서 뺀다
EMAIL_PENDING_STALE = timedelta(minutes=10)
# 세션 하나에 쌓을 수 있는 상담 메시지 수(질문·답변 각각 1건)
CHAT_MAX_MESSAGES_PER_SESSION = 400

_I18N_SCHEMA = """
CREATE TABLE IF NOT EXISTS localized_outputs (
    session_id TEXT NOT NULL,
    lang TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    report_markdown TEXT,
    report_html TEXT,
    report_document_html TEXT,
    cardnews_html TEXT,
    assessments_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, lang)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_email(email: Optional[str]) -> Optional[str]:
    """유효하면 소문자로 정리한 주소, 아니면 None.

    앞뒤 공백(스페이스)만 떼어 준다. 줄바꿈·탭 같은 제어 문자가 어디에든 있으면 거절한다 —
    예전에는 strip() 이 끝의 줄바꿈을 지워 "a@b.co\\n" 이 통과했다."""
    if not isinstance(email, str):
        return None
    e = email.strip(" ")
    if not e or len(e) > 254 or any(ch.isspace() or not ch.isprintable() for ch in e):
        return None
    if not EMAIL_RE.fullmatch(e) or len(e.rsplit("@", 1)[0]) > 64:
        return None
    return e.lower()


def session_tag(session_id: Optional[str]) -> str:
    """로그에 남길 세션 표식. 세션 id 는 그 자체가 접근 권한이라 로그에는 짧은 해시만 적는다."""
    if not session_id:
        return "-"
    return hashlib.sha256(f"session:{session_id}".encode("utf-8")).hexdigest()[:10]


def hash_ip(ip: Optional[str]) -> str:
    """IP 를 그대로 저장하지 않기 위한 짧은 해시(한도 계산용 식별자)."""
    return hashlib.sha256(f"ip:{ip or 'unknown'}".encode("utf-8")).hexdigest()[:24]


def _loads(text: Optional[str], default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return default


def _create_private(path: Path) -> None:
    """DB 파일(과 이미 있는 -wal/-shm)을 소유자만 읽고 쓰게(0600) 만든다. 없으면 빈 파일로 만든다."""
    try:
        os.close(os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600))
    except OSError:
        pass
    for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


# 이 프로세스 안의 sqlite3_open 과 sqlite3_close 를 한 줄로 세운다(모든 Store·모든 DB 공통).
# SQLite 호출을 감싸는 파이썬 잠금은 이것 하나뿐이고, 이 잠금을 쥔 채 다른 파이썬 잠금을 잡지 않는다.
_open_close_gate = threading.Lock()
POOL_MAX_IDLE = 8               # DB 하나에 쉬는 연결을 이만큼까지 남겨 둔다
BUSY_TIMEOUT_SEC = 15           # 다른 연결(다른 프로세스 포함)이 쓰는 중이면 이만큼 기다린다
WAL_SIZE_LIMIT_BYTES = 32 * 1024 * 1024    # 체크포인트 뒤 -wal 파일을 이 크기로 줄인다(연결을 안 닫으니 저절로 지워지지 않는다)


class _ConnectionPool:
    """DB 파일 하나의 연결 풀. 연결을 호출마다 열고 닫지 않고 돌려 쓴다.

    왜: 예전에는 Store 의 모든 메서드가 connect → 쿼리 → close 를 했다. WAL 모드에서 마지막 연결을
    닫으면 SQLite 가 DB 파일에 EXCLUSIVE 잠금을 걸어 체크포인트하고 -wal/-shm 을 지우는데,
    SQLite 3.51.0·3.51.1 은 그 경로(sqlite3WalClose → unixLock → unixIsSharingShmNode)에서
    inode 잠금(pLockMutex)을 쥔 채 VFS 전역 잠금(unixBigLock)을 잡는다. 같은 파일을 여는 다른 스레드
    (unixOpen → findReusableFd)는 반대 순서(전역 → inode)로 잡으므로, 요청 스레드의 close 와
    백그라운드 번역 스레드의 connect 가 겹치면 프로세스가 영영 멈춘다(3.51.2 에서 고쳐짐).

    그래서 (1) 연결을 풀에 두어 서비스 중에는 닫는 일이 거의 없게 하고, (2) 그래도 일어나는
    열기·닫기는 _open_close_gate 로 서로 겹치지 않게 한다. 연결은 한 번에 한 스레드만 쓴다
    (빌린 스레드가 반납할 때까지 풀에 없다) — check_same_thread=False 는 스레드를 옮겨 다닐 수 있다는 뜻일 뿐이다.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()   # _idle·_closed 만 지킨다. 쥔 채로 SQLite 를 부르지 않는다
        self._idle: List[sqlite3.Connection] = []
        self._closed = False

    def _open(self) -> sqlite3.Connection:
        with _open_close_gate:
            conn = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT_SEC, check_same_thread=False)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute(f"PRAGMA journal_size_limit={WAL_SIZE_LIMIT_BYTES}")
            except BaseException:
                conn.close()
                raise
            return conn

    @staticmethod
    def _close(conn: sqlite3.Connection) -> None:
        with _open_close_gate:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def acquire(self) -> sqlite3.Connection:
        with self._lock:
            if self._closed:
                raise sqlite3.ProgrammingError("store is closed")
            if self._idle:
                return self._idle.pop()
        return self._open()

    def release(self, conn: sqlite3.Connection, reusable: bool) -> None:
        with self._lock:
            if reusable and not self._closed and len(self._idle) < POOL_MAX_IDLE:
                self._idle.append(conn)
                return
        self._close(conn)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, []
        for conn in idle:
            self._close(conn)


class Store:
    def __init__(self, main_path: Path, i18n_path: Path):
        self.main_path = Path(main_path)
        self.i18n_path = Path(i18n_path)
        for p in (self.main_path, self.i18n_path):
            p.parent.mkdir(parents=True, exist_ok=True)
            _create_private(p)          # 연결을 열기 전에 0600 — SQLite 는 -wal/-shm 을 DB 파일 권한대로 만든다
        self._pools = {p: _ConnectionPool(p) for p in {self.main_path, self.i18n_path}}
        with self._main() as c:
            c.executescript(_MAIN_SCHEMA)
            for table, column, decl in _MAIN_MIGRATIONS:
                if column not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        with self._i18n() as c:
            c.executescript(_I18N_SCHEMA)
        for p in (self.main_path, self.i18n_path):
            _create_private(p)          # 건강정보 — 소유자만 읽고 쓴다(-wal/-shm 포함)

    def close(self) -> None:
        """풀의 연결을 모두 닫는다(쓰는 중인 연결은 반납될 때 닫힌다). 여러 번 불러도 된다."""
        for pool in getattr(self, "_pools", {}).values():
            pool.close()

    def __del__(self):
        try:
            self.close()
        except Exception:  # noqa: BLE001 — 인터프리터 종료 중
            pass

    # ---------------- 연결 ----------------
    @contextmanager
    def _conn(self, path: Path):
        """풀에서 연결 하나를 빌려 한 트랜잭션을 돌리고 돌려준다. 호출마다 열고 닫지 않는다
        (이유는 _ConnectionPool 설명 참고). 빌린 동안 그 연결은 이 스레드만 쓴다."""
        pool = self._pools[path]
        conn = pool.acquire()
        reusable = False
        try:
            yield conn
            conn.commit()
            reusable = True
        except BaseException:
            try:
                conn.rollback()
                reusable = not conn.in_transaction
            except sqlite3.Error:
                pass                    # 되돌리지 못한 연결은 풀에 돌려놓지 않고 닫는다
            raise
        finally:
            pool.release(conn, reusable)

    def _main(self):
        return self._conn(self.main_path)

    def _i18n(self):
        return self._conn(self.i18n_path)

    # ---------------- 사용자 ----------------
    @staticmethod
    def _upsert_user(c: sqlite3.Connection, email: str) -> int:
        ts = now_iso()
        c.execute("INSERT INTO users(email, created_at, last_seen_at) VALUES(?,?,?) "
                  "ON CONFLICT(email) DO UPDATE SET last_seen_at=excluded.last_seen_at", (email, ts, ts))
        return c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]

    def attach_user(self, session_id: str, email: str) -> str:
        """익명 세션을 이메일 사용자에 연결한다. 이미 연결된 세션의 주소는 바꾸지 않는다.

        돌려주는 값: "bound"(새로 연결했거나 같은 주소로 이미 연결됨) · "conflict"(다른 주소에 연결돼 있음)
        · "not_found"(세션 없음)."""
        with self._main() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT u.email FROM sessions s LEFT JOIN users u ON u.id=s.user_id"
                            " WHERE s.id=?", (session_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["email"]:
                return "bound" if row["email"] == email else "conflict"
            uid = self._upsert_user(c, email)
            c.execute("UPDATE sessions SET user_id=?, updated_at=? WHERE id=?", (uid, now_iso(), session_id))
            return "bound"

    def session_email(self, session_id: str) -> Optional[str]:
        with self._main() as c:
            row = c.execute("SELECT u.email FROM sessions s LEFT JOIN users u ON u.id=s.user_id"
                            " WHERE s.id=?", (session_id,)).fetchone()
        return row["email"] if row else None

    # ---------------- 세션 ----------------
    def session_exists(self, session_id: Optional[str]) -> bool:
        if not session_id:
            return False
        with self._main() as c:
            return c.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() is not None

    def save_session(self, *, session_id: Optional[str], email: Optional[str], lang: str, ui: str,
                     input_data: Dict[str, Any], summary: Optional[Dict[str, Any]] = None,
                     assessments: Optional[List[Dict[str, Any]]] = None) -> str:
        """세션을 만들거나(없는 id·빈 id) 같은 id 로 다시 저장한다. 세션 id 를 돌려준다.

        이미 이메일에 연결된 세션은 email 을 줘도 연결을 바꾸지 않는다(세션 id 를 아는 제3자가
        남의 세션을 자기 주소로 옮기지 못하게). assessments 가 None 이면 산출물 없음으로 표시하고
        항원 목록은 건드리지 않는다."""
        ocr = input_data.get("ocr") or {}
        patient = ocr.get("patient") or {}
        results = ocr.get("results") or []
        ts = now_iso()
        with self._main() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT user_id, has_outputs FROM sessions WHERE id=?",
                            (session_id,)).fetchone() if session_id else None
            uid = row["user_id"] if row is not None and row["user_id"] else (
                self._upsert_user(c, email) if email else None)
            if row is None:
                session_id = uuid.uuid4().hex
                c.execute(
                    "INSERT INTO sessions(id, user_id, created_at, updated_at, lang, ui, test_type, patient_name,"
                    " result_count, positive_count, has_outputs, input_json, summary_json)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (session_id, uid, ts, ts, lang, ui, ocr.get("test_type"), patient.get("name"),
                     len(results), len(assessments or []), 1 if assessments is not None else 0,
                     json.dumps(input_data, ensure_ascii=False),
                     json.dumps(summary, ensure_ascii=False) if summary is not None else None))
            elif assessments is not None:
                c.execute(
                    "UPDATE sessions SET user_id=?, updated_at=?, lang=?, ui=?, test_type=?, patient_name=?,"
                    " result_count=?, positive_count=?, has_outputs=1, input_json=?, summary_json=? WHERE id=?",
                    (uid, ts, lang, ui, ocr.get("test_type"), patient.get("name"),
                     len(results), len(assessments), json.dumps(input_data, ensure_ascii=False),
                     json.dumps(summary, ensure_ascii=False), session_id))
            elif uid and not row["user_id"]:
                c.execute("UPDATE sessions SET user_id=?, updated_at=? WHERE id=?", (uid, ts, session_id))
            if assessments is not None:
                c.execute("DELETE FROM session_allergens WHERE session_id=?", (session_id,))
                c.executemany(
                    "INSERT INTO session_allergens(session_id, allergen_name, korean_name, category, relevance,"
                    " class_value) VALUES(?,?,?,?,?,?)",
                    [(session_id, a.get("allergen_name") or "", a.get("korean_name"), a.get("category"),
                      a.get("relevance"), None if a.get("class_value") is None else str(a.get("class_value")))
                     for a in assessments])
        return session_id

    @staticmethod
    def _session_row(r: sqlite3.Row, full: bool = False) -> Dict[str, Any]:
        d = {k: r[k] for k in ("id", "user_id", "created_at", "updated_at", "lang", "ui", "test_type",
                               "patient_name", "result_count", "positive_count")}
        d["has_outputs"] = bool(r["has_outputs"])
        if "email" in r.keys():
            d["email"] = r["email"]
        if full:
            d["input"] = _loads(r["input_json"], {})
            d["summary"] = _loads(r["summary_json"])
        return d

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._main() as c:
            r = c.execute("SELECT s.*, u.email FROM sessions s LEFT JOIN users u ON u.id=s.user_id"
                          " WHERE s.id=?", (session_id,)).fetchone()
            if not r:
                return None
            d = self._session_row(r, full=True)
            d["allergens"] = [dict(x) for x in c.execute(
                "SELECT allergen_name, korean_name, category, relevance, class_value"
                " FROM session_allergens WHERE session_id=?", (session_id,))]
            return d

    def delete_session(self, session_id: str) -> bool:
        with self._main() as c:
            n = c.execute("DELETE FROM sessions WHERE id=?", (session_id,)).rowcount
            c.execute("DELETE FROM email_deliveries WHERE session_id=?", (session_id,))
        with self._i18n() as c:
            c.execute("DELETE FROM localized_outputs WHERE session_id=?", (session_id,))
        return n > 0

    # ---------------- 상담 대화 ----------------
    def add_chat(self, session_id: str, role: str, content: str, lang: Optional[str] = None,
                 source: Optional[str] = None) -> None:
        with self._main() as c:
            c.execute("INSERT INTO chat_messages(session_id, role, content, lang, source, created_at)"
                      " VALUES(?,?,?,?,?,?)", (session_id, role, content, lang, source, now_iso()))

    def add_chat_turn(self, session_id: str, question: str, answer: str, lang: Optional[str] = None,
                      source: Optional[str] = None) -> str:
        """질문과 답변을 한 번에(같은 트랜잭션) 남긴다.
        돌려주는 값: "ok" · "not_found"(세션 없음) · "full"(세션당 메시지 한도 초과)."""
        with self._main() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                return "not_found"
            n = c.execute("SELECT COUNT(*) n FROM chat_messages WHERE session_id=?",
                          (session_id,)).fetchone()["n"]
            if n + 2 > CHAT_MAX_MESSAGES_PER_SESSION:
                return "full"
            ts = now_iso()
            c.executemany("INSERT INTO chat_messages(session_id, role, content, lang, source, created_at)"
                          " VALUES(?,?,?,?,?,?)",
                          [(session_id, "user", question, lang, None, ts),
                           (session_id, "assistant", answer, lang, source, ts)])
            return "ok"

    def list_chat(self, session_id: str) -> List[Dict[str, Any]]:
        with self._main() as c:
            return [dict(r) for r in c.execute(
                "SELECT id, role, content, lang, source, created_at FROM chat_messages"
                " WHERE session_id=? ORDER BY id", (session_id,))]

    # ---------------- 메일 발송 기록 ----------------
    def reserve_email(self, session_id: str, to_email: str, ip_hash: Optional[str], *,
                      per_session_hour: int, per_recipient_day: int, per_ip_day: int,
                      global_day: int) -> Tuple[Optional[int], Optional[str]]:
        """발송 한도를 확인하고 통과하면 pending 기록을 넣는다. (기록 id, None) 또는 (None, 거절 사유).

        확인과 기록을 한 쓰기 트랜잭션(BEGIN IMMEDIATE)에서 하므로 동시에 들어온 요청이 같은 여유분을
        함께 통과하지 못한다. 한도는 성공·실패·진행 중을 모두 센다(실패를 반복해 우회하지 못하게).
        0 이하인 한도는 끈 것으로 본다.

        거절 사유: session_not_found · recipient_mismatch(이 세션은 다른 주소에 묶여 있음) · rate_limited"""
        now = datetime.now(timezone.utc)
        hour_ago = (now - timedelta(hours=1)).isoformat(timespec="seconds")
        day_ago = (now - timedelta(days=1)).isoformat(timespec="seconds")
        stale = (now - EMAIL_PENDING_STALE).isoformat(timespec="seconds")
        live = "(status!='pending' OR created_at>=?)"
        with self._main() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT u.email FROM sessions s LEFT JOIN users u ON u.id=s.user_id"
                            " WHERE s.id=?", (session_id,)).fetchone()
            if row is None:
                return None, "session_not_found"
            bound = row["email"]
            if not bound:
                prior = c.execute(
                    "SELECT to_email FROM email_deliveries WHERE session_id=? AND (status='sent'"
                    " OR (status='pending' AND created_at>=?)) ORDER BY id LIMIT 1",
                    (session_id, stale)).fetchone()
                bound = prior["to_email"] if prior else None
            if bound and bound != to_email:
                return None, "recipient_mismatch"

            def used(where: str, *args) -> int:
                return c.execute(f"SELECT COUNT(*) n FROM email_deliveries WHERE {where} AND {live}",
                                 (*args, stale)).fetchone()["n"]

            checks = (
                (per_session_hour, "session_id=? AND created_at>=?", (session_id, hour_ago)),
                (per_recipient_day, "to_email=? AND created_at>=?", (to_email, day_ago)),
                (per_ip_day if ip_hash else 0, "ip_hash=? AND created_at>=?", (ip_hash, day_ago)),
                (global_day, "created_at>=?", (day_ago,)),
            )
            for limit, where, args in checks:
                if limit and limit > 0 and used(where, *args) >= limit:
                    return None, "rate_limited"
            cur = c.execute("INSERT INTO email_deliveries(session_id, to_email, lang, status, error,"
                            " attachments, created_at, ip_hash) VALUES(?,?,?,?,?,?,?,?)",
                            (session_id, to_email, None, "pending", None, "[]",
                             now.isoformat(timespec="seconds"), ip_hash))
            return cur.lastrowid, None

    def finish_email(self, delivery_id: int, status: str, *, lang: Optional[str] = None,
                     error: Optional[str] = None, attachments: Optional[List[str]] = None) -> None:
        """예약한 발송 기록을 sent/failed 로 마무리한다."""
        with self._main() as c:
            c.execute("UPDATE email_deliveries SET status=?, lang=?, error=?, attachments=? WHERE id=?",
                      (status, lang, (error or None) and error[:300], json.dumps(attachments or []),
                       delivery_id))

    def list_emails(self, session_id: str) -> List[Dict[str, Any]]:
        with self._main() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT id, to_email, lang, status, error, attachments, created_at FROM email_deliveries"
                " WHERE session_id=? ORDER BY id DESC", (session_id,))]
        for r in rows:
            r["attachments"] = _loads(r["attachments"], [])
        return rows

    # ---------------- 언어별 산출물(별도 DB) ----------------
    def save_localized(self, session_id: str, lang: str, status: str,
                       outputs: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
        """outputs 가 None 이면 상태·오류만 바꾸고 기존 내용은 둔다."""
        ts = now_iso()
        with self._i18n() as c:
            if outputs is None:
                c.execute(
                    "INSERT INTO localized_outputs(session_id, lang, status, error, created_at, updated_at)"
                    " VALUES(?,?,?,?,?,?) ON CONFLICT(session_id, lang) DO UPDATE SET"
                    " status=excluded.status, error=excluded.error, updated_at=excluded.updated_at",
                    (session_id, lang, status, error, ts, ts))
                return
            c.execute(
                "INSERT INTO localized_outputs(session_id, lang, status, error, report_markdown, report_html,"
                " report_document_html, cardnews_html, assessments_json, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id, lang) DO UPDATE SET"
                " status=excluded.status, error=excluded.error, report_markdown=excluded.report_markdown,"
                " report_html=excluded.report_html, report_document_html=excluded.report_document_html,"
                " cardnews_html=excluded.cardnews_html, assessments_json=excluded.assessments_json,"
                " updated_at=excluded.updated_at",
                (session_id, lang, status, error, *[outputs.get(f) for f in OUTPUT_FIELDS],
                 json.dumps(outputs.get("assessments") or [], ensure_ascii=False), ts, ts))

    def localized_status(self, session_id: str) -> Dict[str, Dict[str, Any]]:
        """{lang: {status, error, updated_at}} — 기록이 없는 언어는 빠진다."""
        with self._i18n() as c:
            return {r["lang"]: {"status": r["status"], "error": r["error"], "updated_at": r["updated_at"]}
                    for r in c.execute("SELECT lang, status, error, updated_at FROM localized_outputs"
                                       " WHERE session_id=?", (session_id,))}

    def get_localized(self, session_id: str, lang: str) -> Optional[Dict[str, Any]]:
        with self._i18n() as c:
            r = c.execute("SELECT * FROM localized_outputs WHERE session_id=? AND lang=?",
                          (session_id, lang)).fetchone()
        if not r:
            return None
        d = {k: r[k] for k in ("session_id", "lang", "status", "error", "created_at", "updated_at",
                               *OUTPUT_FIELDS)}
        d["assessments"] = _loads(r["assessments_json"], [])
        return d

    # ---------------- 정리(재시작 복구·보존 기간) ----------------
    def fail_interrupted(self) -> Dict[str, int]:
        """프로세스가 죽으면서 pending/running 에 멈춘 기록을 failed(interrupted) 로 돌린다.
        서버를 띄울 때 한 번 부른다 — 그대로 두면 화면이 끝나지 않는 작업을 계속 기다린다."""
        ts = now_iso()
        with self._i18n() as c:
            langs = c.execute("UPDATE localized_outputs SET status='failed', error='interrupted', updated_at=?"
                              " WHERE status IN ('pending','running')", (ts,)).rowcount
        with self._main() as c:
            mails = c.execute("UPDATE email_deliveries SET status='failed', error='interrupted'"
                              " WHERE status='pending'").rowcount
        return {"localized": langs, "emails": mails}

    def purge_older_than(self, days: int) -> int:
        """마지막 저장이 days 일보다 오래된 세션과 그에 딸린 산출물·대화·발송 기록을 지운다.
        세션이 하나도 남지 않은 사용자도 함께 지운다. 지운 세션 수를 돌려준다."""
        if days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self._main() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM sessions WHERE updated_at<?", (cutoff,))]
        for sid in ids:
            self.delete_session(sid)
        with self._main() as c:
            c.execute("DELETE FROM email_deliveries WHERE created_at<?", (cutoff,))
            c.execute("DELETE FROM users WHERE last_seen_at<? AND NOT EXISTS"
                      " (SELECT 1 FROM sessions s WHERE s.user_id=users.id)", (cutoff,))
        return len(ids)

    # ---------------- 관리자 로그인 보조 ----------------
    def get_or_create_secret(self, name: str) -> str:
        """서버가 만든 난수 비밀값(관리자 토큰 서명 키 등). 처음 부를 때 만들어 DB 에 둔다."""
        with self._main() as c:
            c.execute("INSERT OR IGNORE INTO app_secrets(name, value) VALUES(?,?)",
                      (name, secrets.token_hex(32)))
            return c.execute("SELECT value FROM app_secrets WHERE name=?", (name,)).fetchone()["value"]

    def revoke_admin_token(self, jti: str, expires_at: int) -> None:
        with self._main() as c:
            c.execute("DELETE FROM admin_revoked_tokens WHERE expires_at<?", (int(time.time()),))
            c.execute("INSERT OR IGNORE INTO admin_revoked_tokens(jti, expires_at) VALUES(?,?)",
                      (jti, int(expires_at)))

    def is_admin_token_revoked(self, jti: str) -> bool:
        with self._main() as c:
            return c.execute("SELECT 1 FROM admin_revoked_tokens WHERE jti=?", (jti,)).fetchone() is not None

    # ---------------- 관리자 조회 ----------------
    def list_users(self, q: str = "", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        like = f"%{(q or '').strip().lower()}%"
        with self._main() as c:
            total = c.execute("SELECT COUNT(*) n FROM users WHERE email LIKE ?", (like,)).fetchone()["n"]
            rows = c.execute(
                "SELECT u.id, u.email, u.created_at, u.last_seen_at,"
                " (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id) AS session_count,"
                " (SELECT MAX(s.created_at) FROM sessions s WHERE s.user_id=u.id) AS last_session_at,"
                " (SELECT COUNT(*) FROM chat_messages m JOIN sessions s ON s.id=m.session_id"
                "   WHERE s.user_id=u.id AND m.role='user') AS chat_count,"
                " (SELECT s.lang FROM sessions s WHERE s.user_id=u.id ORDER BY s.created_at DESC LIMIT 1) AS lang"
                " FROM users u WHERE u.email LIKE ? ORDER BY u.last_seen_at DESC LIMIT ? OFFSET ?",
                (like, limit, offset)).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._main() as c:
            u = c.execute("SELECT id, email, created_at, last_seen_at FROM users WHERE id=?",
                          (user_id,)).fetchone()
        if not u:
            return None
        d = dict(u)
        d["sessions"] = self.list_sessions(user_id=user_id, limit=500)["items"]
        return d

    def delete_user(self, user_id: int) -> bool:
        """사용자와 그 세션·대화·산출물·발송 기록을 모두 지운다."""
        with self._main() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM sessions WHERE user_id=?", (user_id,))]
        for sid in ids:
            self.delete_session(sid)
        with self._main() as c:
            return c.execute("DELETE FROM users WHERE id=?", (user_id,)).rowcount > 0

    def list_sessions(self, user_id: Optional[int] = None, anonymous: bool = False, q: str = "",
                      limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        where, args = ["1=1"], []
        if user_id is not None:
            where.append("s.user_id=?")
            args.append(user_id)
        if anonymous:
            where.append("s.user_id IS NULL")
        if (q or "").strip():
            like = f"%{q.strip().lower()}%"
            where.append("(s.id LIKE ? OR LOWER(COALESCE(s.patient_name,'')) LIKE ?"
                         " OR COALESCE(u.email,'') LIKE ?)")
            args += [like, like, like]
        w = " AND ".join(where)
        with self._main() as c:
            total = c.execute(f"SELECT COUNT(*) n FROM sessions s LEFT JOIN users u ON u.id=s.user_id"
                              f" WHERE {w}", args).fetchone()["n"]
            rows = c.execute(
                f"SELECT s.*, u.email,"
                f" (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id=s.id) AS chat_count"
                f" FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE {w}"
                f" ORDER BY s.created_at DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        items = []
        for r in rows:
            d = self._session_row(r)
            d["chat_count"] = r["chat_count"]
            items.append(d)
        statuses = self._localized_status_many([d["id"] for d in items])
        for d in items:
            d["i18n"] = {lang: st for lang, st in statuses.get(d["id"], {}).items()}
        return {"total": total, "items": items}

    def _localized_status_many(self, session_ids: Iterable[str]) -> Dict[str, Dict[str, str]]:
        ids = list(session_ids)
        out: Dict[str, Dict[str, str]] = {}
        if not ids:
            return out
        with self._i18n() as c:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                marks = ",".join("?" * len(chunk))
                for r in c.execute(f"SELECT session_id, lang, status FROM localized_outputs"
                                   f" WHERE session_id IN ({marks})", chunk):
                    out.setdefault(r["session_id"], {})[r["lang"]] = r["status"]
        return out

    def stats(self, days: int = 30, tz_offset_minutes: int = 0) -> Dict[str, Any]:
        """대시보드 집계. 일자 구분은 관리자 브라우저 시간대(tz_offset_minutes) 기준이다."""
        days = max(1, min(int(days), 365))
        tz = timezone(timedelta(minutes=max(-840, min(int(tz_offset_minutes), 840))))
        today = datetime.now(tz).date()
        start_day = today - timedelta(days=days - 1)
        start_utc = datetime.combine(start_day, datetime.min.time(), tz).astimezone(timezone.utc)
        prev_utc = start_utc - timedelta(days=days)
        since, prev_since = (start_utc.isoformat(timespec="seconds"), prev_utc.isoformat(timespec="seconds"))

        def local_day(ts: str) -> str:
            return datetime.fromisoformat(ts).astimezone(tz).date().isoformat()

        with self._main() as c:
            one = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
            totals = {
                "users": one("SELECT COUNT(*) FROM users"),
                "sessions": one("SELECT COUNT(*) FROM sessions"),
                "anonymous_sessions": one("SELECT COUNT(*) FROM sessions WHERE user_id IS NULL"),
                "chat_messages": one("SELECT COUNT(*) FROM chat_messages WHERE role='user'"),
                "emails_sent": one("SELECT COUNT(*) FROM email_deliveries WHERE status='sent'"),
                "emails_failed": one("SELECT COUNT(*) FROM email_deliveries WHERE status='failed'"),
            }
            period = {
                "sessions": one("SELECT COUNT(*) FROM sessions WHERE created_at>=?", since),
                "sessions_prev": one("SELECT COUNT(*) FROM sessions WHERE created_at>=? AND created_at<?",
                                     prev_since, since),
                "new_users": one("SELECT COUNT(*) FROM users WHERE created_at>=?", since),
                "new_users_prev": one("SELECT COUNT(*) FROM users WHERE created_at>=? AND created_at<?",
                                      prev_since, since),
                "chat_messages": one("SELECT COUNT(*) FROM chat_messages WHERE role='user' AND created_at>=?",
                                     since),
                "chat_messages_prev": one("SELECT COUNT(*) FROM chat_messages WHERE role='user'"
                                          " AND created_at>=? AND created_at<?", prev_since, since),
                "emails_sent": one("SELECT COUNT(*) FROM email_deliveries WHERE status='sent'"
                                   " AND created_at>=?", since),
                "emails_sent_prev": one("SELECT COUNT(*) FROM email_deliveries WHERE status='sent'"
                                        " AND created_at>=? AND created_at<?", prev_since, since),
            }
            per_day = Counter(local_day(r["created_at"]) for r in c.execute(
                "SELECT created_at FROM sessions WHERE created_at>=?", (since,)))
            langs = {r["lang"]: r["n"] for r in c.execute(
                "SELECT lang, COUNT(*) n FROM sessions WHERE created_at>=? GROUP BY lang", (since,))}
            top = [dict(r) for r in c.execute(
                "SELECT a.allergen_name, MAX(a.korean_name) AS korean_name, MAX(a.category) AS category,"
                " COUNT(DISTINCT a.session_id) AS sessions,"
                " COUNT(DISTINCT CASE WHEN a.relevance='clinically_relevant' THEN a.session_id END) AS relevant"
                " FROM session_allergens a JOIN sessions s ON s.id=a.session_id WHERE s.created_at>=?"
                " GROUP BY a.allergen_name ORDER BY sessions DESC, a.allergen_name LIMIT 10", (since,))]
            relevance = {r["relevance"] or "not_assessed": r["n"] for r in c.execute(
                "SELECT a.relevance, COUNT(*) n FROM session_allergens a JOIN sessions s ON s.id=a.session_id"
                " WHERE s.created_at>=? GROUP BY a.relevance", (since,))}
        with self._i18n() as c:
            i18n = {s: 0 for s in STATUSES}
            for r in c.execute("SELECT status, COUNT(*) n FROM localized_outputs GROUP BY status"):
                i18n[r["status"]] = r["n"]
        series = [{"date": (start_day + timedelta(days=i)).isoformat(),
                   "sessions": per_day.get((start_day + timedelta(days=i)).isoformat(), 0)}
                  for i in range(days)]
        return {"days": days, "since": since, "totals": totals, "period": period,
                "sessions_per_day": series, "languages": {lang: langs.get(lang, 0) for lang in LANGS},
                "top_allergens": top, "relevance": relevance, "localized_outputs": i18n}


_store: Optional[Store] = None
_store_key: Optional[tuple] = None
_store_lock = threading.Lock()


def _warn_if_sqlite_deadlock_prone() -> None:
    """SQLite 3.51.0·3.51.1 은 같은 프로세스에서 WAL DB 의 열기와 닫기가 겹치면 멈출 수 있다
    (_ConnectionPool 설명). 풀이 그 겹침을 막지만, 라이브러리를 3.51.2 이상으로 올리는 것이 근본 해결이다."""
    if sqlite3.sqlite_version_info[:3] in ((3, 51, 0), (3, 51, 1)):
        logger.warning("SQLite %s 에는 WAL 연결 열기·닫기 교착 버그가 있습니다(3.51.2 에서 수정). "
                       "연결 풀로 우회 중 — libsqlite 를 올리세요.", sqlite3.sqlite_version)


def get_store() -> Optional[Store]:
    """저장이 꺼져 있거나(STORAGE_ENABLED=false) DB 를 열 수 없으면 None — 호출부는 저장 없이 계속 간다."""
    global _store, _store_key
    if not settings.storage_enabled:
        return None
    key = (str(settings.app_db_path), str(settings.i18n_db_path))
    with _store_lock:
        if _store is None or _store_key != key:
            try:
                old, _store = _store, None
                if old is not None:
                    old.close()
                _warn_if_sqlite_deadlock_prone()
                _store = Store(settings.app_db_path, settings.i18n_db_path)
                _store_key = key
            except Exception as e:  # noqa: BLE001
                logger.error(f"저장소를 열 수 없습니다: {type(e).__name__}: {e}")
                return None
        return _store


def close_store() -> None:
    """열려 있는 저장소의 연결을 닫는다(서버 종료 시 — WAL 을 체크포인트하고 -wal/-shm 을 정리한다).
    그 뒤 get_store() 를 부르면 다시 연다."""
    global _store, _store_key
    with _store_lock:
        old, _store, _store_key = _store, None, None
        if old is not None:
            old.close()
