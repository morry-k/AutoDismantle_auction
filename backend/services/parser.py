# backend/services/parser.py
# PDF出品票を解析して AuctionSheetIn 互換の dict を返す実装（pdfplumber版）
# Python 3.9 対応 / 2025-08-21 Maker抽出ロジック改良（分割カラム対応・最小修正）

import io
import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

# ==== ユーティリティ ====
ZEN2HAN = str.maketrans("０１２３４５６７８９－，．／", "0123456789-,./")

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
        era_year = int(m.group(2)); month = int(m.group(3)); day = 1
        base = 2018  # 令和: 2019年=R1 → +2018
        try:
            return date(base + era_year, month, day)
        except Exception:
            pass
    m = re.search(r"[Hh平成][\s]*(\d{1,2})[./年](\d{1,2})", t)
    if m:
        era_year = int(m.group(1)); month = int(m.group(2)); day = 1
        base = 1988  # 平成: 1989年=H1 → +1988
        try:
            return date(base + era_year, month, day)
        except Exception:
            pass
    return None

def _complete_maker_prefix(token_norm: str) -> Optional[str]:
    cands = [mk_orig for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items()
             if mk_norm.startswith(token_norm)]
    return cands[0] if len(cands) == 1 else None

# ==== ヘッダ定義 ====
HEADER_ALIASES: Dict[str, List[str]] = {
    "auction_no": ["出品番号", "番号", "Lot", "LOT", "ロット", "車番", "出品No", "出品No."],
    "maker": ["メーカー", "メーカー名", "Maker", "Make", "ﾒｰｶｰ"],
    "car_name": ["車名", "車種", "車両名", "Model", "車型"],
    "grade": ["グレード", "仕様", "Grade"],
    "model_code": ["型式", "型式/類別", "型式類別", "Type"],
    "year": ["年式", "初度登録", "初年度", "年式(初度)"],
    "mileage_km": ["走行距離", "距離", "走行", "走行(km)"],
    "color": ["色", "カラー", "Color"],
    "shift": ["ミッション", "AT/MT", "シフト", "変速"],
    "inspection_until": ["車検", "車検有効", "車検満了", "検切れ"],
    "score": ["評価", "評価点", "点数"],
    "start_price_yen": ["スタート", "開始価格", "最低価格", "Start"],
    "displacement_cc": ["排気量", "cc", "エンジン"],
    "aircon": ["ｴｱｺﾝ", "エアコン", "A/C"],
    "equipment": ["装備", "オプション"],
    "lane": ["レーン", "ﾚｰﾝ", "Lane"],
}

KNOWN_MAKERS: List[str] = [
    "トヨタ", "ホンダ", "日産", "スズキ", "スバル", "ダイハツ", "マツダ", "三菱",
    "レクサス", "いすゞ", "日野", "三菱ふそう", "メルセデス・ベンツ", "BMW",
    "アウディ", "フォルクスワーゲン", "ボルボ", "プジョー", "シトロエン",
    "ルノー", "ジープ", "フィアット", "アルファロメオ", "ポルシェ", "ミニ",
    "ランドローバー", "ジャガー", "キャデラック", "シボレー", "フォード", "テスラ",
]

# 先頭付近の定義に追加
MAKER_FRAGMENT_RULES = [
    (("トヨ", "タ"), "トヨタ"),
    (("ホン", "ダ"), "ホンダ"),
    (("スバ", "ル"), "スバル"),
    (("ダイ", "ハツ"), "ダイハツ"),
    (("マツ", "ダ"), "マツダ"),
]

def _lead_kana(s: str, n: int = 2) -> str:
    return _norm_jp(z2h(s))[:n]


_KNOWN_MAKERS_NORM: Dict[str, str] = {unicodedata.normalize("NFKC", mk): mk for mk in KNOWN_MAKERS}

# ==== 正規化とサルベージ ====
def _norm_jp(s: str) -> str:
    # 半角ｶﾅ→全角、空白除去、長音/中黒のゆれを最小限吸収
    t = unicodedata.normalize("NFKC", s or "")
    t = re.sub(r"\s+", "", t)
    t = t.replace("ｰ", "ー").replace("･", "・").replace("‐", "ー").replace("―", "ー")
    return t

def _strip_leading_lot_tokens(text: str) -> str:
    # スペース無しの数字直結にも対応
    t = re.sub(r"^\s*(?:出品\s*No\.?\s*|No\.?\s*|#|＃|№)?\s*\d{3,7}", "", text)
    return t.strip()

def salvage_maker_from_row_cells(row: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    行内（列跨ぎを含む）からメーカーと車名候補を抽出。
    戻り: (maker or None, car_name_rest or None)
    """
    if not row:
        return None, None

    first6 = [(c or "") for c in row[:6]]
    for i in range(len(first6) - 1):
        a = _lead_kana(first6[i], 2)
        b = _lead_kana(first6[i + 1], 2)
        for (fragA, fragB), mk in MAKER_FRAGMENT_RULES:
            if a.endswith(fragA) and (b.startswith(fragB) or fragB in b):
                return mk, None


    # 1) 先頭5列を連結（例: "86009ホン"+"ダ" → "86009ホンダ"）
    left_join = "".join([(c or "") for c in row[:6]])
    head = _strip_leading_lot_tokens(z2h(left_join))
    head_norm = _norm_jp(head)

    # 既知メーカーの包含チェック
    for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
        if mk_norm in head_norm:
            rest = head_norm.replace(mk_norm, "").strip() or None
            return mk_orig, rest

    # 2) 列割れの2分割復元（a列の末尾×b列の先頭）
    lead5 = [(c or "") for c in row[:5]]
    def _lead2(s: str) -> str:
        return _norm_jp(z2h(s))[:2]
    for i in range(len(lead5) - 1):
        a = _lead2(lead5[i]); b = _lead2(lead5[i + 1])
        for (fragA, fragB), mk in MAKER_FRAGMENT_RULES:
            if a.endswith(fragA) and (b.startswith(fragB) or fragB in b):
                return mk, None

    # 3) 行全体でも一応チェック
    all_join = " ".join([c for c in row if c]).strip()
    all_norm = _norm_jp(_strip_leading_lot_tokens(z2h(all_join)))
    for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
        if mk_norm in all_norm:
            rest = all_norm.replace(mk_norm, "").strip() or None
            return mk_orig, rest

    # 先頭トークン（カタカナ/漢字 2〜3 文字）を取り出して補完
    m = re.match(r"^([\u30A0-\u30FF\u4E00-\u9FFF]{2,3})", head_norm)
    if m:
        mk = _complete_maker_prefix(m.group(1))
        if mk:
            return mk, None

    return None, None

def salvage_maker_from_car_name_prefix(v: Dict[str, Any]) -> None:
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

# ==== ヘッダ解析 ====
def header_match_score(cell: str, target_list: List[str]) -> int:
    cs = z2h(cell)
    return max((1 if alias in cs else 0) for alias in target_list)

def normalize_header_row(row: List[str]) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for idx, cell in enumerate(row):
        best_key, best_score = None, 0
        for key, aliases in HEADER_ALIASES.items():
            score = header_match_score(cell or "", aliases)
            if score > best_score:
                best_key, best_score = key, score
        if best_key:
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

# ==== 行展開処理（縦積み） ====
def explode_stacked_row(row: List[str]) -> List[List[str]]:
    split_cols: List[List[str]] = [(c or "").splitlines() for c in row]
    max_len = max((len(p) for p in split_cols), default=1)
    if max_len <= 1:
        return [row]
    out: List[List[str]] = []
    for i in range(max_len):
        out.append([(p[i].strip() if i < len(p) else "") for p in split_cols])
    return out

# ==== メイン処理 ====
def parse_auction_sheet(pdf_bytes: bytes, filename: str) -> Dict[str, Any]:
    auction_name: Optional[str] = None
    auction_date_val: Optional[date] = None
    vehicles: List[Dict[str, Any]] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        # 全文から会場名・日付
        all_text = []
        for page in pdf.pages:
            try:
                all_text.append(page.extract_text() or "")
            except Exception:
                all_text.append("")
        joined = "\n".join(all_text)

        m = re.search(r"(USS\s*[\u3040-\u30FF\u4E00-\u9FFF]+|JU\s*[\u3040-\u30FF\u4E00-\u9FFF]+|TAA\s*[\u3040-\u30FF\u4E00-\u9FFF]+)", joined)
        if m:
            auction_name = z2h(m.group(1)).replace(" ", "")
        auction_date_val = parse_auction_date(joined)

        # テーブル抽出
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
                # ヘッダ推定
                header_row_idx, header_map, best_cols = 0, {}, 0
                for i in range(min(3, len(tbl))):
                    row = [(c or "").strip() for c in tbl[i]]
                    mapping = normalize_header_row(row)
                    if len(mapping) > best_cols:
                        best_cols, header_map, header_row_idx = len(mapping), mapping, i
                if not header_map:
                    continue

                # データ行
                data_rows = tbl[header_row_idx + 1 :]
                for r in data_rows:
                    base_row = [(c or "").strip() for c in r]
                    # 縦積み展開
                    for row in explode_stacked_row(base_row):
                        vehicle = coerce_row_to_vehicle(row, header_map)

                        # maker サルベージ（列割れ & 行内）
                        if not vehicle.get("maker"):
                            mk, car_rest = salvage_maker_from_row_cells(row)
                            if mk:
                                vehicle["maker"] = mk
                                if not vehicle.get("car_name") and car_rest:
                                    vehicle["car_name"] = car_rest

                        # car_name 先頭にメーカーが付いていれば分離
                        salvage_maker_from_car_name_prefix(vehicle)

                        # 最低限のキーが無ければ捨てる
                        if not any(vehicle.get(k) for k in ("auction_no", "car_name", "maker")):
                            continue

                        if "raw_extracted_json" not in vehicle:
                            vehicle["raw_extracted_json"] = {"row": row}

                        vehicles.append(vehicle)

    if not auction_name and "uss" in (filename or "").lower():
        auction_name = "USS"

    return {
        "file_name": filename,
        "auction_name": auction_name,
        "auction_date": auction_date_val.isoformat() if auction_date_val else None,
        "vehicles": vehicles or [],
    }
