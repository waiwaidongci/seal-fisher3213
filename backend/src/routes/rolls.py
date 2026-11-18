from fastapi import APIRouter, Depends, Query
from src.config.database import get_db
from src.middlewares.auth import admin, current_user, writer
from src.schemas.roll import RollBatchInput, RollRecordInput, RollRunInput
from src.services import rolls
router = APIRouter(prefix='/rolls')
@router.get('/batches')
def list_batches(db=Depends(get_db), user=Depends(current_user)): return rolls.list_batches(db)
@router.post('/batches')
def create_batch(data: RollBatchInput, db=Depends(get_db), user=Depends(writer)): return rolls.create_batch(db, data, user)
@router.get('/batches/{id}/records')
def list_records(id: int, db=Depends(get_db), user=Depends(current_user)): return rolls.records(db, id)
@router.post('/batches/{id}/records')
def add_record(id: int, data: RollRecordInput, db=Depends(get_db), user=Depends(writer)): return rolls.save_record(db, id, data, user)
@router.put('/batches/{id}/records/{row_id}')
def update_record(id: int, row_id: int, data: RollRecordInput, db=Depends(get_db), user=Depends(writer)): return rolls.save_record(db, id, data, user, row_id)
@router.delete('/batches/{id}/records/{row_id}')
def delete_record(id: int, row_id: int, expected_version: int | None = Query(default=None), db=Depends(get_db), user=Depends(writer)): return rolls.delete_record(db, id, row_id, user, expected_version)
@router.get('/runs')
def list_runs(db=Depends(get_db), user=Depends(current_user)): return rolls.list_runs(db)
@router.post('/runs')
def create_run(data: RollRunInput, db=Depends(get_db), user=Depends(writer)): return rolls.create_run(db, data, user)
@router.get('/runs/{id}')
def get_run(id: int, db=Depends(get_db), user=Depends(current_user)): return rolls.get(db, id)
