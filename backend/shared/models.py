from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import date, datetime

# --- IN/OUT 基本 ---
class VehicleIn(BaseModel):
    auction_no: Optional[str] = None
    maker: Optional[str] = None
    car_name: Optional[str] = None
    grade: Optional[str] = None
    model_code: Optional[str] = None
    year: Optional[int] = None
    mileage_km: Optional[int] = None
    color: Optional[str] = None
    shift: Optional[str] = None
    inspection_until: Optional[str] = None
    score: Optional[str] = None
    start_price_yen: Optional[int] = None
    raw_extracted_json: Optional[Dict[str, Any]] = None

class AuctionSheetIn(BaseModel):
    file_name: str
    auction_name: Optional[str] = None
    auction_date: Optional[date] = None
    vehicles: List[VehicleIn]

class VehicleOut(VehicleIn):
    id: int

class AuctionSheetOut(BaseModel):
    id: int
    file_name: str
    auction_name: Optional[str] = None
    auction_date: Optional[date] = None
    uploaded_at: datetime
    vehicles: List[VehicleOut]

# --- Valuation ---
class AnalyzeParams(BaseModel):
    market: Optional[Dict[str, Any]] = None
    reuse_bonus: Optional[int] = 0
    safety_ratio: Optional[float] = 0.75
    algo_version: Optional[str] = "v0.1-scrap-only"

class ValuationOut(BaseModel):
    id: int
    vehicle_id: int
    algo_version: str
    recommended_bid_yen: Optional[int] = None
    resource_value_yen: Optional[int] = None
    component_value_yen: Optional[int] = None
    assumptions_json: Optional[Dict[str, Any]] = None
    created_at: datetime
