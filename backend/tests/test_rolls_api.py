import threading

import pytest
from fastapi.testclient import TestClient

from src.config.database import Base, Session, engine
from src.main import app
from src.middlewares import rate_limit
from src.models.roll import RollBatch, RollRecord, RollRun
from src.models.user import User
from src.services import rolls
from src.utils.security import hash_password, token


class FakeRedis:
    def incr(self, key):
        return 1

    def expire(self, key, seconds):
        return True

    def rpush(self, key, value):
        return 1


@pytest.fixture(scope='module')
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    rate_limit.redis = FakeRedis()
    rolls.enqueue = lambda run_id: None
    with Session() as db:
        db.add_all([
            User(email='analyst@demo.local', role='analyst',
                 password_hash=hash_password('Research2026!')),
            User(email='viewer@demo.local', role='viewer',
                 password_hash=hash_password('Research2026!')),
        ])
        db.commit()
    with TestClient(app) as test_client:
        yield test_client


def auth(client, email):
    with Session() as db:
        user = db.query(User).filter_by(email=email).one()
        return {'Authorization': 'Bearer ' + token(user)}


def make_batch(client, name):
    response = client.post('/rolls/batches', json={'name': name, 'note': '测试'},
                           headers=auth(client, 'analyst@demo.local'))
    assert response.status_code == 200, response.text
    return response.json()['id']


def post_record(client, batch_id, shift, roll_no, process, qualified, pieces=100, version=None):
    body = {'shift': shift, 'roll_no': roll_no, 'process': process,
            'qualified': qualified, 'pieces': pieces}
    if version is not None:
        body['expected_version'] = version
    return client.post(f'/rolls/batches/{batch_id}/records', json=body,
                       headers=auth(client, 'analyst@demo.local'))


def test_frozen_version_dedupe_and_compute(client):
    batch_id = make_batch(client, 'API-整卷-A')
    assert post_record(client, batch_id, '早班', 'R1', 'A', 70).status_code == 200
    assert post_record(client, batch_id, '早班', 'R2', 'B', 85).status_code == 200
    conflict = post_record(client, batch_id, '早班', 'R1', 'B', 80)
    assert conflict.status_code == 409 and '卷号重复' in conflict.text

    listing = client.get(f'/rolls/batches/{batch_id}/records',
                         headers=auth(client, 'analyst@demo.local')).json()
    assert listing[0]['roll_no'] == 'R1'

    run = client.post('/rolls/runs', json={'batch_id': batch_id, 'name': '整卷核算一',
                                           'parameters': {'alpha': 0.05}},
                      headers=auth(client, 'analyst@demo.local'))
    assert run.status_code == 200, run.text
    frozen = run.json()
    assert frozen['included_rolls'] == 2
    assert frozen['batch_version'] == 2

    # 冻结后补录不改写已冻结快照
    assert post_record(client, batch_id, '晚班', 'R3', 'A', 60).status_code == 200
    assert post_record(client, batch_id, '晚班', 'R4', 'B', 90).status_code == 200
    again = client.get(f"/rolls/runs/{frozen['id']}",
                       headers=auth(client, 'analyst@demo.local')).json()
    assert len(again['snapshot']) == 2 and again['checksum'] == frozen['checksum']


def test_backfill_with_stale_version_rejected(client):
    batch_id = make_batch(client, 'API-整卷-B')
    assert post_record(client, batch_id, '早班', 'R1', 'A', 70).status_code == 200
    stale = post_record(client, batch_id, '早班', 'R2', 'B', 80, version=0)
    assert stale.status_code == 409 and '版本已变化' in stale.text
    current = post_record(client, batch_id, '早班', 'R2', 'B', 80, version=1)
    assert current.status_code == 200


def test_backfill_colliding_with_freeze_rejected(client):
    """冻结消耗版本号：冻结前读到的版本号再补录会被拒绝，避免误以为已纳入。"""
    batch_id = make_batch(client, 'API-整卷-G')
    headers = auth(client, 'analyst@demo.local')
    assert post_record(client, batch_id, '早班', 'R1', 'A', 70).status_code == 200
    assert post_record(client, batch_id, '早班', 'R2', 'B', 80).status_code == 200
    batches = client.get('/rolls/batches', headers=headers).json()
    stale_version = [b for b in batches if b['id'] == batch_id][0]['version']
    run = client.post('/rolls/runs', json={'batch_id': batch_id, 'name': '撞冻结核算'},
                      headers=headers)
    assert run.status_code == 200 and run.json()['batch_version'] == stale_version
    collided = post_record(client, batch_id, '早班', 'R3', 'B', 90, version=stale_version)
    assert collided.status_code == 409 and '版本已变化' in collided.text
    # 重读版本后补录成功，但不会改写已冻结运行
    batches = client.get('/rolls/batches', headers=headers).json()
    current = [b for b in batches if b['id'] == batch_id][0]['version']
    assert current == stale_version + 1
    assert post_record(client, batch_id, '早班', 'R3', 'B', 90, version=current).status_code == 200
    frozen = client.get(f"/rolls/runs/{run.json()['id']}", headers=headers).json()
    assert frozen['included_rolls'] == 2 and frozen['batch_version'] == stale_version


def test_shift_without_both_groups_rejected(client):
    batch_id = make_batch(client, 'API-整卷-C')
    assert post_record(client, batch_id, '早班', 'R1', 'A', 70).status_code == 200
    assert post_record(client, batch_id, '早班', 'R2', 'B', 80).status_code == 200
    assert post_record(client, batch_id, '晚班', 'R3', 'A', 70).status_code == 200
    response = client.post('/rolls/runs', json={'batch_id': batch_id, 'name': '缺组核算'},
                           headers=auth(client, 'analyst@demo.local'))
    assert response.status_code == 422 and '班次 晚班 缺少A组或B组' in response.text


def test_mixed_piece_counts_rejected_at_entry(client):
    batch_id = make_batch(client, 'API-整卷-D')
    assert post_record(client, batch_id, '早班', 'R1', 'A', 70, pieces=100).status_code == 200
    response = post_record(client, batch_id, '早班', 'R2', 'B', 80, pieces=90)
    assert response.status_code == 422 and '试封件数必须相同' in response.text


def test_viewer_cannot_write(client):
    batch_id = make_batch(client, 'API-整卷-E')
    response = client.post(f'/rolls/batches/{batch_id}/records',
                           json={'shift': '早班', 'roll_no': 'R1', 'process': 'A',
                                 'qualified': 1, 'pieces': 10},
                           headers=auth(client, 'viewer@demo.local'))
    assert response.status_code == 403


def test_concurrent_backfill_only_one_wins(client):
    """同一冻结版本下并发补录同一卷号：只允许一条成功，另一条以重复拒绝。"""
    batch_id = make_batch(client, 'API-整卷-F')
    assert post_record(client, batch_id, '早班', 'R1', 'A', 70).status_code == 200
    session = Session()
    batch = session.get(RollBatch, batch_id)
    version = batch.version
    session.close()

    results = []

    def worker(process):
        local = Session()
        try:
            from src.schemas.roll import RollRecordInput
            data = RollRecordInput(shift='早班', roll_no='R2', process=process,
                                   qualified=80, pieces=100, expected_version=version)
            user = local.query(User).filter_by(email='analyst@demo.local').one()
            row = rolls.save_record(local, batch_id, data, user)
            results.append(('ok', row['id']))
        except Exception as exc:  # HTTPException
            results.append(('error', getattr(exc, 'status_code', type(exc).__name__)))
        finally:
            local.close()

    threads = [threading.Thread(target=worker, args=(p,)) for p in ('B', 'A')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for kind, _ in results if kind == 'ok') == 1
    assert any(kind == 'error' and status == 409 for kind, status in results)
    with Session() as db:
        assert db.query(RollRecord).filter_by(batch_id=batch_id, roll_no='R2').count() == 1
