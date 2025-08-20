# backend/services/parser.py
# PDF出品票を解析して AuctionSheetIn 互換の dict を返す実装（pdfplumber版）
# Python 3.9 対応

import io
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

# ==== ユーティリティ ====

ZEN2HAN = str.maketrans(
    "０１２３４５６７８９－，．／",
    "0123456789-,./",
)

def z2h(s: Optional[str]) -> str:
    return (s or "").translate(ZEN2HAN).strip()

def to_int_or_none(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    s2 = z2h(s)
    s2 = re.sub(r"[^\d\-]", "", s2)
    if s2 in ("", "-", "--"):
        return None
    try:
        return int(s2)
    except Exception:
        return None

def parse_mileage_km(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    s2 = z2h(s)
    s2 = s2.replace(",", "")
    m = re.search(r"(\d{1,9})\s*(?:km|KM|ＫＭ)?", s2)
    return int(m.group(1)) if m else to_int_or_none(s2)

def parse_auction_date(text: str) -> Optional[date]:
    """
    例: 2025/07/31, 2025-07-31, R6/5, 令和6年5月 などを可能な範囲で解釈
    """
    t = z2h(text)

    # 西暦 YYYY/MM/DD
    m = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d)
        except Exception:
            pass

    # 元号 R(令和)/H(平成) の簡易対応: R=2019, H=1989起点
    m = re.search(r"([Rr令][\s]*)(\d{1,2})[./年](\d{1,2})", t)
    if m:
        era_year = int(m.group(2))
        month = int(m.group(3))
        # 日付がないケースは 1日扱い
        day = 1
        base = 2018  # 令和: 2019年=R1 → +2018
        try:
            return date(base + era_year, month, day)
        except Exception:
            pass

    m = re.search(r"[Hh平成][\s]*(\d{1,2})[./年](\d{1,2})", t)
    if m:
        era_year = int(m.group(1))
        month = int(m.group(2))
        day = 1
        base = 1988  # 平成: 1989年=H1 → +1988
        try:
            return date(base + era_year, month, day)
        except Exception:
            pass

    return None

# 列ヘッダの候補（よくあるゆらぎを吸収）
HEADER_ALIASES = {
    "auction_no": ["出品番号", "番号", "Lot", "LOT", "ロット", "車番", "出品No", "出品No."],
    "maker":      ["メーカー", "メーカー名", "Maker", "Make"],
    "car_name":   ["車名", "車種", "車両名", "Model", "車型"],
    "grade":      ["グレード", "仕様", "Grade"],
    "model_code": ["型式", "型式/類別", "型式類別", "Type"],
    "year":       ["年式", "初度登録", "初年度", "年式(初度)"],
    "mileage_km": ["走行距離", "距離", "走行", "走行(km)"],
    "color":      ["色", "カラー", "Color"],
    "shift":      ["ミッション", "AT/MT", "シフト", "変速"],
    "inspection_until": ["車検", "車検有効", "車検満了", "検切れ"],
    "score":      ["評価", "評価点", "点数"],
    "start_price_yen": ["スタート", "開始価格", "最低価格", "Start"],
}



def header_match_score(cell: str, target_list: List[str]) -> int:
    cs = z2h(cell)
    return max((1 if alias in cs else 0) for alias in target_list)

def normalize_header_row(row: List[str]) -> Dict[int, str]:
    """
    テーブル1行（ヘッダ想定）から、どのカラムがどのキーに対応するかを推定
    戻り: {col_index: key_name}
    """
    mapping: Dict[int, str] = {}
    for idx, cell in enumerate(row):
        best_key = None
        best_score = 0
        for key, aliases in HEADER_ALIASES.items():
            score = header_match_score(cell or "", aliases)
            if score > best_score:
                best_key = key
                best_score = score
        if best_key and best_score > 0:
            mapping[idx] = best_key
    return mapping

def coerce_row_to_vehicle(row: List[str], colmap: Dict[int, str]) -> Dict[str, Any]:
    v: Dict[str, Any] = {}
    for i, raw in enumerate(row):
        key = colmap.get(i)
        if not key:
            continue
        s = (raw or "").strip()
        if key == "mileage_km":
            v[key] = parse_mileage_km(s)
        elif key == "year":
            v[key] = to_int_or_none(s)
        elif key == "start_price_yen":
            ss = z2h(s).replace(",", "")
            m = re.search(r"(-?\d+)", ss)
            v[key] = int(m.group(1)) if m else None
        else:
            v[key] = z2h(s)
    return v

# ==== メイン処理 ====

def parse_auction_sheet(pdf_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    入力: PDF bytes, filename
    出力: AuctionSheetIn 互換の dict
    """
    auction_name: Optional[str] = None
    auction_date_val: Optional[date] = None
    vehicles: List[Dict[str, Any]] = []
    raw_rows_debug: List[Dict[str, Any]] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        # ---- ページ全文から会場名・日付のヒントを拾う ----
        all_text = []
        for page in pdf.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            all_text.append(t)
        joined = "\n".join(all_text)

        # 会場名っぽい箇所（例: "USS東京", "JU埼玉" など）
        m = re.search(r"(USS\s*[\u3040-\u30FF\u4E00-\u9FFF]+|JU\s*[\u3040-\u30FF\u4E00-\u9FFF]+|TAA\s*[\u3040-\u30FF\u4E00-\u9FFF]+)", joined)
        if m:
            auction_name = z2h(m.group(1)).replace(" ", "")

        # 日付抽出
        auction_date_val = parse_auction_date(joined)

        # ---- テーブル抽出（各ページ）----
        for page in pdf.pages:
            # テーブル設定を2パターン試す（PDFの個体差に対応）
            table_settings_candidates = [
                dict(vertical_strategy="lines", horizontal_strategy="lines", snap_tolerance=3, join_tolerance=3, edge_min_length=20, min_words_vertical=1),
                dict(vertical_strategy="text", horizontal_strategy="text", snap_tolerance=8, join_tolerance=8, edge_min_length=10, min_words_vertical=1),
            ]
            tables: List[List[List[str]]] = []
            for ts in table_settings_candidates:
                try:
                    _t = page.extract_tables(table_settings=ts) or []
                    if _t:
                        tables.extend(_t)
                except Exception:
                    pass

            # 取得したテーブル候補から、ヘッダーを推定して最も“車両一覧っぽい”ものを採用
            for tbl in tables:
                if not tbl or len(tbl) < 2:
                    continue
                # 先頭の数行のうち、ベストなヘッダを探す
                header_row_idx = 0
                header_map: Dict[int, str] = {}
                best_cols = 0
                scan = min(3, len(tbl))
                for i in range(scan):
                    row = [ (c or "").strip() for c in tbl[i] ]
                    mapping = normalize_header_row(row)
                    if len(mapping) > best_cols:
                        best_cols = len(mapping)
                        header_map = mapping
                        header_row_idx = i

                if not header_map:
                    # ヘッダが見つからない → スキップ
                    continue

                # ヘッダ行より下をデータ行と見なす
                data_rows = tbl[header_row_idx + 1 :]
                for r in data_rows:
                    row = [ (c or "").strip() for c in r ]
                    vehicle = coerce_row_to_vehicle(row, header_map)

                    # 最低限のキー（出品番号 or 車名）があれば採用
                    if not any(vehicle.get(k) for k in ("auction_no", "car_name", "maker")):
                        continue

                    # デバッグ用に生行も添える
                    if "raw_extracted_json" not in vehicle:
                        vehicle["raw_extracted_json"] = {"row": row}

                    vehicles.append(vehicle)
                    raw_rows_debug.append({"header_map": header_map, "row": row})

    # テーブルから拾えなかった場合のフォールバック（PDFの1車両票など）
    if not vehicles:
        # 簡易に全文から拾えそうなキーを正規表現で抜く
        txt = z2h(joined if 'joined' in locals() else "")
        def find(pattern: str) -> Optional[str]:
            m = re.search(pattern, txt)
            return m.group(1).strip() if m else None

        v: Dict[str, Any] = {
            "auction_no": find(r"(?:出品番号|出品No\.?|LOT|ロット)[^\d]*(\d{3,6})"),
            "maker": find(r"(?:メーカー|Make)[：:\s]*([^\n]+)"),
            "car_name": find(r"(?:車名|車種|Model)[：:\s]*([^\n]+)"),
            "grade": find(r"(?:グレード|仕様|Grade)[：:\s]*([^\n]+)"),
            "model_code": find(r"(?:型式)[：:\s]*([^\n]+)"),
            "year": to_int_or_none(find(r"(?:年式|初度登録)[：:\s]*([0-9０-９]{4})")),
            "mileage_km": parse_mileage_km(find(r"(?:走行距離)[：:\s]*([0-9０-９,]+)")),
            "color": find(r"(?:色|カラー)[：:\s]*([^\n]+)"),
            "shift": find(r"(?:ミッション|AT/MT|変速)[：:\s]*([^\n]+)"),
            "inspection_until": find(r"(?:車検|有効|満了)[：:\s]*([^\n]+)"),
            "score": find(r"(?:評価|評価点)[：:\s]*([^\n]+)"),
            "start_price_yen": to_int_or_none(find(r"(?:スタート|開始価格)[：:\s]*([0-9０-９,]+)")),
            "raw_extracted_json": {"fallback_text": True},
        }
        # どれか埋まっていれば1台として採用
        if any(v.get(k) for k in ("auction_no", "car_name", "maker")):
            vehicles.append(v)

    # 会場名・日付の最終フォールバック
    if not auction_name:
        # ファイル名から想定（例: USS東京 の断片）
        if "uss" in filename.lower():
            auction_name = "USS"
        else:
            auction_name = None

    result: Dict[str, Any] = {
        "file_name": filename,
        "auction_name": auction_name,
        "auction_date": auction_date_val.isoformat() if auction_date_val else None,  # ←ここを文字列化
        "vehicles": vehicles or [],
    }
    return result
    