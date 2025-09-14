from pydantic import BaseModel, Field
from typing import Optional

class LeadIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    phone: str = Field(..., min_length=5, max_length=64)
    age: Optional[str] = Field(default=None, max_length=16)
    comment: Optional[str] = Field(default=None, max_length=600)
    parent_id: Optional[int] = None
    source: Optional[str] = "site"
    ref_code: Optional[str] = ""

class LeadOut(BaseModel):
    ok: bool
    id: int