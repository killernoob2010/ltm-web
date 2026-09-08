import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / 'backend'))
from app import db, trading_collector_service as service
from app import trading_collector_replication as relay
from test_trading_collector_service import payload, activate

@pytest.fixture
def databases(tmp_path, monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('ORDER_FINANCE_SNAPSHOT_SHARED_SECRET', 'test-only-replication-secret')
    monkeypatch.setenv('LTM_RUNTIME_ENVIRONMENT', 'staging')
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'source.db')
    db.init_db()
    device=activate(1)
    service.ingest_observations(device['token'], [payload()])
    batch=relay.export_page('fills', 0)
    monkeypatch.setenv('LTM_RUNTIME_ENVIRONMENT', 'production')
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'follower.db')
    db.init_db()
    return batch

def test_replay_preserves_one_fact_and_receipt(databases):
    assert relay.apply_page('fills', databases)['imported']==1
    assert relay.apply_page('fills', databases)['imported']==0
    with db.connect() as conn:
        assert conn.execute('SELECT count(*) FROM trading_intraday_fills').fetchone()[0]==1
        assert conn.execute('SELECT count(*) FROM trading_collector_replica_receipts').fetchone()[0]==1
        assert conn.execute('SELECT environment FROM trading_collector_devices').fetchone()[0]=='production'

def test_invalid_environment_and_changed_receipt_fail_closed(databases):
    bad={**databases,'environment':'production'}
    with pytest.raises(ValueError):relay.apply_page('fills',bad)
    relay.apply_page('fills',databases)
    databases['records'][0]['payload']['price']='77'
    databases['records'][0]['digest']=relay.digest(databases['records'][0]['payload'])
    with pytest.raises(ValueError):relay.apply_page('fills',databases)

def test_failure_does_not_advance_cursor(databases,monkeypatch):
    def fail(*args,**kwargs):raise RuntimeError('simulated interrupted ingest')
    monkeypatch.setattr(service,'ingest_observations',fail)
    with pytest.raises(RuntimeError):relay.apply_page('fills',databases)
    with db.connect() as conn:
        assert conn.execute('SELECT count(*) FROM trading_collector_replica_receipts').fetchone()[0]==0

def test_source_guard_and_authorization(databases,monkeypatch):
    with pytest.raises(Exception):relay.export_page('fills',0)
    with pytest.raises(Exception):relay.require_secret(None)

def test_positions_empty_is_not_successful_position_capture(databases,monkeypatch):
    monkeypatch.setenv('LTM_RUNTIME_ENVIRONMENT','staging')
    result=relay.export_page('positions',0)
    assert result['records']==[]
