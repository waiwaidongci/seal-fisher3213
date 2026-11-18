import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix='roll-tests-')
os.environ.setdefault('DATABASE_URL', 'sqlite+pysqlite:///' + os.path.join(_tmp, 'test.db'))
os.environ.setdefault('REDIS_URL', 'redis://localhost:6399/15')
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.pop('SEED_DEMO', None)
