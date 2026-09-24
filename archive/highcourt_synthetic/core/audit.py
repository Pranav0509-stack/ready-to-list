from datetime import datetime


def log(conn, service, case_id, decision, reason, overridden_by=None):
    """Every service writes here: what it decided, why, and whether a human overrode it."""
    conn.execute("INSERT INTO audit_log VALUES (?,?,?,?,?,?)",
                 (datetime.now().isoformat(timespec="seconds"), service, case_id, decision, reason,
                  overridden_by))
