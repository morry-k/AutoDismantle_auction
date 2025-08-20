# 先頭の import に追加
import re
from typing import Optional, List, Dict, Any
from pydantic import ValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from datetime import date

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session
from db import get_db
from models_db import AuctionSheet, Vehicle
from shared.models import AuctionSheetOut, VehicleOut
from services import parser

router = APIRouter()


# ========= 追加: 改行で複数台を1台ずつに展開するヘルパー =========

# 実改行: \r, \n, Unicode LSEP/PSEP / 文字列の "\r\n" "\n" "\r" の両対応
_SPLIT_NL = re.compile(r"(?:\r\n|\r|\n|\\r\\n|\\n|\\r|\u2028|\u2029)+")

def _split_lines(x: Any) -> List[str]:
    if x is None:
        return []
    s = str(x).strip()
    if not s:
        return []
    parts = [p.strip() for p in _SPLIT_NL.split(s)]
    return [p for p in parts if p]  # 空は除去

def _normalize_score(x: Any):
    if x is None:
        return None
    s = str(x).strip()
    if s == ".5":
        return 0.5
    try:
        return float(s)
    except Exception:
        return s  # 数値化できない評価体系はそのまま

def _expand_by_index(v: Dict[str, Any], multiline_cols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    v の中で改行/文字列改行で連なっているセルを縦に展開。
    - multiline_cols が未指定なら「実際に改行を含むキー」を自動検出
    - 列行数が揃わない所は None（全行に同値を伸ばしたい場合は lst[-1] へ変更）
    """
    if multiline_cols is None:
        # 自動検出（どれかの値に改行 or 文字列改行が含まれていた列）
        multiline_cols = []
        for k, val in v.items():
            if isinstance(val, (str, bytes)):
                s = val.decode() if isinstance(val, bytes) else val
                if _SPLIT_NL.search(s):
                    multiline_cols.append(k)

    splits: Dict[str, List[str]] = {}
    max_len = 1
    for k in multiline_cols:
        lst = _split_lines(v.get(k))
        splits[k] = lst
        if lst:
            max_len = max(max_len, len(lst))

    # 何も割れなかった場合はそのまま返す（保守的）
    if max_len == 1:
        return [v]

    out: List[Dict[str, Any]] = []
    for i in range(max_len):
        rec: Dict[str, Any] = {}
        for k, val in v.items():
            if k in splits:
                lst = splits[k]
                rec[k] = lst[i] if i < len(lst) else None
            else:
                rec[k] = val
        rec["score"] = _normalize_score(rec.get("score"))
        out.append(rec)

    # デバッグ：どの列が何行に割れたかをログに出す（必要なら print を logger に）
    try:
        print("[expand] cols=", multiline_cols, "lens=", {k: len(splits.get(k, [])) for k in multiline_cols})
    except Exception:
        pass

    return out
# --- helpers end ---


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
        iv = int(str(val).strip())
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

    # 3) DB保存（車両複数）— 改行展開を先に行う
    expanded: List[Dict[str, Any]] = []
    for v in parsed.get("vehicles", []):
        expanded.extend(_expand_by_index(v, multiline_cols=[
            "maker", "car_name", "grade", "model_code", "year",
            "mileage_km", "score", "start_price_yen",
            "color", "shift", "inspection_until"
        ]))

    vouts: List[VehicleOut] = []
    for v in expanded:
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
            score=(str(vo.score) if vo.score is not None else None),
            start_price_yen=vo.start_price_yen,
            raw_extracted_json=vo.raw_extracted_json
        ))

    db.commit()

    # ---- レスポンス検証（開発用） ----
    try:
        payload = AuctionSheetOut(
            id=sheet.id,
            file_name=sheet.file_name,
            auction_name=sheet.auction_name,
            auction_date=sheet.auction_date,
            uploaded_at=sheet.uploaded_at,
            vehicles=vouts,
        )
        return payload
    except ValidationError as ve:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "where": "response_model_validation",
                "errors": ve.errors(),
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
