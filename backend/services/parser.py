# backend/services/parser.py
# PDF出品票を解析して AuctionSheetIn 互換の dict を返す実装（pdfplumber版）
# Python 3.9 対応 / 2025-08-21 Maker抽出ロジック簡潔版（見出しフィルダウン廃止）

from __future__ import annotations

import io
import re
import unicodedata
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
    s2 = z2h(s).replace(",", "")
    m = re.search(r"(\d{1,9})\s*(?:km|KM|ＫＭ)?", s2)
    return int(m.group(1)) if m else to_int_or_none(s2)

def parse_auction_date(text: str) -> Optional[date]:
    """例: 2025/07/31, 2025-07-31, R6/5, 令和6年5月 を可能な範囲で解釈"""
    t = z2h(text)
    m = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d)
        except Exception:
            pass
    m = re.search(r"([Rr令][\s]*)(\d{1,2})[./年](\d{1,2})", t)
    if m:
        era_year = int(m.group(2))
        month = int(m.group(3))
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
HEADER_ALIASES: Dict[str, List[str]] = {
    "auction_no": ["出品番号", "番号", "Lot", "LOT", "ロット", "車番", "出品No", "出品No."],
    "maker":      ["メーカー", "メーカー名", "Maker", "Make", "ﾒｰｶｰ"],
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
    "displacement_cc": ["排気量", "cc", "エンジン"],
    "aircon": ["ｴｱｺﾝ", "エアコン", "A/C"],
    "equipment": ["装備", "オプション"],
    "lane": ["レーン", "ﾚｰﾝ", "Lane"],
}

# 既知メーカー（正準表記）
KNOWN_MAKERS: List[str] = [
    "トヨタ", "ホンダ", "日産", "スズキ", "スバル", "ダイハツ", "マツダ", "三菱",
    "レクサス", "いすゞ", "日野", "三菱ふそう", "メルセデス・ベンツ", "BMW",
    "アウディ", "フォルクスワーゲン", "ボルボ", "プジョー", "シトロエン",
    "ルノー", "ジープ", "フィアット", "アルファロメオ", "ポルシェ", "ミニ",
    "ランドローバー", "ジャガー", "キャデラック", "シボレー", "フォード", "テスラ",
]

# 英字表記の別名（必要最小限）
MAKER_ALIASES: Dict[str, str] = {
    "TOYOTA": "トヨタ",
    "NISSAN": "日産",
    "HONDA": "ホンダ",
    "SUZUKI": "スズキ",
    "SUBARU": "スバル",
    "DAIHATSU": "ダイハツ",
    "MAZDA": "マツダ",
    "MITSUBISHI": "三菱",
}

# --- 日本語カナ正規化（半角→全角、互換分解/合成 + 記号揺れの統一） ---

def _norm_jp(s: str) -> str:
    t = unicodedata.normalize("NFKC", s or "")
    # 空白除去（PDF抽出で空白が割り込む対策）
    t = re.sub(r"\s+", "", t)
    # 長音・中黒のゆれ
    t = t.replace("ｰ", "ー").replace("･", "・").replace("‐", "ー").replace("―", "ー")
    return t

def _norm_ascii_key(s: str) -> str:
    """ASCII英字を大文字に、英字以外を除去してキー化。"""
    t = _norm_jp(s)
    t = re.sub(r"[^A-Za-z]", "", t).upper()
    return t

# 正規化済みメーカー語の辞書（比較用）
_KNOWN_MAKERS_NORM: Dict[str, str] = {_norm_jp(mk): mk for mk in KNOWN_MAKERS}

def _strip_leading_lot_tokens(text: str) -> str:
    """行頭の出品番号/記号をスペース無しでも除去。
    例: "86001トヨタ シエンタ" / "86001 トヨタ" → "トヨタ シエンタ"
    """
    t = text
    t = re.sub(r"^\s*(?:出品\s*No\.?\s*|No\.?\s*|#|＃|№)?\s*\d{3,7}", "", t)
    t = re.sub(r"^\s*ﾚｰﾝ\s*[A-Za-z]?\d+\s+", "", t)
    return t.strip()

def explode_stacked_row(row: List[str]) -> List[List[str]]:
    """1セルに複数件が縦積みされた行を、\\n 区切りで複数行へ展開。"""
    if not row:
        return [row]
    split_cols: List[List[str]] = [(c or "").splitlines() for c in row]
    max_len = max((len(p) for p in split_cols), default=1)
    if max_len <= 1:
        return [row]
    multi_cols = sum(1 for p in split_cols if len(p) >= 2)
    if multi_cols < 2 and len(split_cols[0]) < 2:
        return [row]
    out: List[List[str]] = []
    for i in range(max_len):
        out.append([(p[i].strip() if i < len(p) else "") for p in split_cols])
    return out

def _detect_maker_in_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    """フリーテキストからメーカーと残り（車名候補）を抽出。"""
    if not text:
        return None, None
    head = _strip_leading_lot_tokens(text)
    head_norm = _norm_jp(z2h(head))

    # 日本語（正準）で前方一致
    for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
        if head_norm.startswith(mk_norm):
            rest = head_norm[len(mk_norm):].strip() or None
            return mk_orig, rest
    # 日本語：包含
    for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
        idx = head_norm.find(mk_norm)
        if idx != -1:
            rest = (head_norm[:idx] + head_norm[idx + len(mk_norm):]).strip() or None
            return mk_orig, rest

    # 英字表記
    ascii_key = _norm_ascii_key(head)
    for alias_key, canonical in MAKER_ALIASES.items():
        if ascii_key.startswith(alias_key) or ascii_key.find(alias_key) != -1:
            return canonical, None

    return None, None

def salvage_maker_from_row_cells(row_cells: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    行内（列跨ぎを含む）からメーカーと車名候補を抽出。
    - 先頭〜数列を連結（例: "86753トヨ" + "タ" → "86753トヨタ"）
    - 行全体連結の両方で検出を試行
    戻り値: (maker or None, car_name_rest or None)
    """
    if not row_cells:
        return None, None

    # a) 左側 3 列を連結して判定（メーカー断片が分割されやすい）
    left_join = "".join([(c or "") for c in row_cells[:3]])
    mk, rest = _detect_maker_in_text(left_join)
    if mk:
        return mk, rest

    # b) 行全体をスペース区切りで連結して判定
    head = " ".join([c for c in row_cells if c]).strip()
    return _detect_maker_in_text(head)

def salvage_maker_from_car_name_prefix(v: Dict[str, Any]) -> None:
    """car_name 先頭にメーカーが連結されている場合に切り出す。"""
    name = (v.get("car_name") or "").strip()
    if not name or v.get("maker"):
        return
    name_norm = _norm_jp(z2h(name))
    for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
        if name_norm.startswith(mk_norm):
            rest = name_norm[len(mk_norm):].strip()
            v["maker"] = mk_orig
            if rest:
                v["car_name"] = rest
            return

def header_match_score(cell: str, target_list: List[str]) -> int:
    cs = z2h(cell)
    return max((1 if alias in cs else 0) for alias in target_list)

def normalize_header_row(row: List[str]) -> Dict[int, str]:
    """ヘッダ行から {col_index: key_name} を推定"""
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
        elif key == "displacement_cc":
            v[key] = to_int_or_none(s)
        else:
            v[key] = z2h(s)
    return v

# ==== メイン処理 ====

def parse_auction_sheet(pdf_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    入力: PDF bytes, filename
    出力: AuctionSheetIn 互換の dict
    * メーカー見出しのフィルダウンは行わず、常に行内から直接抽出。
    * セル縦積み（stacked rows）は展開して1レコードずつに分解。
    """
    auction_name: Optional[str] = None
    auction_date_val: Optional[date] = None
    vehicles: List[Dict[str, Any]] = []

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

        m = re.search(r"(USS\s*[\u3040-\u30FF\u4E00-\u9FFF]+|JU\s*[\u3040-\u30FF\u4E00-\u9FFF]+|TAA\s*[\u3040-\u30FF\u4E00-\u9FFF]+)", joined)
        if m:
            auction_name = z2h(m.group(1)).replace(" ", "")

        auction_date_val = parse_auction_date(joined)

        # ---- テーブル抽出（各ページ）----
        for page in pdf.pages:
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

            for tbl in tables:
                if not tbl or len(tbl) < 2:
                    continue

                # ベストなヘッダを選定
                header_row_idx = 0
                header_map: Dict[int, str] = {}
                best_cols = 0
                scan = min(3, len(tbl))
                for i in range(scan):
                    row = [(c or "").strip() for c in tbl[i]]
                    mapping = normalize_header_row(row)
                    if len(mapping) > best_cols:
                        best_cols = len(mapping)
                        header_map = mapping
                        header_row_idx = i
                if not header_map:
                    continue

                # データ行
                data_rows = tbl[header_row_idx + 1 :]
                for r in data_rows:
                    base_row = [(c or "").strip() for c in r]
                    for row in explode_stacked_row(base_row):
                        vehicle = coerce_row_to_vehicle(row, header_map)

                        # maker が空なら行内からサルベージ（列跨ぎ対応）
                        if not vehicle.get("maker"):
                            mk, car_rest = salvage_maker_from_row_cells(row)
                            if mk:
                                vehicle["maker"] = mk
                                if not vehicle.get("car_name") and car_rest:
                                    vehicle["car_name"] = car_rest

                        # car_name 先頭にメーカーが付いていれば分離
                        salvage_maker_from_car_name_prefix(vehicle)

                        # 最低限のキー（出品番号 or 車名 or メーカー）がなければ捨てる
                        if not any(vehicle.get(k) for k in ("auction_no", "car_name", "maker")):
                            continue

                        # デバッグ出力（必要に応じて利用）
                        try:
                            print(
                                "[maker]",
                                vehicle.get("maker"),
                                "| car=",
                                vehicle.get("car_name"),
                                "| row=",
                                " | ".join(row),
                            )
                        except Exception:
                            pass

                        if "raw_extracted_json" not in vehicle:
                            vehicle["raw_extracted_json"] = {"row": row}

                        vehicles.append(vehicle)

    # テーブルから拾えなかった場合のフォールバック（単票など）
    if not vehicles:
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
        if not v.get("maker") and v.get("car_name"):
            mk, rest = _detect_maker_in_text(v["car_name"])  # 例: "トヨタｼｴﾝﾀ"
            if mk:
                v["maker"] = mk
                if rest:
                    v["car_name"] = rest
        if any(v.get(k) for k in ("auction_no", "car_name", "maker")):
            try:
                print("[maker-fallback]", v.get("maker"), "| car=", v.get("car_name"))
            except Exception:
                pass
            vehicles.append(v)

    if not auction_name:
        if "uss" in (filename or "").lower():
            auction_name = "USS"

    result: Dict[str, Any] = {
        "file_name": filename,
        "auction_name": auction_name,
        "auction_date": auction_date_val.isoformat() if auction_date_val else None,
        "vehicles": vehicles or [],
    }
    return result
