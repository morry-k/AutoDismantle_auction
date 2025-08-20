# 先頭の import に追加
from typing import Optional
from pydantic import ValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from datetime import date
from typing import Optional  # ← 追加

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session
from db import get_db
from models_db import AuctionSheet, Vehicle
from shared.models import AuctionSheetOut, VehicleOut
from services import parser

router = APIRouter()


# 数値の安全化ヘルパー
def _safe_int(val: Optional[int], max_abs: int = 2_000_000_000) -> Optional[int]:
    """
    val が int で現実的な範囲内ならそのまま返す。
    範囲外/不正なら None を返して DB への挿入を安全化。
    デフォは±20億（必要に応じてカラム毎に変える）。
    """
    if val is None:
        return None
    try:
        iv = int(val)
        if -max_abs <= iv <= max_abs:
            return iv
        return None
    except Exception:
        return None


def _to_date_if_needed(val) -> Optional[date]:
    """
    parsed["auction_date"] が str の場合は date に変換して返す。
    None はそのまま。
    """
    if val is None:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val)
        except Exception:
            return None
    return None

@router.post("/upload", response_model=AuctionSheetOut)
async def upload_pdf(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # 1) PDF解析（出品票メタ＋車両リストを得る）
    try:
        content = await file.read()
        parsed = parser.parse_auction_sheet(content, file.filename)
        # parsed は AuctionSheetIn 相当の dict
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {e}")

    # 2) DB保存（出品票）
    sheet = AuctionSheet(
        file_name=parsed["file_name"],
        auction_name=parsed.get("auction_name"),
        auction_date=_to_date_if_needed(parsed.get("auction_date")),  # ← 型変換
    )
    db.add(sheet)
    db.flush()  # sheet.id を得る

    # 3) DB保存（車両複数）
    vouts = []
    for v in parsed.get("vehicles", []):
        vo = Vehicle(
            sheet_id=sheet.id,
            auction_no=_safe_int(v.get("auction_no"), max_abs=9_999_999),  # 1〜7桁程度
            maker=v.get("maker"),
            car_name=v.get("car_name"),
            grade=v.get("grade"),
            model_code=v.get("model_code"),
            year=_safe_int(v.get("year"), max_abs=3000),                   # 〜西暦上限
            mileage_km=_safe_int(v.get("mileage_km"), max_abs=10_000_000), # 1千万km上限
            color=v.get("color"),
            shift=v.get("shift"),
            inspection_until=v.get("inspection_until"),
            score=v.get("score"),
            start_price_yen=_safe_int(v.get("start_price_yen"), max_abs=1_000_000_000),  # 〜10億
            raw_extracted_json=v.get("raw_extracted_json"),
        )

        db.add(vo)
        db.flush()
        vouts.append(VehicleOut(
            id=vo.id, auction_no=vo.auction_no, maker=vo.maker, car_name=vo.car_name,
            grade=vo.grade, model_code=vo.model_code, year=vo.year, mileage_km=vo.mileage_km,
            color=vo.color, shift=vo.shift, inspection_until=vo.inspection_until,
            score=vo.score, start_price_yen=vo.start_price_yen,
            raw_extracted_json=vo.raw_extracted_json
        ))

# 末尾の返却部分の直前に置き換え
    db.commit()

    # ---- ここからデバッグ用に厳密に検証して、失敗したら中身を返す ----
    try:
        payload = AuctionSheetOut(
            id=sheet.id,
            file_name=sheet.file_name,
            auction_name=sheet.auction_name,
            auction_date=sheet.auction_date,
            uploaded_at=sheet.uploaded_at,
            vehicles=vouts,
        )
        return payload  # 通常はこれでOK（FastAPIがJSONにしてくれる）
    except ValidationError as ve:
        # どのフィールドで型不一致かを可視化
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "where": "response_model_validation",
                "errors": ve.errors(),
                # 実際に入れようとしたデータも一緒に返す
                "data": jsonable_encoder({
                    "id": sheet.id,
                    "file_name": sheet.file_name,
                    "auction_name": sheet.auction_name,
                    "auction_date": sheet.auction_date,
                    "uploaded_at": sheet.uploaded_at,
                    "vehicles": [jsonable_encoder(v) for v in vouts],
                })
            }
        )
    return AuctionSheetOut(
        id=sheet.id,
        file_name=sheet.file_name,
        auction_name=sheet.auction_name,
        auction_date=sheet.auction_date,
        uploaded_at=sheet.uploaded_at,
        vehicles=vouts,
    )
