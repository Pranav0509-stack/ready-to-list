from core.data import DB_PATH, DEMO_DAY, build_db, connect
from core.readiness import run_nudges


def ensure_db(force=False):
    fresh = force or not DB_PATH.exists()
    build_db(force=force)
    conn = connect()
    if fresh:
        run_nudges(conn, DEMO_DAY)
    return conn
