# backend/services/parser.py
# PDF出品票を解析して AuctionSheetIn 互換の dict を返す実装（pdfplumber版）
# print デバッグ対応（PARSER_DEBUG=1 で有効）/ 2025-08-22

import io
import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
import os

import pdfplumber

# ==== デバッグ（print のみ） ====
DEBUG_ON = os.getenv("PARSER_DEBUG") == "1"

def trace(stage: str, msg: str, extra: Dict[str, Any] = None):
    if not DEBUG_ON:
        return
    if extra:
        print(f"[TRACE] {stage}: {msg} | {extra}")
    else:
        print(f"[TRACE] {stage}: {msg}")

def miss(field: str, row: Any, stage: str = "final"):
    if not DEBUG_ON:
        return
    if isinstance(row, list):
        row_preview = [c[:30] if isinstance(c, str) else c for c in row[:8]]
    else:
        row_preview = row
    print(f"[MISS] stage={stage} field={field} row_preview={row_preview}")

# ==== ノイズ（キー行など） ====
NOISE_REGEX = re.compile(r'^\s*(?:ｷｰ|ｷ|キー|key|Key|KEY)\s*$')

def _strip_noise_cell(cell: Optional[str]) -> str:
    s = (cell or "").strip()
    sz = z2h(s)
    return "" if NOISE_REGEX.match(sz) else s

def _strip_noise_row(row: List[str]) -> List[str]:
    return [_strip_noise_cell(c) for c in row]

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
        base = 2018  # 2019年=R1
        try:
            return date(base + era_year, month, day)
        except Exception:
            pass
    m = re.search(r"[Hh平成][\s]*(\d{1,2})[./年](\d{1,2})", t)
    if m:
        era_year = int(m.group(1)); month = int(m.group(2)); day = 1
        base = 1988  # 1989年=H1
        try:
            return date(base + era_year, month, day)
        except Exception:
            pass
    return None

# 濁点・半濁点無視比較
_DAKUTEN_BASE = str.maketrans(
    "ガギグゲゴザジズゼゾダヂヅデドバビブベボヴパピプペポ",
    "カキクケコサシスセソタチツテトハヒフヘホウハヒフヘホ"
)
def _devoice_katakana(s: str) -> str:
    return (s or "").translate(_DAKUTEN_BASE)

def _norm_jp(s: str) -> str:
    t = unicodedata.normalize("NFKC", s or "")
    t = re.sub(r"\s+", "", t)
    t = t.replace("ｰ", "ー").replace("･", "・").replace("‐", "ー").replace("―", "ー")
    return t

def _strip_leading_lot_tokens(text: str) -> str:
    t = re.sub(r"^\s*(?:出品\s*No\.?\s*|No\.?\s*|#|＃|№)?\s*\d{3,7}", "", text)
    return t.strip()

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

# PDFに出現した語の一致のみ（推測禁止）
KNOWN_MAKERS: List[str] = [
    "トヨタ", "ホンダ", "日産", "スズキ", "スバル", "ダイハツ", "マツダ", "三菱",
    "レクサス", "いすゞ", "日野", "三菱ふそう", "メルセデス・ベンツ", "BMW",
    "アウディ", "フォルクスワーゲン", "ボルボ", "プジョー", "シトロエン",
    "ルノー", "ジープ", "フィアット", "アルファロメオ", "ポルシェ", "ミニ",
    "ランドローバー", "ジャガー", "キャデラック", "シボレー", "フォード", "テスラ",
]
MAKER_FRAGMENT_RULES: List[Tuple[Tuple[str, str], str]] = [
    (("トヨ", "タ"), "トヨタ"),
    (("ホン", "ダ"), "ホンダ"),
    (("スバ", "ル"), "スバル"),
    (("ダイ", "ハツ"), "ダイハツ"),
    (("マツ", "ダ"), "マツダ"),
    (("スズ", "キ"), "スズキ"),
]
_KNOWN_MAKERS_NORM: Dict[str, str] = {unicodedata.normalize("NFKC", mk): mk for mk in KNOWN_MAKERS}

# ==== メーカー抽出（サルベージ） ====
def _complete_maker_prefix(token_norm: str) -> Optional[str]:
    cands = [mk_orig for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items() if mk_norm.startswith(token_norm)]
    return cands[0] if len(cands) == 1 else None

def salvage_maker_from_row_cells(row: List[str]) -> Tuple[Optional[str], Optional[str]]:
    row = _strip_noise_row(row)
    if not row or not any((c or "").strip() for c in row):
        return None, None

    first6 = [(c or "") for c in row[:6]]
    def _lead2_norm(s: str) -> str:
        return _norm_jp(z2h(s))[:2]
    for i in range(len(first6) - 1):
        a = _lead2_norm(first6[i]); b = _lead2_norm(first6[i + 1])
        a_dev, b_dev = _devoice_katakana(a), _devoice_katakana(b)
        for (fragA, fragB), mk in MAKER_FRAGMENT_RULES:
            if (a.endswith(fragA) and (b.startswith(fragB) or fragB in b)) or \
               (a_dev.endswith(fragA) and (b_dev.startswith(fragB) or fragB in b_dev)):
                return mk, None

    left_join = "".join([(c or "") for c in row[:6]])
    head = _strip_leading_lot_tokens(z2h(left_join))
    head_norm = _norm_jp(head); head_dev = _devoice_katakana(head_norm)
    for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
        if mk_norm in head_norm or mk_norm in head_dev:
            rest = head_norm.replace(mk_norm, "").strip() or None
            return mk_orig, rest

    lead5 = [(c or "") for c in row[:5]]
    for i in range(len(lead5) - 1):
        a = _lead2_norm(lead5[i]); b = _lead2_norm(lead5[i + 1])
        a_dev, b_dev = _devoice_katakana(a), _devoice_katakana(b)
        for (fragA, fragB), mk in MAKER_FRAGMENT_RULES:
            if (a.endswith(fragA) and (b.startswith(fragB) or fragB in b)) or \
               (a_dev.endswith(fragA) and (b_dev.startswith(fragB) or fragB in b_dev)):
                return mk, None

    all_join = " ".join([c for c in row if c]).strip()
    all_norm = _norm_jp(_strip_leading_lot_tokens(z2h(all_join)))
    all_dev = _devoice_katakana(all_norm)
    for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
        if mk_norm in all_norm or mk_norm in all_dev:
            rest = all_norm.replace(mk_norm, "").strip() or None
            return mk_orig, rest

    tokens = re.findall(r"[\u30A0-\u30FF\u4E00-\u9FFF]", head_norm)
    for n in (3, 2):
        limit = max(0, len(tokens) - n + 1)
        for i in range(0, min(limit, 6)):
            t = "".join(tokens[i:i + n]); t_dev = _devoice_katakana(t)
            mk = _complete_maker_prefix(t) or _complete_maker_prefix(t_dev)
            if mk:
                return mk, None

    return None, None

def salvage_maker_from_car_name_prefix(v: Dict[str, Any]) -> None:
    name = (v.get("car_name") or "").strip()
    if not name or v.get("maker"):
        return
    name_norm = _norm_jp(z2h(name)); name_dev = _devoice_katakana(name_norm)
    for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
        if name_norm.startswith(mk_norm) or name_dev.startswith(mk_norm):
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

# ==== 行→車両 dict 変換 ====
def coerce_row_to_vehicle(row: List[str], colmap: Dict[int, str]) -> Dict[str, Any]:
    v: Dict[str, Any] = {}
    for i, raw in enumerate(row):
        key = colmap.get(i)
        if not key:
            continue
        s = (raw or "").strip()
        if key == "mileage_km":
            v[key] = parse_mileage_km(s)
            if v[key] is None and s:
                miss("mileage_km", row)
        elif key == "year":
            v[key] = to_int_or_none(s)
            if v[key] is None and s:
                miss("year", row)
        elif key == "start_price_yen":
            ss = z2h(s).replace(",", "")
            m = re.search(r"(-?\d+)", ss)
            v[key] = int(m.group(1)) if m else None
            if v[key] is None and s:
                miss("start_price_yen", row)
        elif key == "displacement_cc":
            v[key] = to_int_or_none(s)
            if v[key] is None and s:
                miss("displacement_cc", row)
        else:
            v[key] = z2h(s)
            if not v[key] and s:
                miss(key, row)
    return v

# ==== 縦展開（セル内改行） ====
def explode_stacked_row(row: List[str]) -> List[List[str]]:
    split_cols: List[List[str]] = [(c or "").splitlines() for c in row]
    max_len = max((len(p) for p in split_cols), default=1)
    if max_len <= 1:
        return [row]
    out: List[List[str]] = []
    for i in range(max_len):
        out.append([(p[i].strip() if i < len(p) else "") for p in split_cols])
    return out

# ==== 断片行の検知とマージ ====
_KATAKANA_RANGE = re.compile(r"^[\u30A0-\u30FF]{1,3}$")

def _is_short_kana_fragment(s: str) -> bool:
    t = unicodedata.normalize("NFKC", (s or "").strip())
    if not t:
        return False
    # 1〜3文字のカタカナ（記号なし）
    return bool(_KATAKANA_RANGE.fullmatch(t))

def _non_empty_count(row: List[str]) -> int:
    return sum(1 for c in row if (c or "").strip())

def is_fragment_row(row: List[str]) -> bool:
    """
    例: ['', 'タ', '', 'ﾊﾟｯｿ 4WD', 'ﾌﾟﾗｽﾊﾅ', '', 'H24', 'KGC35']
    → 'タ' のような短いカタカナ断片を含み、全体の非空セルが少ない行を「断片行」とみなす
    """
    if not row:
        return False
    ne = _non_empty_count(row)
    if ne == 0:
        return False
    # 断片的（非空が3以下）かつ1セル以上に短いカタカナ断片がある
    has_frag = any(_is_short_kana_fragment(c) for c in row)
    if has_frag and ne <= 3:
        return True
    # もう少し緩める: 非空<=4 かつ 断片+年式/型式が同居 → 直前にマージ
    year_like = any(re.search(r"\bH\d{1,2}\b", z2h(c or "")) for c in row)
    model_like = any(re.search(r"[A-Z]{2,}\d{1,}", (c or "")) for c in row)
    return has_frag and (year_like or model_like) and ne <= 4

def merge_fragment_row(prev: List[str], frag: List[str]) -> List[str]:
    """
    セルごとに「prevが空ならfragで埋める」「prevに短カナ断片＋fragで連結」の簡易マージ。
    """
    out: List[str] = prev[:]
    cols = max(len(prev), len(frag))
    if len(out) < cols:
        out += [""] * (cols - len(out))
    frag2 = frag + [""] * (cols - len(frag))

    for i in range(cols):
        a = (out[i] or "").strip()
        b = (frag2[i] or "").strip()
        if not b:
            continue
        if not a:
            out[i] = b
            continue
        # どちらかが短いカナ断片なら結合（例: "トヨ" + "タ" → "トヨタ"）
        if _is_short_kana_fragment(a) or _is_short_kana_fragment(b):
            out[i] = a + b
        # それ以外は、内容が被らなければスペース結合
        elif b not in a:
            out[i] = a + " " + b
    return out

# ==== テキストfallback 用（テーブルがゼロのときのみ使用） ====
def _normalize_text_line(s: str) -> str:
    t = unicodedata.normalize("NFKC", s or "")
    t = re.sub(r"[ \t\u3000]+", " ", t).strip()
    return t

KEY_LINE = re.compile(r"^\s*(?:ｷｰ|キー|KEY)\b", re.IGNORECASE)
LOT_START = re.compile(r"^\s*\d{5}\b")
OPTION_TOKENS = {
    "AW","ナビ","ﾅﾋﾞ","革","B","有","無","I","J","FA","IA","AT","MT","AAC","AC","PS","PW",
    "4WD","2WD","ABS","SRS","HDD","ETC","HID"
}
def _is_option_only(line: str) -> bool:
    toks = [t for t in re.split(r"[ ,]", line) if t]
    if not toks: 
        return False
    good = 0
    for t in toks:
        t_clean = re.sub(r"[^\w一-龥ぁ-んァ-ンｰ\-．\.]", "", t)
        if not t_clean:
            continue
        if t_clean in OPTION_TOKENS:
            good += 1
        elif re.fullmatch(r"[IJ]", t_clean):
            good += 1
        elif re.fullmatch(r"[A-Za-z]{1,3}", t_clean):
            good += 1
        else:
            return False
    return good > 0

def group_records(raw_lines: List[str]) -> List[str]:
    grouped: List[str] = []
    cur: Optional[str] = None
    for idx, raw in enumerate(raw_lines):
        line = _normalize_text_line(raw)
        if not line:
            continue
        if KEY_LINE.match(line):
            trace("skip_key", f"drop line idx={idx}", {"line": line}); continue
        if LOT_START.match(line):
            if cur: grouped.append(cur.strip())
            cur = line
        else:
            if cur is None:
                if _is_option_only(line):
                    trace("drop_orphan_opt", f"idx={idx}", {"line": line})
                    continue
                cur = line
            else:
                cur += " " + line
    if cur:
        grouped.append(cur.strip())
    return grouped

# ==== メイン処理 ====
def parse_auction_sheet(pdf_bytes: bytes, filename: str) -> Dict[str, Any]:
    trace("start", "parse begin", {"file": filename})
    auction_name: Optional[str] = None
    auction_date_val: Optional[date] = None
    vehicles: List[Dict[str, Any]] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        # 会場名・日付抽出
        all_text: List[str] = []
        for idx, page in enumerate(pdf.pages, start=1):
            try:
                txt = page.extract_text() or ""
                all_text.append(txt)
            except Exception as e:
                print(f"[WARN] extract_text failed on page {idx}: {e}")
                all_text.append("")
        joined = "\n".join(all_text)
        m = re.search(r"(USS\s*[\u3040-\u30FF\u4E00-\u9FFF]+|JU\s*[\u3040-\u30FF\u4E00-\u9FFF]+|TAA\s*[\u3040-\u30FF\u4E00-\u9FFF]+)", joined)
        if m:
            auction_name = z2h(m.group(1)).replace(" ", "")
        auction_date_val = parse_auction_date(joined)

        any_table_parsed = False

        for page_no, page in enumerate(pdf.pages, start=1):
            trace("page_start", f"page {page_no}")
            table_settings_candidates = [
                dict(vertical_strategy="lines", horizontal_strategy="lines",
                     snap_tolerance=3, join_tolerance=3, edge_min_length=20, min_words_vertical=1),
                dict(vertical_strategy="text", horizontal_strategy="text",
                     snap_tolerance=8, join_tolerance=8, edge_min_length=10, min_words_vertical=1),
            ]
            tables: List[List[List[str]]] = []
            for ts in table_settings_candidates:
                try:
                    _t = page.extract_tables(table_settings=ts) or []
                    if _t:
                        tables.extend(_t)
                except Exception as e:
                    print(f"[WARN] extract_tables failed on page {page_no}: {e}")

            trace("page_tables", f"page {page_no}", {"tables_found": len(tables)})
            if tables:
                any_table_parsed = True

            for tbl in tables:
                if not tbl or len(tbl) < 2:
                    continue

                # ヘッダ検出
                header_row_idx, header_map, best_cols = 0, {}, 0
                max_check = min(3, len(tbl))
                for i in range(max_check):
                    row = [(c or "").strip() for c in tbl[i]]
                    mapping = normalize_header_row(row)
                    if len(mapping) > best_cols:
                        best_cols, header_map, header_row_idx = len(mapping), mapping, i
                if not header_map:
                    trace("header_skip", f"page {page_no}")
                    continue

                # データ行（セル内改行の縦展開→ノイズ除去→断片行マージ）
                data_rows = tbl[header_row_idx + 1:]
                norm_rows: List[List[str]] = []
                for r in data_rows:
                    base_row = [(c or "").strip() for c in r]
                    for sub in explode_stacked_row(base_row):
                        sub = _strip_noise_row(sub)
                        # 全セル空は除外
                        if not any((c or "").strip() for c in sub):
                            continue
                        # 断片行なら直前へマージ
                        if norm_rows and is_fragment_row(sub):
                            merged = merge_fragment_row(norm_rows[-1], sub)
                            norm_rows[-1] = merged
                            trace("merge_frag", f"merged fragment into previous", {"prev_tail": norm_rows[-1][:6]})
                        else:
                            norm_rows.append(sub)

                # 車両化
                for row in norm_rows:
                    vehicle = coerce_row_to_vehicle(row, header_map)

                    # メーカー救済
                    if not vehicle.get("maker"):
                        mk, car_rest = salvage_maker_from_row_cells(row)
                        if mk:
                            vehicle["maker"] = mk
                            if not vehicle.get("car_name") and car_rest:
                                vehicle["car_name"] = car_rest
                        else:
                            miss("maker", row, stage="salvage")

                    # 車名先頭メーカー分離
                    salvage_maker_from_car_name_prefix(vehicle)

                    # 最低限のキーが無いものは破棄
                    if not any(vehicle.get(k) for k in ("auction_no", "car_name", "maker")):
                        miss("row_discarded", row)
                        continue

                    # デバッグ用の素情報
                    vehicle.setdefault("raw_extracted_json", {})["row"] = row
                    vehicles.append(vehicle)

        # --- フォールバック：テーブル皆無時のみ ---
        if not any_table_parsed and not vehicles:
            lines = []
            for p in all_text:
                lines.extend([ln for ln in (p or "").splitlines()])
            recs = group_records(lines)
            for rec in recs:
                head_norm = _norm_jp(_strip_leading_lot_tokens(z2h(rec)))
                head_dev = _devoice_katakana(head_norm)
                mk_found = None
                for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
                    if mk_norm in head_norm or mk_norm in head_dev:
                        mk_found = mk_orig
                        break
                if mk_found:
                    vehicles.append({
                        "maker": mk_found,
                        "raw_extracted_json": {"text_record": rec}
                    })
                else:
                    miss("maker_fallback", rec[:80], stage="text")

    if not auction_name and "uss" in (filename or "").lower():
        auction_name = "USS"

    trace("parse_done", "end", {"vehicles": len(vehicles)})

    return {
        "file_name": filename,
        "auction_name": auction_name,
        "auction_date": auction_date_val.isoformat() if auction_date_val else None,
        "vehicles": vehicles or [],
    }
