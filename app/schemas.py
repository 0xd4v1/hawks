from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class HawksTargetBase(BaseModel):
    domain_ip: str

class HawksTargetCreate(HawksTargetBase):
    pass

class HawksTarget(HawksTargetBase):
    id: int
    scan_status: str
    created_at: datetime
    last_scan: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class HawksTemplateBase(BaseModel):
    name: str
    content: str
    enabled: bool = True
    order_index: int = 0

class HawksTemplateCreate(HawksTemplateBase):
    pass

class HawksTemplate(HawksTemplateBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class HawksScanResultBase(BaseModel):
    target_id: int
    scan_type: str
    status: str = "pending"
    result_data: Optional[str] = None
    error_msg: Optional[str] = None

class HawksScanResult(HawksScanResultBase):
    id: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class HawksLoginRequest(BaseModel):
    username: str
    password: str


class HawksSettingsBase(BaseModel):
    chaos_api_key: Optional[str] = None
    chaos_enabled: bool = False

class HawksSettingsCreate(HawksSettingsBase):
    pass

class HawksSettings(HawksSettingsBase):
    id: int

    class Config:
        from_attributes = True
