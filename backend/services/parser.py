# backend/services/parser.py
# PDF出品票を解析して AuctionSheetIn 互換の dict を返す実装（pdfplumber版）
# Python 3.9 対応 / 2025-08-21 メーカー抽出の安定化（空行スキップ・断片規則強化・濁点無視・先頭補完スライド）

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
    # 例: 2025/07/31
    m = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d)
        except Exception:
            pass
    # 例: 令和7年7
    m = re.search(r"([Rr令][\s]*)(\d{1,2})[./年](\d{1,2})", t)
    if m:
        era_year = int(m.group(2)); month = int(m.group(3)); day = 1
        base = 2018  # 令和: 2019年=R1 → +2018
        try:
            return date(base + era_year, month, day)
        except Exception:
            pass
    # 例: 平成31年4
    m = re.search(r"[Hh平成][\s]*(\d{1,2})[./年](\d{1,2})", t)
    if m:
        era_year = int(m.group(1)); month = int(m.group(2)); day = 1
        base = 1988  # 平成: 1989年=H1 → +1988
        try:
            return date(base + era_year, month, day)
        except Exception:
            pass
    return None

# 濁点・半濁点を無視した比較用（例: トヨダ→トヨタ扱い）
_DAKUTEN_BASE = str.maketrans(
    "ガギグゲゴザジズゼゾダヂヅデドバビブベボヴパピプペポ",
    "カキクケコサシスセソタチツテトハヒフヘホウハヒフヘホ"
)
def _devoice_katakana(s: str) -> str:
    return (s or "").translate(_DAKUTEN_BASE)

def _norm_jp(s: str) -> str:
    # 半角→全角統一、空白除去、記号ゆれの吸収
    t = unicodedata.normalize("NFKC", s or "")
    t = re.sub(r"\s+", "", t)
    t = t.replace("ｰ", "ー").replace("･", "・").replace("‐", "ー").replace("―", "ー")
    return t

def _strip_leading_lot_tokens(text: str) -> str:
    # 行頭の出品番号などを剥がす（例: "87333トヨ"→"トヨ…"）
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

KNOWN_MAKERS: List[str] = [
    "トヨタ", "ホンダ", "日産", "スズキ", "スバル", "ダイハツ", "マツダ", "三菱",
    "レクサス", "いすゞ", "日野", "三菱ふそう", "メルセデス・ベンツ", "BMW",
    "アウディ", "フォルクスワーゲン", "ボルボ", "プジョー", "シトロエン",
    "ルノー", "ジープ", "フィアット", "アルファロメオ", "ポルシェ", "ミニ",
    "ランドローバー", "ジャガー", "キャデラック", "シボレー", "フォード", "テスラ",
]

# 列割れメーカー断片の復元ルール（例: 「トヨ」「タ」→トヨタ）
MAKER_FRAGMENT_RULES: List[Tuple[Tuple[str, str], str]] = [
    (("トヨ", "タ"), "トヨタ"),
    (("ホン", "ダ"), "ホンダ"),
    (("スバ", "ル"), "スバル"),
    (("ダイ", "ハツ"), "ダイハツ"),
    (("マツ", "ダ"), "マツダ"),
    (("スズ", "キ"), "スズキ"),
]

# 正規化済みの既知メーカー辞書
_KNOWN_MAKERS_NORM: Dict[str, str] = {unicodedata.normalize("NFKC", mk): mk for mk in KNOWN_MAKERS}


# ==== メーカー抽出（サルベージ） ====

def _complete_maker_prefix(token_norm: str) -> Optional[str]:
    # 先頭2〜3文字の完全一致で一意に決まる場合に採用
    cands = [mk_orig for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items() if mk_norm.startswith(token_norm)]
    return cands[0] if len(cands) == 1 else None

def salvage_maker_from_row_cells(row: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    行内（列またぎ含む）からメーカー名と車名の残りを抽出。
    戻り値: (maker or None, car_name_rest or None)
    """
    if not row:
        return None, None
    # 空行は即スキップ
    if not any((c or "").strip() for c in row):
        return None, None

    # 0) 先頭6列の隣接ペアで断片復元（高速）
    first6 = [(c or "") for c in row[:6]]
    def _lead2_norm(s: str) -> str:
        return _norm_jp(z2h(s))[:2]
    for i in range(len(first6) - 1):
        a = _lead2_norm(first6[i])
        b = _lead2_norm(first6[i + 1])
        a_dev, b_dev = _devoice_katakana(a), _devoice_katakana(b)
        for (fragA, fragB), mk in MAKER_FRAGMENT_RULES:
            # 通常比較 or 濁点除去比較のどちらかで一致すれば採用
            if (a.endswith(fragA) and (b.startswith(fragB) or fragB in b)) or \
               (a_dev.endswith(fragA) and (b_dev.startswith(fragB) or fragB in b_dev)):
                return mk, None

    # 1) 先頭6列を結合 → 出品番号剥がし → 既知メーカー包含（濁点無視も併用）
    left_join = "".join([(c or "") for c in row[:6]])
    head = _strip_leading_lot_tokens(z2h(left_join))
    head_norm = _norm_jp(head)
    head_dev = _devoice_katakana(head_norm)
    for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
        if mk_norm in head_norm or mk_norm in head_dev:
            rest = head_norm.replace(mk_norm, "").strip() or None
            return mk_orig, rest

    # 2) 列割れの2分割復元（a列末尾×b列先頭）
    lead5 = [(c or "") for c in row[:5]]
    for i in range(len(lead5) - 1):
        a = _lead2_norm(lead5[i]); b = _lead2_norm(lead5[i + 1])
        a_dev, b_dev = _devoice_katakana(a), _devoice_katakana(b)
        for (fragA, fragB), mk in MAKER_FRAGMENT_RULES:
            if (a.endswith(fragA) and (b.startswith(fragB) or fragB in b)) or \
               (a_dev.endswith(fragA) and (b_dev.startswith(fragB) or fragB in b_dev)):
                return mk, None

    # 3) 行全体の結合でもう一度チェック
    all_join = " ".join([c for c in row if c]).strip()
    all_norm = _norm_jp(_strip_leading_lot_tokens(z2h(all_join)))
    all_dev = _devoice_katakana(all_norm)
    for mk_norm, mk_orig in _KNOWN_MAKERS_NORM.items():
        if mk_norm in all_norm or mk_norm in all_dev:
            rest = all_norm.replace(mk_norm, "").strip() or None
            return mk_orig, rest

    # 4) 先頭トークン（2〜3文字）のスライド補完（濁点無視も試す）
    tokens = re.findall(r"[\u30A0-\u30FF\u4E00-\u9FFF]", head_norm)
    for n in (3, 2):
        limit = max(0, len(tokens) - n + 1)
        for i in range(0, min(limit, 6)):  # 先頭〜6文字範囲を走査
            t = "".join(tokens[i:i + n])
            t_dev = _devoice_katakana(t)
            mk = _complete_maker_prefix(t) or _complete_maker_prefix(t_dev)
            if mk:
                return mk, None

    return None, None

def salvage_maker_from_car_name_prefix(v: Dict[str, Any]) -> None:
    """
    car_name の先頭にメーカー名が含まれている場合に分離して maker を設定。
    """
    name = (v.get("car_name") or "").strip()
    if not name or v.get("maker"):
        return
    name_norm = _norm_jp(z2h(name))
    name_dev = _devoice_katakana(name_norm)
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
    """
    セル内改行を縦展開。列長に合わせて空文字で埋める。
    """
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
        # 全ページのテキストをまとめて会場名と日付を抽出
        all_text: List[str] = []
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

        # 各ページでテーブル抽出
        for page in pdf.pages:
            table_settings_candidates = [
                # 罫線ベース
                dict(vertical_strategy="lines", horizontal_strategy="lines",
                     snap_tolerance=3, join_tolerance=3, edge_min_length=20, min_words_vertical=1),
                # テキスト位置ベース
                dict(vertical_strategy="text", horizontal_strategy="text",
                     snap_tolerance=8, join_tolerance=8, edge_min_length=10, min_words_vertical=1),
            ]
            tables: List[List[List[str]]] = []
            for ts in table_settings_candidates:
                try:
                    _t = page.extract_tables(table_settings=ts) or []
                    if _t:
                        tables.extend(_t)
                except Exception:
                    # このページ・設定は無視して次へ
                    pass

            for tbl in tables:
                if not tbl or len(tbl) < 2:
                    continue

                # 1〜3行目のいずれかをヘッダとして最適化
                header_row_idx, header_map, best_cols = 0, {}, 0
                max_check = min(3, len(tbl))
                for i in range(max_check):
                    row = [(c or "").strip() for c in tbl[i]]
                    mapping = normalize_header_row(row)
                    if len(mapping) > best_cols:
                        best_cols, header_map, header_row_idx = len(mapping), mapping, i
                if not header_map:
                    continue

                # データ行を処理
                data_rows = tbl[header_row_idx + 1:]
                for r in data_rows:
                    base_row = [(c or "").strip() for c in r]
                    # セル内改行を縦展開
                    for row in explode_stacked_row(base_row):
                        # 空行（全セル空/空白のみ）は捨てる
                        if not any((c or "").strip() for c in row):
                            continue

                        vehicle = coerce_row_to_vehicle(row, header_map)

                        # メーカーのサルベージ（列割れ・混入・濁点ずれを吸収）
                        if not vehicle.get("maker"):
                            mk, car_rest = salvage_maker_from_row_cells(row)
                            if mk:
                                vehicle["maker"] = mk
                                if not vehicle.get("car_name") and car_rest:
                                    vehicle["car_name"] = car_rest

                        # 車名先頭にメーカーが含まれる場合は分離
                        salvage_maker_from_car_name_prefix(vehicle)

                        # 最低限のキー（auction_no / car_name / maker）が何も無ければ捨てる
                        if not any(vehicle.get(k) for k in ("auction_no", "car_name", "maker")):
                            continue

                        # 元行の生データを添付（デバッグ観察用）
                        if "raw_extracted_json" not in vehicle:
                            vehicle["raw_extracted_json"] = {"row": row}
                        elif "row" not in vehicle["raw_extracted_json"]:
                            vehicle["raw_extracted_json"]["row"] = row

                        vehicles.append(vehicle)

    # ファイル名からの最終フォールバック（念のため）
    if not auction_name and "uss" in (filename or "").lower():
        auction_name = "USS"

    return {
        "file_name": filename,
        "auction_name": auction_name,
        "auction_date": auction_date_val.isoformat() if auction_date_val else None,
        "vehicles": vehicles or [],
    }
