# backend/api/admin.py
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from db import get_db
from models_db import AuctionSheet, Vehicle

router = APIRouter(prefix="/admin", tags=["admin"])

def _esc(s):
    if s is None:
        return ""
    # 超簡易エスケープ + 改行は <br>
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )

_BASE_CSS = """
table{border-collapse:collapse;font-family:system-ui,Segoe UI,Arial; font-size:14px}
th,td{border:1px solid #ddd;padding:6px 8px;vertical-align:top}
th{background:#f6f6f6}
h2{font-family:system-ui,Segoe UI,Arial}
a{color:#1f6feb;text-decoration:none}
"""

@router.get("/sheets", response_class=HTMLResponse)
def list_sheets(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(
            AuctionSheet.id,
            AuctionSheet.file_name,
            AuctionSheet.auction_name,
            AuctionSheet.auction_date,
            AuctionSheet.uploaded_at,
            func.count(Vehicle.id).label("vehicle_count"),
        )
        .outerjoin(Vehicle, Vehicle.sheet_id == AuctionSheet.id)
        .group_by(AuctionSheet.id)
        .order_by(AuctionSheet.id.desc())
        # .limit(limit)
        .all()
    )
    html = [
        "<html><head><meta charset='utf-8'>",
        f"<style>{_BASE_CSS}</style>",
        "</head><body>",
        f"<h2>Auction Sheets (latest {limit})</h2>",
        "<table><tr><th>ID</th><th>File</th><th>Auction</th><th>Date</th><th>Uploaded</th><th>Vehicles</th></tr>"
    ]
    for r in rows:
        html.append(
            "<tr>"
            f"<td>{r.id}</td>"
            f"<td>{_esc(r.file_name)}</td>"
            f"<td>{_esc(r.auction_name)}</td>"
            f"<td>{_esc(r.auction_date)}</td>"
            f"<td>{_esc(r.uploaded_at)}</td>"
            f"<td><a href='/admin/vehicles?sheet_id={r.id}'>{r.vehicle_count}</a></td>"
            "</tr>"
        )
    html.append("</table></body></html>")
    return "".join(html)

@router.get("/vehicles", response_class=HTMLResponse)
def list_vehicles(
    sheet_id: int = Query(..., ge=1),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(
            Vehicle.id,
            Vehicle.sheet_id,
            Vehicle.auction_no,
            Vehicle.maker,
            Vehicle.car_name,
            Vehicle.grade,
            Vehicle.model_code,
            Vehicle.year,
            Vehicle.mileage_km,
            Vehicle.start_price_yen,
            Vehicle.score,
            Vehicle.lane,            # ← 追加
        )
        .filter(Vehicle.sheet_id == sheet_id)
        .order_by(Vehicle.id.asc())
        #.limit(limit)
        .all()
    )
    html = [
        "<html><head><meta charset='utf-8'>",
        f"<style>{_BASE_CSS}</style>",
        "</head><body>",
        f"<h2>Vehicles for Sheet #{sheet_id} (all)</h2>",
        "<p><a href='/admin/sheets'>&laquo; back</a></p>",
        "<table><tr>"
        "<th>ID</th><th>Sheet</th><th>Shuppin_No</th><th>Maker</th><th>Car</th>"
        "<th>Grade</th><th>Nenshiki</th><th>Katashiki</th><th>Haikiryou</th><th>kyori</th><th>Iro</th><th>Shift</th><th>AC</th><th>Soubi</th><th>Score</th><th>Start</th><th>Lane</th>"
        "</tr>"
    ]
    for r in rows:
        html.append(
            "<tr>"
            f"<td>{r.id}</td>"
            f"<td>{r.sheet_id}</td>"
            f"<td>{_esc(r.auction_no)}</td>"
            f"<td>{_esc(r.maker)}</td>"
            f"<td>{_esc(r.car_name)}</td>"
            f"<td>{_esc(r.grade)}</td>"
            f"<td>{_esc(r.year)}</td>"
            f"<td>{_esc(r.model_code)}</td>"
            f"<td>{_esc(r.model_code)}</td>"
            f"<td>{_esc(r.mileage_km)}</td>"
            f"<td>{_esc(r.model_code)}</td>"
            f"<td>{_esc(r.model_code)}</td>"
            f"<td>{_esc(r.model_code)}</td>"
            f"<td>{_esc(r.model_code)}</td>"
            f"<td>{_esc(r.score)}</td>"
            f"<td>{_esc(r.start_price_yen)}</td>"
            f"<td>{_esc(r.lane)}</td>"
            "</tr>"
        )
    html.append("</table></body></html>")
    return "".join(html)
