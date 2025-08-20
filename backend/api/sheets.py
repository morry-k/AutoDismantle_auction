from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from db import get_db
from models_db import AuctionSheet
from shared.models import AuctionSheetOut, VehicleOut

router = APIRouter()

@router.get("/sheets", response_model=list[AuctionSheetOut])
def list_sheets(db: Session = Depends(get_db)):
    sheets = db.query(AuctionSheet).order_by(AuctionSheet.uploaded_at.desc()).all()
    out = []
    for s in sheets:
        vouts = [
            VehicleOut(
                id=v.id, auction_no=v.auction_no, maker=v.maker, car_name=v.car_name,
                grade=v.grade, model_code=v.model_code, year=v.year, mileage_km=v.mileage_km,
                color=v.color, shift=v.shift, inspection_until=v.inspection_until,
                score=v.score, start_price_yen=v.start_price_yen,
                raw_extracted_json=v.raw_extracted_json
            )
            for v in s.vehicles
        ]
        out.append(AuctionSheetOut(
            id=s.id, file_name=s.file_name, auction_name=s.auction_name,
            auction_date=s.auction_date, uploaded_at=s.uploaded_at, vehicles=vouts
        ))
    return out

@router.get("/sheets/{sheet_id}", response_model=AuctionSheetOut)
def get_sheet(sheet_id: int, db: Session = Depends(get_db)):
    s = db.query(AuctionSheet).filter(AuctionSheet.id == sheet_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Sheet not found")
    vouts = [
        VehicleOut(
            id=v.id, auction_no=v.auction_no, maker=v.maker, car_name=v.car_name,
            grade=v.grade, model_code=v.model_code, year=v.year, mileage_km=v.mileage_km,
            color=v.color, shift=v.shift, inspection_until=v.inspection_until,
            score=v.score, start_price_yen=v.start_price_yen,
            raw_extracted_json=v.raw_extracted_json
        )
        for v in s.vehicles
    ]
    return AuctionSheetOut(
        id=s.id, file_name=s.file_name, auction_name=s.auction_name,
        auction_date=s.auction_date, uploaded_at=s.uploaded_at, vehicles=vouts
    )
