import os,time,json,logging
from pathlib import Path
from redis import Redis
from sqlalchemy import select
from src.config.database import Session
from src.models.roll import RollRun
from src.services import rolls
ARTIFACT_DIR=Path('/app/artifacts')

def process(run_id):
    """处理一个判优任务；返回最终状态。"""
    with Session() as db:
        status=rolls.compute(db,run_id)
        run=db.get(RollRun,run_id)
        if run and run.result is not None:
            try:
                ARTIFACT_DIR.mkdir(exist_ok=True)
                (ARTIFACT_DIR/f'roll-run-{run_id}.json').write_text(json.dumps(run.result,ensure_ascii=False,allow_nan=False))
            except OSError: logging.exception('artifact write failed')
        return status

def recover(redis):
    with Session() as db:
        pending=list(db.scalars(select(RollRun).where(RollRun.status.in_(['queued','running']))))
    for run in pending: redis.rpush('roll-queue',run.id)
    return len(pending)

def main():
    redis=Redis.from_url(os.environ['REDIS_URL'])
    # 整卷判优专用 worker：精确枚举限时10秒，超时由服务层记为拒绝判优。
    recover(redis)
    while True:
        Path('/tmp/roll-worker-heartbeat').touch()
        job=redis.blpop('roll-queue',timeout=2)
        if not job: continue
        try: process(int(job[1]))
        except Exception: logging.exception('roll job failed')

if __name__=='__main__': main()
