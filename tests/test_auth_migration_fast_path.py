import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import db


def test_already_migrated_postgres_auth_does_not_take_schema_locks(monkeypatch):
    statements = []
    class Cursor:
        def execute(self, sql, *args):
            statements.append(sql)
        def fetchone(self):
            return {'ready': True}
        def fetchall(self):
            return [{'column_name': 'can_sensitive'}]
    class Connection:
        def cursor(self):
            return Cursor()
        def commit(self):
            pass
    monkeypatch.setattr(db, '_is_pg', lambda: True)
    db.migrate_auth_schema(Connection())
    assert not any('ALTER TABLE' in sql for sql in statements)
    assert any("department = '管理部门'" in sql for sql in statements)
