"""저장소(SQLite) 동시성 — 요청 스레드와 백그라운드 번역 스레드가 두 DB 를 함께 써도 멈추지 않는다.

배경: Store 가 호출마다 connect/close 하던 때, 실제 uvicorn 이 /api/classify 를 수십 번 받다가
SQLite 안에서 영영 멈췄다(sqlite3WalClose→unixLock 과 unixOpen→findReusableFd 의 잠금 순서 역전,
SQLite 3.51.0·3.51.1). 스트레스는 별도 프로세스에서 돌린다 — 교착이 나면 그 프로세스만 죽이고
시간 제한으로 바로 실패시킨다(같은 프로세스에서 멈추면 SQLite 잠금이 남아 뒤 테스트까지 멈춘다).
"""
import os
import sqlite3
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from services import store_service
from services.store_service import Store

ROOT = Path(__file__).resolve().parent
STRESS_TIMEOUT_SEC = 90

# argv: main_db i18n_db threads iterations
_STRESS_SCRIPT = r"""
import sys, threading
from services.store_service import Store

main_db, i18n_db, threads, iters = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
store = Store(main_db, i18n_db)
errors = []

def request_then_background(i):
    # /api/classify 한 번이 하는 저장 순서 + 응답 뒤 백그라운드 작업(_run_i18n_job)이 하는 저장 순서
    try:
        for n in range(iters):
            sid = store.save_session(session_id=None, email=f"u{i}@example.com" if n % 3 == 0 else None,
                                     lang="ko", ui="quest", input_data={"ocr": {"results": []}},
                                     summary={"n": n}, assessments=[{"allergen_name": "D1"}])
            store.save_localized(sid, "ko", "ready", {"report_markdown": "x" * 4000})
            for lang in ("en", "zh"):
                store.save_localized(sid, lang, "pending")
            store.session_email(sid)
            store.localized_status(sid)
            for lang in ("en", "zh"):
                store.save_localized(sid, lang, "running")
                store.save_localized(sid, lang, "ready", {"report_html": "y" * 4000})
            assert store.add_chat_turn(sid, "q", "a") == "ok"
            assert store.get_session(sid)["id"] == sid
            assert store.get_localized(sid, "en")["status"] == "ready"
            if n % 7 == 0:
                store.delete_session(sid)
    except BaseException as e:
        errors.append(repr(e))

def admin_reads(i):
    try:
        for n in range(iters):
            store.stats()
            store.list_sessions(limit=20)
            store.list_users()
            store.reserve_email("no-such-session", "a@b.co", None, per_session_hour=3,
                                per_recipient_day=10, per_ip_day=20, global_day=500)
    except BaseException as e:
        errors.append(repr(e))

def reopen(i):
    # 같은 파일을 여닫는 다른 Store(설정이 바뀌어 get_store 가 저장소를 다시 여는 경우 등)
    try:
        for n in range(iters):
            other = Store(main_db, i18n_db)
            other.session_exists("x")
            other.localized_status("x")
            other.close()
    except BaseException as e:
        errors.append(repr(e))

workers = [threading.Thread(target=request_then_background, args=(i,), daemon=True) for i in range(threads)]
workers += [threading.Thread(target=admin_reads, args=(i,), daemon=True) for i in range(2)]
workers += [threading.Thread(target=reopen, args=(i,), daemon=True) for i in range(2)]
for t in workers:
    t.start()
for t in workers:
    t.join()
total = store.list_sessions()["total"]
store.close()
print("ERRORS" if errors else "OK", total, errors[:3])
sys.exit(1 if errors else 0)
"""


@pytest.mark.parametrize("threads", [1, 4, 16])
def test_concurrent_store_use_never_deadlocks(tmp_path, threads):
    iters = 60
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-c", _STRESS_SCRIPT, str(tmp_path / "app.sqlite3"),
             str(tmp_path / "localized.sqlite3"), str(threads), str(iters)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=STRESS_TIMEOUT_SEC,
            env={**os.environ, "PYTHONPATH": str(ROOT)})
    except subprocess.TimeoutExpired:
        pytest.fail(f"저장소 스트레스({threads} 스레드)가 {STRESS_TIMEOUT_SEC}초 안에 끝나지 않았다 — 교착")
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    status, total = proc.stdout.split()[-3:-1] if proc.stdout.strip().endswith("[]") else ("?", "0")
    # 7번에 한 번은 지우므로 남는 세션 수가 정해져 있다 — 잃어버린 쓰기가 없다
    assert status == "OK" and int(total) == threads * (iters - len(range(0, iters, 7)))


def test_connections_are_pooled_not_reopened_per_call(tmp_path, monkeypatch):
    opened = []
    real_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        opened.append(args[0])
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(store_service.sqlite3, "connect", counting_connect)
    store = Store(tmp_path / "a.sqlite3", tmp_path / "l.sqlite3")
    for _ in range(50):
        sid = store.save_session(session_id=None, email=None, lang="ko", ui="quest",
                                 input_data={"ocr": {}}, summary={}, assessments=[])
        store.save_localized(sid, "ko", "ready", {})
        store.localized_status(sid)
    assert len(opened) == 2          # DB 하나에 연결 하나 — 호출마다 열지 않는다
    store.close()
    store.close()                    # 두 번 닫아도 된다
    with pytest.raises(sqlite3.ProgrammingError):
        store.session_exists("x")


def test_open_and_close_are_serialized_by_one_gate(tmp_path, monkeypatch):
    """SQLite 의 열기·닫기가 겹치지 않는다는 것이 교착을 막는 불변식이다."""
    inside = []
    overlaps = []
    real_connect = sqlite3.connect

    class Probe:
        def __enter__(self):
            if inside:
                overlaps.append(1)
            inside.append(1)

        def __exit__(self, *exc):
            inside.pop()

    def probed_connect(*args, **kwargs):
        assert store_service._open_close_gate.locked()
        with Probe():
            return real_connect(*args, **kwargs)

    monkeypatch.setattr(store_service.sqlite3, "connect", probed_connect)
    monkeypatch.setattr(store_service, "POOL_MAX_IDLE", 1)      # 반납할 때 닫히는 연결이 생기게
    store = Store(tmp_path / "a.sqlite3", tmp_path / "l.sqlite3")
    closes = []
    real_close = store_service._ConnectionPool._close

    def probed_close(conn):
        closes.append(1)
        return real_close(conn)

    monkeypatch.setattr(store_service._ConnectionPool, "_close", staticmethod(probed_close))

    def work():
        for _ in range(40):
            sid = store.save_session(session_id=None, email=None, lang="ko", ui="quest",
                                     input_data={"ocr": {}}, summary={}, assessments=[])
            store.save_localized(sid, "en", "pending")
            store.get_session(sid)

    threads = [threading.Thread(target=work, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads)
    assert not overlaps and closes       # 닫기도 실제로 일어났고, 열기끼리 겹친 적이 없다
    assert store.list_sessions()["total"] == 8 * 40
    store.close()


def test_failed_transaction_is_rolled_back_and_connection_stays_usable(tmp_path):
    store = Store(tmp_path / "a.sqlite3", tmp_path / "l.sqlite3")
    with pytest.raises(RuntimeError):
        with store._main() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO users(email, created_at, last_seen_at) VALUES('x@y.zz','t','t')")
            raise RuntimeError("boom")
    assert store.list_users()["total"] == 0          # 반쯤 쓴 내용이 다음 호출로 새지 않는다
    with store._main() as c:
        assert not c.in_transaction
    sid = store.save_session(session_id=None, email="a@b.co", lang="ko", ui="quest",
                             input_data={"ocr": {}}, summary={}, assessments=[])
    assert store.session_email(sid) == "a@b.co"
    store.close()


def test_pooled_connection_never_serves_a_stale_snapshot(tmp_path):
    """돌려 쓰는 연결이 예전 읽기 트랜잭션을 붙들고 있으면 다른 연결의 새 쓰기를 못 본다."""
    store = Store(tmp_path / "a.sqlite3", tmp_path / "l.sqlite3")
    with store._main() as first:                     # 연결 두 개를 풀에 만든다
        first.execute("SELECT COUNT(*) FROM sessions").fetchone()
        with store._main() as second:
            second.execute("SELECT COUNT(*) FROM sessions").fetchone()
    seen = []
    for n in range(1, 6):
        store.save_session(session_id=None, email=None, lang="ko", ui="quest",
                           input_data={"ocr": {}}, summary={}, assessments=[])
        with store._main() as a, store._main() as b:  # 두 연결 모두에서 방금 쓴 것이 보여야 한다
            seen.append((n, a.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                         b.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]))
    assert seen == [(n, n, n) for n in range(1, 6)]
    store.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX 파일 권한")
def test_db_wal_and_shm_files_are_owner_only(tmp_path):
    old_umask = os.umask(0o022)
    try:
        store = Store(tmp_path / "a.sqlite3", tmp_path / "l.sqlite3")
        sid = store.save_session(session_id=None, email=None, lang="ko", ui="quest",
                                 input_data={"ocr": {}}, summary={}, assessments=[])
        store.save_localized(sid, "ko", "ready", {})
        files = sorted(p for p in tmp_path.iterdir())
        assert {p.name for p in files} >= {"a.sqlite3", "a.sqlite3-wal", "a.sqlite3-shm",
                                           "l.sqlite3", "l.sqlite3-wal", "l.sqlite3-shm"}
        for p in files:
            assert stat.S_IMODE(p.stat().st_mode) == 0o600, p.name
        with store._main() as c:
            assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        store.close()
    finally:
        os.umask(old_umask)
