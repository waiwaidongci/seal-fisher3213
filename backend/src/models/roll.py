from sqlalchemy import Column,Integer,String,Text,JSON,ForeignKey,UniqueConstraint
from src.config.database import Base
class RollBatch(Base):
    __tablename__='roll_batches'
    id=Column(Integer,primary_key=True)
    name=Column(String(120),unique=True,nullable=False)
    note=Column(Text,default='')
    version=Column(Integer,nullable=False,default=0)
    created_at=Column(String(40),nullable=False)
class RollRecord(Base):
    __tablename__='roll_records'
    __table_args__=(UniqueConstraint('batch_id','roll_no'),)
    id=Column(Integer,primary_key=True)
    batch_id=Column(Integer,ForeignKey('roll_batches.id'),nullable=False)
    shift=Column(String(60),nullable=False)
    roll_no=Column(String(100),nullable=False)
    process=Column(String(1),nullable=False)
    qualified=Column(Integer,nullable=False)
    pieces=Column(Integer,nullable=False)
    created_at=Column(String(40),nullable=False)
class RollRun(Base):
    __tablename__='roll_runs'
    id=Column(Integer,primary_key=True)
    batch_id=Column(Integer,ForeignKey('roll_batches.id'),nullable=False)
    name=Column(String(120),nullable=False)
    status=Column(String(20),default='queued')
    parameters=Column(JSON,nullable=False)
    snapshot=Column(JSON,nullable=False)
    checksum=Column(String(64),nullable=False)
    batch_version=Column(Integer,nullable=False)
    included_rolls=Column(Integer,nullable=False)
    result=Column(JSON,nullable=True)
    error=Column(Text,default='')
    created_at=Column(String(40),nullable=False)
