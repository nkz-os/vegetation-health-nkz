from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from uuid import UUID
from datetime import date, datetime

class JobCreateRequest(BaseModel):
    job_type: str = Field(..., description="download or calculate_index")
    entity_id: Optional[str] = None
    entity_type: Optional[str] = "AgriParcel"
    parameters: Dict[str, Any] = Field(default_factory=dict)
    bounds: Optional[Dict[str, Any]] = None
    ha_to_process: Optional[float] = None

class JobResponse(BaseModel):
    id: UUID
    tenant_id: str
    job_type: str
    entity_id: Optional[str] = None
    entity_type: Optional[str] = None
    status: str
    parameters: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LatestResultsItem(BaseModel):
    entity_id: str
    raster_path: Optional[str] = None
    job_id: str
    tile_token: Optional[str] = None
    bounds: Optional[List[float]] = None  # [west, south, east, north]
    minzoom: Optional[int] = None
    maxzoom: Optional[int] = None
    sensing_date: Optional[date] = None
    scene_id: Optional[str] = None
