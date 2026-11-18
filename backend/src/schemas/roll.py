from pydantic import BaseModel,Field
class RollBatchInput(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    note:str=Field(default='',max_length=2000)
class RollRecordInput(BaseModel):
    shift:str=Field(min_length=1,max_length=60)
    roll_no:str=Field(min_length=1,max_length=100)
    process:str=Field(min_length=1,max_length=1)
    qualified:int
    pieces:int
    expected_version:int|None=None
class RollRunInput(BaseModel):
    batch_id:int=Field(gt=0)
    name:str=Field(min_length=2,max_length=120)
    parameters:dict=Field(default_factory=dict)
