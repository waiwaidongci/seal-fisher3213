from datetime import datetime, timezone

import pytest
from pathlib import Path

from src.config.database import Base, Session, engine
from src.models.roll import RollBatch, RollRecord, RollRun
from src.services import roll_algorithm, rolls


@pytest.fixture(scope='module', autouse=True)
def schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


def make_run(name, records, parameters=None):
    with Session() as db:
        batch = RollBatch(name=name, note='', version=len(records),
                          created_at=datetime.now(timezone.utc).isoformat())
        db.add(batch)
        db.flush()
        for record in records:
            db.add(RollRecord(batch_id=batch.id, created_at=datetime.now(timezone.utc).isoformat(),
                              **record))
        db.flush()
        from src.utils.serializers import checksum
        snapshot = [{'shift': r['shift'], 'roll_no': r['roll_no'], 'process': r['process'],
                     'qualified': r['qualified'], 'pieces': r['pieces']} for r in records]
        run = RollRun(batch_id=batch.id, name=name, status='queued',
                      parameters=parameters or {'alpha': 0.05},
                      snapshot=snapshot, checksum=checksum(snapshot),
                      batch_version=batch.version, included_rolls=len(records),
                      created_at=datetime.now(timezone.utc).isoformat())
        db.add(run)
        db.commit()
        return run.id


def rec(shift, roll_no, process, qualified, pieces=100):
    return {'shift': shift, 'roll_no': roll_no, 'process': process,
            'qualified': qualified, 'pieces': pieces}


def test_compute_completes_with_included_rolls():
    run_id = make_run('计算完成', [
        rec('早班', 'R1', 'A', 70), rec('早班', 'R2', 'B', 90),
        rec('晚班', 'R3', 'A', 60), rec('晚班', 'R4', 'B', 95),
    ])
    with Session() as db:
        assert rolls.compute(db, run_id) == 'completed'
        run = db.get(RollRun, run_id)
        assert run.result['included_rolls'] == 4
        assert 0 <= run.result['metrics']['单侧p值'] <= 1
        assert run.result['input_sha256'] == run.checksum


def test_compute_refuses_shift_without_control_group():
    run_id = make_run('缺组拒绝', [
        rec('早班', 'R1', 'A', 70), rec('早班', 'R2', 'B', 90), rec('晚班', 'R3', 'A', 60),
    ])
    with Session() as db:
        assert rolls.compute(db, run_id) == 'refused'
        run = db.get(RollRun, run_id)
        assert '晚班 缺少' in run.error
        assert run.result is None


def test_compute_refuses_on_timeout(monkeypatch):
    monkeypatch.setattr(roll_algorithm, 'TIME_LIMIT_SECONDS', 10.0)
    records = [rec('早班', f'R{i}', 'B' if i < 20 else 'A', 50000, 100000) for i in range(40)]
    run_id = make_run('超时拒绝', records)
    monkeypatch.setattr(roll_algorithm, 'permutation_test',
                        lambda *a, **k: (_ for _ in ()).throw(roll_algorithm.Refused('超过10秒判优时限')))
    with Session() as db:
        assert rolls.compute(db, run_id) == 'refused'
        assert '超过10秒' in db.get(RollRun, run_id).error


def test_worker_process_and_recover(monkeypatch):
    import src.roll_worker as worker
    run_id = make_run('工人处理', [
        rec('早班', 'R1', 'A', 70), rec('早班', 'R2', 'B', 90),
    ])

    class FakeRedis:
        def __init__(self): self.queue = []
        def rpush(self, key, value): self.queue.append((key, value))
        def blpop(self, key, timeout=0): return (key, self.queue.pop(0)) if self.queue else None

    fake = FakeRedis()
    assert worker.recover(fake) == 1
    monkeypatch.setattr(worker, 'ARTIFACT_DIR', Path('/tmp/roll-artifacts-test'))
    assert worker.process(run_id) == 'completed'
    with Session() as db:
        assert db.get(RollRun, run_id).result['included_rolls'] == 2
