from pathlib import Path
import sys
from unittest.mock import Mock
import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
from app import main

class Deadlock(Exception):
    pgcode = "40P01"

def test_retries_transaction_conflict_then_completes(monkeypatch):
    initialize = Mock(side_effect=[Deadlock(), None])
    monkeypatch.setattr(main.db, "init_db", initialize)
    monkeypatch.setattr(main.time, "sleep", Mock())
    main.initialize_release_database()
    assert initialize.call_count == 2

def test_does_not_hide_permanent_schema_error(monkeypatch):
    initialize = Mock(side_effect=ValueError("invalid schema"))
    monkeypatch.setattr(main.db, "init_db", initialize)
    with pytest.raises(ValueError):
        main.initialize_release_database()
    assert initialize.call_count == 1

def test_conflict_retries_are_bounded(monkeypatch):
    initialize = Mock(side_effect=Deadlock())
    monkeypatch.setattr(main.db, "init_db", initialize)
    monkeypatch.setattr(main.time, "sleep", Mock())
    with pytest.raises(Deadlock):
        main.initialize_release_database()
    assert initialize.call_count == 5
