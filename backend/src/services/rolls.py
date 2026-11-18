import os
import threading

from fastapi import HTTPException
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.models.roll import RollBatch, RollRecord, RollRun
from src.services import roll_algorithm
from src.services.audits import log
from src.utils.serializers import checksum, now, record

_BATCH_LOCKS = {}
_BATCH_LOCKS_GUARD = threading.Lock()


def _batch_lock(batch_id):
    """同一进程内串行化同批次的补录与冻结，数据库约束再兜底跨进程。

    补录查重和运行冻结都必须基于同一个批次版本，锁内读取版本号，避免
    两个并发补录各自“没查到重复”后同时写入。
    """
    with _BATCH_LOCKS_GUARD:
        return _BATCH_LOCKS.setdefault(int(batch_id), threading.Lock())


def enqueue(run_id):
    Redis.from_url(os.environ['REDIS_URL']).rpush('roll-queue', run_id)


def list_batches(db):
    data = []
    for batch in db.scalars(select(RollBatch).order_by(RollBatch.id)):
        item = record(batch)
        item['roll_count'] = db.scalar(
            select(func.count()).select_from(RollRecord).where(RollRecord.batch_id == batch.id))
        data.append(item)
    return data


def require(db, batch_id):
    batch = db.get(RollBatch, batch_id)
    if not batch:
        raise HTTPException(404, '整卷批次不存在')
    return batch


def _locked_batch(db, batch_id):
    """取批次行并加锁；MySQL 下跨进程串行化补录与冻结，SQLite 忽略 FOR UPDATE。

    先结束请求前的只读事务，丢弃旧读视图（登录校验已读过用户表）。否则在
    MySQL 可重复读下，加锁读拿到新版本号而整卷明细仍来自旧快照，冻结的
    卷集与标注版本会错位。
    """
    db.rollback()
    batch = db.scalar(select(RollBatch).where(RollBatch.id == batch_id).with_for_update())
    if not batch:
        raise HTTPException(404, '整卷批次不存在')
    return batch


def create_batch(db, data, user):
    if db.scalar(select(RollBatch).where(RollBatch.name == data.name)):
        raise HTTPException(409, '批次名称已存在')
    batch = RollBatch(name=data.name, note=data.note, version=0, created_at=now())
    db.add(batch)
    log(db, user, 'roll_batch.create', data.name)
    db.commit()
    return record(batch)


def records(db, batch_id):
    require(db, batch_id)
    return [record(x) for x in db.scalars(
        select(RollRecord).where(RollRecord.batch_id == batch_id)
        .order_by(RollRecord.shift, RollRecord.roll_no, RollRecord.id))]


def _clean_record(db, batch_id, data, row_id=None):
    shift = str(data.shift).strip()
    roll_no = str(data.roll_no).strip()
    process = str(data.process).strip().upper()
    if not shift:
        raise HTTPException(422, '班次不能为空')
    if not roll_no:
        raise HTTPException(422, '卷号不能为空')
    if process not in ('A', 'B'):
        raise HTTPException(422, '工艺必须为A或B')
    qualified = data.qualified
    pieces = data.pieces
    for value, label in ((qualified, '合格件数'), (pieces, '试封件数')):
        if isinstance(value, bool) or not isinstance(value, int):
            raise HTTPException(422, label + '必须是整数')
    if not 1 <= pieces <= roll_algorithm.MAX_PIECES:
        raise HTTPException(422, '每卷试封件数必须在1到%d之间' % roll_algorithm.MAX_PIECES)
    if not 0 <= qualified <= pieces:
        raise HTTPException(422, '合格件数必须在0到试封件数之间')
    query = select(RollRecord).where(RollRecord.batch_id == batch_id)
    if row_id is not None:
        query = query.where(RollRecord.id != row_id)
    existing = list(db.scalars(query))
    if any(row.roll_no == roll_no for row in existing):
        raise HTTPException(409, '卷号重复：%s' % roll_no)
    if any(row.pieces != pieces for row in existing):
        raise HTTPException(422, '各卷试封件数必须相同；本批已按 %d 件登记'
                            % existing[0].pieces)
    if len(existing) >= roll_algorithm.MAX_ROLLS and row_id is None:
        raise HTTPException(422, '单批最多%d卷' % roll_algorithm.MAX_ROLLS)
    return {'shift': shift, 'roll_no': roll_no, 'process': process,
            'qualified': qualified, 'pieces': pieces}


def _check_version(batch, expected_version):
    if expected_version is not None and int(expected_version) != int(batch.version):
        raise HTTPException(409, '批次版本已变化（当前 v%d），请刷新后补录，避免与冻结快照错位'
                            % batch.version)


def save_record(db, batch_id, data, user, row_id=None):
    lock = _batch_lock(batch_id)
    with lock:
        batch = _locked_batch(db, batch_id)
        _check_version(batch, data.expected_version)
        values = _clean_record(db, batch_id, data, row_id)
        if row_id is not None:
            row = db.get(RollRecord, row_id)
            if not row or row.batch_id != batch_id:
                raise HTTPException(404, '卷记录不存在')
            for key, value in values.items():
                setattr(row, key, value)
            action = 'roll_record.update'
        else:
            row = RollRecord(batch_id=batch_id, created_at=now(), **values)
            db.add(row)
            action = 'roll_record.create'
        batch.version = int(batch.version) + 1
        log(db, user, action, '%d/%s' % (batch_id, values['roll_no']))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, '卷号重复：%s' % values['roll_no'])
        return record(row)


def delete_record(db, batch_id, row_id, user, expected_version=None):
    lock = _batch_lock(batch_id)
    with lock:
        batch = _locked_batch(db, batch_id)
        _check_version(batch, expected_version)
        row = db.get(RollRecord, row_id)
        if not row or row.batch_id != batch_id:
            raise HTTPException(404, '卷记录不存在')
        db.delete(row)
        batch.version = int(batch.version) + 1
        log(db, user, 'roll_record.delete', '%d/%s' % (batch_id, row.roll_no))
        db.commit()
        return {'deleted': True}


def _snapshot(db, batch_id):
    rows = list(db.scalars(
        select(RollRecord).where(RollRecord.batch_id == batch_id)
        .order_by(RollRecord.shift, RollRecord.roll_no, RollRecord.id)))
    return [{'shift': x.shift, 'roll_no': x.roll_no, 'process': x.process,
             'qualified': x.qualified, 'pieces': x.pieces} for x in rows]


def create_run(db, data, user):
    """冻结同一批次版本下的全部整卷与参数；之后补录不会改写本运行。

    冻结本身消耗一个版本号：并发补录若携带冻结前的版本会被拒绝并要求重读，
    从而不会误以为自己的补录已进入本次冻结。运行记录保存的是实际冻结内容
    的版本，纳入卷数与快照一一对应。
    """
    lock = _batch_lock(data.batch_id)
    with lock:
        batch = _locked_batch(db, data.batch_id)
        snapshot = _snapshot(db, batch.id)
        if not snapshot:
            raise HTTPException(422, '先录入至少一卷试封记录')
        try:
            roll_algorithm.validate_snapshot(snapshot)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

        try:
            params = roll_algorithm.validate_parameters(data.parameters)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc))
        frozen_version = int(batch.version)
        batch.version = frozen_version + 1
        run = RollRun(batch_id=batch.id, name=data.name, parameters=params,
                      snapshot=snapshot, checksum=checksum(snapshot),
                      batch_version=frozen_version, included_rolls=len(snapshot),
                      status='queued', created_at=now())
        db.add(run)
        db.flush()
        log(db, user, 'roll_run.queue', '%d@v%d' % (run.id, frozen_version))
        db.commit()
    enqueue(run.id)
    return record(run)


def get(db, run_id):
    run = db.get(RollRun, run_id)
    if not run:
        raise HTTPException(404, '运行不存在')
    item = record(run)
    item['included_rolls'] = run.included_rolls
    return item


def list_runs(db):
    return [get(db, x) for x in db.scalars(select(RollRun.id).order_by(RollRun.id.desc()))]


def compute(db, run_id):
    """worker 调用：在冻结快照上计算；超时或结构不合格记为拒绝判优。"""
    run = db.get(RollRun, run_id)
    if not run or run.status in ('completed', 'failed', 'refused'):
        return None
    run.status = 'running'
    db.commit()
    try:
        if checksum(run.snapshot) != run.checksum:
            raise ValueError('输入快照校验失败')
        alpha = float(run.parameters.get('alpha', 0.05))
        result = roll_algorithm.permutation_test(run.snapshot, alpha)
        result['input_sha256'] = run.checksum
        result['batch_version'] = run.batch_version
        run.result = result
        run.status = 'completed'
    except roll_algorithm.Refused as exc:
        run.status = 'refused'
        run.error = str(exc)
    except ValueError as exc:
        run.status = 'refused'
        run.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - 未预期错误保留为失败，便于排查
        run.status = 'failed'
        run.error = str(exc)
    db.commit()
    return run.status
