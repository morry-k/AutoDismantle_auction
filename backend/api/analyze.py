from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from db import get_db
from models_db import Vehicle, Valuation
from shared.models import AnalyzeParams, ValuationOut
from services.calculator import estimate_resource_value, recommend_bid

router = APIRouter()

@router.post("/vehicles/{vehicle_id}/analyze", response_model=ValuationOut)
def analyze_vehicle(vehicle_id: int, params: AnalyzeParams = AnalyzeParams(), db: Session = Depends(get_db)):
    v = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    vehicle_dict = {
        "car_name": v.car_name,
        "year": v.year,
        "mileage_km": v.mileage_km,
        "raw": v.raw_extracted_json or {},
    }
    resource_value, breakdown = estimate_resource_value(vehicle_dict, params.market or {})

    bid = recommend_bid(resource_value, reuse_bonus=params.reuse_bonus or 0, safety_ratio=params.safety_ratio or 0.75)

    assumptions = {
        "market": params.market or {},
        "breakdown": breakdown,
        "safety_ratio": params.safety_ratio,
        "reuse_bonus": params.reuse_bonus,
    }

    val = Valuation(
        vehicle_id=v.id,
        algo_version=params.algo_version or "v0.1-scrap-only",
        recommended_bid_yen=bid,
        resource_value_yen=resource_value,
        component_value_yen=None,
        assumptions_json=assumptions,
    )
    db.add(val)
    db.flush()
    db.refresh(val)
    db.commit()

    return ValuationOut(
        id=val.id,
        vehicle_id=val.vehicle_id,
        algo_version=val.algo_version,
        recommended_bid_yen=val.recommended_bid_yen,
        resource_value_yen=val.resource_value_yen,
        component_value_yen=val.component_value_yen,
        assumptions_json=val.assumptions_json,
        created_at=val.created_at,
    )
