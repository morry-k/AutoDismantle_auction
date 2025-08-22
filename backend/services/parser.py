import io
import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional
import os
import json

import pdfplumber

# ==== デバッグ ====
DEBUG_ON = os.getenv("PARSER_DEBUG") == "1"

def trace(stage: str, msg: str, extra: Dict[str, Any] = None):
    if not DEBUG_ON: return
    extra_str = f" | {extra}" if extra else ""
    print(f"[TRACE] {stage}: {msg}{extra_str}")

# ==== ユーティリティ ====
ZEN2HAN = str.maketrans("０１２３４５６７８９－，．／", "0123456789-,./")
def z2h(s: Optional[str]) -> str:
    return unicodedata.normalize("NFKC", (s or "")).translate(ZEN2HAN).strip()

def to_int_or_none(s: Optional[str]) -> Optional[int]:
    if s is None: return None
    s2 = re.sub(r"[^\d]", "", z2h(s))
    if not s2: return None
    try:
        return int(s2)
    except ValueError:
        return None

def parse_japanese_year(s: Optional[str]) -> Optional[int]:
    if not s: return None
    s_norm = z2h(s)
    m = re.search(r"[Hh平成](\d{1,2})", s_norm)
    if m: return 1988 + int(m.group(1))
    m = re.search(r"[Rr令](\d{1,2})", s_norm)
    if m: return 2018 + int(m.group(1))
    return to_int_or_none(s_norm)

def parse_mileage_km(s: Optional[str]) -> Optional[int]:
    if not s: return None
    val = to_int_or_none(s)
    if val is not None and val < 1000:
        return val * 1000
    return val

def parse_auction_date_from_text(text: str) -> Optional[date]:
    t = z2h(text)
    m = re.search(r"(20\d{2})[/\.](\d{1,2})[/\.](\d{1,2})", t)
    if m:
        try: return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError: pass
    m = re.search(r"([Rr令])(\d{1,2})[/\.](\d{1,2})", t)
    if m:
        try: return date(2018 + int(m.group(2)), int(m.group(3)), 1)
        except ValueError: pass
    return None

# ==== 座標ベースのテーブル再構築 ====
HEADER_KEYWORDS: Dict[str, str] = {
    "出品№": "auction_no", "メーカー": "maker", "車名": "car_name", "グレード": "grade",
    "年式": "year", "型式": "model_code", "排気量": "displacement_cc", "車検": "inspection_until",
    "走行": "mileage_km", "色": "color", "ｼﾌﾄ": "shift", "ｴｱｺﾝ": "aircon",
    "装備": "equipment", "評価点": "score", "ｽﾀｰﾄ": "start_price_yen", "ﾚｰﾝ": "lane"
}

def build_layout_from_page(page: pdfplumber.page.Page) -> List[Dict[str, Any]]:
    words = page.extract_words(x_tolerance=2, y_tolerance=2, keep_blank_chars=True)
    if not words: return []

    header_words: Dict[str, Dict[str, Any]] = {}
    for word in [w for w in words if w['top'] < page.height * 0.2]:
        for keyword, field_name in HEADER_KEYWORDS.items():
            if word['text'].strip().startswith(keyword):
                if field_name not in header_words or word['x0'] < header_words[field_name]['x0']:
                    header_words[field_name] = word

    if not header_words:
        trace("layout_build", "Header keywords not found", {"page": page.page_number})
        return []

    sorted_headers = sorted(header_words.values(), key=lambda w: w['x0'])
    columns = []
    for i, header_word in enumerate(sorted_headers):
        field_name = next(k for k, v in header_words.items() if v == header_word)
        x0 = header_word['x0']
        x1 = sorted_headers[i + 1]['x0'] if i + 1 < len(sorted_headers) else page.width
        columns.append({'name': field_name, 'x0': x0 - 2, 'x1': x1 - 2})

    lines: Dict[float, List[Dict[str, Any]]] = {}
    header_bottom = max(w['bottom'] for w in sorted_headers)
    data_words = [w for w in words if w['top'] > header_bottom]

    for word in data_words:
        y_center = (word['top'] + word['bottom']) / 2
        found_line = False
        for y_key in lines.keys():
            if abs(y_key - y_center) < 5:
                lines[y_key].append(word)
                found_line = True
                break
        
        # ▼▼▼ 修正点: y_keyが定義されていない場合はy_centerを新しいキーとして使う ▼▼▼
        if not found_line:
            lines[y_center] = [word]
        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    rows = []
    for y_key in sorted(lines.keys()):
        line_words = sorted(lines[y_key], key=lambda w: w['x0'])
        row_data: Dict[str, str] = {c['name']: "" for c in columns}
        for word in line_words:
            word_center_x = (word['x0'] + word['x1']) / 2
            for col in columns:
                if col['x0'] <= word_center_x < col['x1']:
                    if row_data[col['name']]: row_data[col['name']] += " "
                    row_data[col['name']] += word['text']
                    break
        for k, v in row_data.items(): row_data[k] = v.strip()
        if not row_data.get("auction_no") or not row_data["auction_no"].isdigit(): continue
        rows.append(row_data)

    trace("layout_build", f"Reconstructed {len(rows)} rows", {"page": page.page_number})
    return rows

# ==== メイン処理 ====
def parse_auction_sheet(pdf_bytes: bytes, filename: str) -> Dict[str, Any]:
    trace("start", "parse begin (coordinate-based)", {"file": filename})
    all_vehicles: List[Dict[str, Any]] = []
    
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        full_text = "".join(p.extract_text() or "" for p in pdf.pages)
        m = re.search(r"(USS|JU|TAA)\s*([\u4E00-\u9FFF]+)", full_text)
        auction_name = f"{m.group(1)}{m.group(2)}" if m else None
        auction_date = parse_auction_date_from_text(full_text)

        for page in pdf.pages:
            trace("parser", f"Processing page {page.page_number}")
            reconstructed_rows = build_layout_from_page(page)
            
            for i, row in enumerate(reconstructed_rows):
                try:
                    start_price_str = row.get("start_price_yen", "") or ""
                    
                    vehicle: Dict[str, Any] = {
                        "auction_no": row.get("auction_no"),
                        "maker": row.get("maker"),
                        "car_name": row.get("car_name"),
                        "grade": row.get("grade"),
                        "model_code": row.get("model_code"),
                        "year": parse_japanese_year(row.get("year")),
                        "displacement_cc": to_int_or_none(row.get("displacement_cc")),
                        "mileage_km": parse_mileage_km(row.get("mileage_km")),
                        "inspection_until": row.get("inspection_until"),
                        "color": row.get("color"),
                        "shift": row.get("shift"),
                        "aircon": row.get("aircon"),
                        "equipment": row.get("equipment"),
                        "score": row.get("score"),
                        "start_price_yen": to_int_or_none(start_price_str.replace(",", "")),
                        "lane": row.get("lane"),
                        "raw_extracted_json": {"coordinate_based_row": row}
                    }
                    all_vehicles.append(vehicle)
                except Exception as e:
                    print(f"--- ERROR parsing row {i} on page {page.page_number} ---")
                    print(f"Row data: {row}")
                    print(f"Error: {e}")
                    print("-----------------------------------------------------")
                    continue
            
    trace("parse_done", "end", {"vehicles": len(all_vehicles)})

    return {
        "file_name": filename,
        "auction_name": auction_name,
        "auction_date": auction_date.isoformat() if auction_date else None,
        "vehicles": all_vehicles,
    }