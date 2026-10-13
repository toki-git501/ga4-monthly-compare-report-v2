#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GA4 CSVパーサー — report_logic.py の parse_ga4_csv() を抽出

v9〜:
- 必須表のセルが `-`・空欄・数値に変換できない値なら、0に置き換えず `issues` に記録する
  （input_validation が生成を止める）
- 同じ表が複数のファイルにある場合は値の一致を確かめ、一致しなければ `issues` に記録する
  （先勝ちで黙って選ばない。別ファイルの同じ表を合算もしない）
- イベントの分類は event_classify.classify_event_counts() に集約（完了到達・未分類・
  同数イベントの二重計測の疑いを分けて保持する）
"""

import calendar
import datetime as _dt
import io
import math
import re

import numpy as np
import pandas as pd

from event_classify import (
    DISPLAY_NAME_MAP,
    RECRUIT_PATTERNS,
    SYSTEM_EVENTS,
    aggregate_event_rows,
    classify_event_counts,
    is_recruit_event,  # noqa: F401  （互換のため再公開）
)


def _decode_text(filepath_or_bytes) -> str:
    """Accept str path / bytes / file-like and decode robustly."""
    if isinstance(filepath_or_bytes, (bytes, bytearray)):
        blob = bytes(filepath_or_bytes)
    elif hasattr(filepath_or_bytes, "read"):
        blob = filepath_or_bytes.read()
        if isinstance(blob, str):
            return blob
        blob = bytes(blob)
    else:
        with open(filepath_or_bytes, "rb") as f:
            blob = f.read()

    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return blob.decode(enc)
        except UnicodeDecodeError:
            continue
    # Last resort: keep processing with replacement chars instead of crashing
    return blob.decode("utf-8", errors="replace")


def _to_numeric_series(series: pd.Series) -> pd.Series:
    """Coerce formatted numbers (commas, %, spaces, placeholders) safely."""
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace(r"\s+", "", regex=True)
        .replace({"-": np.nan, "": np.nan, "nan": np.nan})
    )
    return pd.to_numeric(cleaned, errors="coerce")


# ─────────────────────────────────────────────────────
# キーイベント発生回数の集計ルール（実体は event_classify.py）
# 合計・比較表・グラフはすべて classify_event_counts() の結果を使う（単一ソース）
# ─────────────────────────────────────────────────────
CV_EXCLUDE_EXACT = SYSTEM_EVENTS
CV_RECRUIT_PATTERNS = RECRUIT_PATTERNS
# 互換用の別名（v7まではこのパターンを患者コンバージョンから除外していた）
CV_EXCLUDE_PATTERNS = CV_RECRUIT_PATTERNS
CV_DISPLAY_NAME_MAP = DISPLAY_NAME_MAP

# キーイベント状態: GA4のキーイベント明細が無く、イベント名から候補を判定したもの
KEY_EVENTS_INFERRED = 'event_name_inferred'
KEY_EVENTS_INFERRED_NOTE = 'GA4のキーイベント設定は出力に含まれないため、イベント名から判定'
# プラットフォーム別の合計だけがあり、イベント別の内訳が無い状態
KEY_EVENTS_FALLBACK = 'fallback'
# イベント別の内訳がある（0件を含む）状態
KEY_EVENTS_BREAKDOWN_STATUSES = ('available', KEY_EVENTS_INFERRED, 'configured_zero')

# 複数CSVを結合した入力で、各ファイルの見出しに入るファイル名の行（csv_merge が付ける）
FILE_MARKER_PREFIX = '# 入力ファイル:'


def has_event_breakdown(status) -> bool:
    """イベント別の内訳がある（合計・求人・完了到達などを数えられる）状態か。"""
    return status in KEY_EVENTS_BREAKDOWN_STATUSES


def _is_event_inference_source(title: str) -> bool:
    """イベント名からの判定に使ってよい出力元レポートか。"""
    t = (title or '').strip()
    return t == 'エンゲージメントの概要' or t.startswith('イベント')


def classify_key_events(kev_dict: dict, mode: str = 'available', users: dict | None = None) -> tuple:
    """キーイベント辞書を (患者アクション, 求人関連) の2カテゴリに分ける。

    詳細な分類（完了到達・未分類・同数イベント）は event_classify.classify_event_counts()。
    当月・前月とも同一ルールで分類するため、比較の整合が保証される。
    """
    r = classify_event_counts(kev_dict, mode=mode, users=users)
    return r['patient'], r['recruit']


def extract_cv_events(kev_dict: dict) -> dict:
    """キーイベント辞書から患者の重要アクション（予約・電話等）のみを抽出する。"""
    return classify_key_events(kev_dict)[0]


def extract_recruit_events(kev_dict: dict) -> dict:
    """キーイベント辞書から求人関連イベントのみを抽出する（件数降順）。"""
    return classify_key_events(kev_dict)[1]


# ─────────────────────────────────────────────────────
# 純粋関数（検証用）
# ─────────────────────────────────────────────────────
def parse_yyyymmdd(s):
    """'20260901' → date。不正なら None。"""
    if not isinstance(s, str) or not re.fullmatch(r'\d{8}', s):
        return None
    try:
        return _dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError:
        return None


def period_labels(start: str, end: str) -> dict:
    """期間の表示ラベル。暦月（1日〜末日）でない期間は月名で呼ばず期間で表示する。

    戻り値: {'is_calendar_month', 'month_label', 'period_label'}
      month_label  表の列見出し等に使う短いラベル（暦月: 2026年9月 / それ以外: 9/15〜10/12）
      period_label 年つきの期間（2026/9/15〜2026/10/12）
    """
    s, e = parse_yyyymmdd(start), parse_yyyymmdd(end)
    if not e:
        return {'is_calendar_month': False, 'month_label': '', 'period_label': ''}
    if not s:
        return {'is_calendar_month': False, 'month_label': f'{e.year}年{e.month}月',
                'period_label': ''}
    period = f'{s.year}/{s.month}/{s.day}〜{e.year}/{e.month}/{e.day}'
    last_day = calendar.monthrange(e.year, e.month)[1]
    is_cal = (s.year == e.year and s.month == e.month and s.day == 1 and e.day == last_day)
    if is_cal:
        return {'is_calendar_month': True, 'month_label': f'{e.year}年{e.month}月',
                'period_label': period}
    return {'is_calendar_month': False, 'month_label': f'{s.month}/{s.day}〜{e.month}/{e.day}',
            'period_label': period}


def bad_numeric_cells(df: pd.DataFrame, cols, integer: bool = True) -> list:
    """数値として使えないセルを [(行のキー, 列名, 元の値), ...] で返す。

    `-`・空欄・数値に変換できない値・負数・非有限値、integer=True なら小数も不正とする。
    （実データ・既存フィクスチャには `-` が正当な値として出る箇所は無かったため、すべて不正）
    """
    out = []
    if df is None:
        return out
    for c in cols:
        if c >= len(df.columns):
            continue
        col_name = str(df.columns[c])
        for i, v in enumerate(df.iloc[:, c]):
            s = str(v).strip().replace(',', '')
            ok = True
            try:
                f = float(s)
                if not math.isfinite(f) or f < 0 or (integer and f != int(f)):
                    ok = False
            except ValueError:
                ok = False
            if s == '' or s.lower() == 'nan':
                ok = False
            if not ok:
                out.append((str(df.iloc[i, 0]).strip(), col_name, '' if s.lower() == 'nan' else s))
    return out


def _cell_key(v):
    s = str(v).strip().replace(',', '')
    try:
        f = float(s)
        if math.isfinite(f):
            return round(f, 9)
    except ValueError:
        pass
    return s


def _keyed_values(df: pd.DataFrame, cols: list) -> dict:
    """{先頭列のキー: [指定列の値のタプル, ...]}（同じキーの行は合算せず、そのまま並べる）。"""
    out = {}
    key_col = df.columns[0]
    for _, r in df.iterrows():
        out.setdefault(str(r[key_col]).strip(), []).append(tuple(_cell_key(r[c]) for c in cols))
    return {k: sorted(v, key=repr) for k, v in out.items()}


def _is_sub_multiset(small: list, large: list) -> bool:
    rest = list(large)
    for x in small:
        if x not in rest:
            return False
        rest.remove(x)
    return True


def tables_consistent(df_a: pd.DataFrame, df_b: pd.DataFrame, strict_rows: bool = False) -> bool:
    """同じ表が2つのファイルにある場合に、内容が食い違っていないか。

    同じ見出し名の列どうし・同じキー（先頭列）の行どうしだけを比べる（v9 実データ対応）。
    GA4の画面によって同じ表でも列数（スナップショットの上位ページには直帰率等がある）や
    行数（上位N件の打ち切り）が違うため、行・列の過不足は食い違いにしない。
    - 共通のキーが1つも無い（片方が見出しだけの0件の表を含む）→ 食い違い
    - 共通キー×共通列の値が1つでも違う → 食い違い
    - 同じキーの行が複数ある場合は合算せず、少ない方の行が多い方に含まれるかを見る
      （ユーザー数のように行間で足せない列があるため）
    strict_rows=True（日別表）の場合は、キーの集合も完全に一致することを求める。
    """
    common = [c for c in df_a.columns[1:] if c in set(df_b.columns[1:])]
    if not common:
        return True
    ka, kb = _keyed_values(df_a, common), _keyed_values(df_b, common)
    if strict_rows and set(ka) != set(kb):
        return False
    shared = set(ka) & set(kb)
    if not shared:
        return not ka and not kb
    for k in shared:
        va, vb = ka[k], kb[k]
        small, large = (va, vb) if len(va) <= len(vb) else (vb, va)
        if not _is_sub_multiset(small, large):
            return False
    return True


def table_information_key(df: pd.DataFrame) -> tuple:
    """複数ファイルに同じ表があるとき、どれを採用するかの順位（大きいほど優先）。

    行が多い方（上位N件の打ち切りが少ない方）→ 列が多い方。それでも同じなら内容で決める
    （入力の順序で採用する表が変わらないようにするため）。
    """
    content = tuple(tuple(str(x) for x in row) for row in df.itertuples(index=False))
    return (len(df), len(df.columns), tuple(str(c) for c in df.columns), content)


def weighted_engagement(df_engage: pd.DataFrame, df_active: pd.DataFrame) -> tuple:
    """日別の平均エンゲージメント時間を、同じ日（N 日目）の日別アクティブユーザーで加重平均する。

    行の位置ではなく「N 日目」で結合する。日付の重複・欠け・ずれがあれば (None, 理由) を返す。
    単純平均への黙った切り替えはしない。
    戻り値: (平均秒 or None, 問題の説明 or '')
    """
    if df_engage is None or df_active is None:
        return None, '日別アクティブユーザーが無いため加重平均できません'

    def by_day(df):
        days = _to_numeric_series(df.iloc[:, 0])
        vals = _to_numeric_series(df.iloc[:, 1])
        out, dup = {}, []
        for d, v in zip(days, vals):
            if pd.isna(d):
                return None, ['日付（N 日目）が読めない行']
            d = int(d)
            if d in out:
                dup.append(d)
            out[d] = v
        return out, dup

    eng, dup_e = by_day(df_engage)
    act, dup_a = by_day(df_active)
    if eng is None or act is None:
        return None, '日付（N 日目）が読めない行があります'
    if dup_e or dup_a:
        days = sorted(set(dup_e) | set(dup_a))
        return None, f'同じ日（N 日目={",".join(f"{d:04d}" for d in days[:3])}）の行が複数あります'
    if set(eng) != set(act):
        only_e = sorted(set(eng) - set(act))
        only_a = sorted(set(act) - set(eng))
        parts = []
        if only_e:
            parts.append('平均エンゲージメント時間のみ: ' + ','.join(f'{d:04d}' for d in only_e[:3]))
        if only_a:
            parts.append('日別アクティブユーザーのみ: ' + ','.join(f'{d:04d}' for d in only_a[:3]))
        return None, '日付（N 日目）が揃っていません（' + ' / '.join(parts) + '）'
    num = den = 0.0
    for d, e in eng.items():
        w = act[d]
        if pd.isna(w) or w <= 0 or pd.isna(e):
            continue
        num += float(e) * float(w)
        den += float(w)
    return (num / den if den > 0 else 0.0), ''


def _meta_line(raw: str, key: str):
    """「# key: 値」の行を行単位で読む（値が空なら ''、行が無ければ None）。

    空白は [ \t]* に限定する。\s* だと値が空のとき改行を越えて次の行を値として拾っていた。
    """
    m = re.search(r'^#?[ \t]*' + re.escape(key) + r':[ \t]*([^\r\n]*?)[ \t]*\r?$',
                  raw, re.MULTILINE)
    return m.group(1).strip() if m else None


def read_meta(raw: str) -> dict:
    """アカウント・プロパティ・開始日・終了日（最初に出現した行）を返す。

    property_line は「# プロパティ:」の行があったか（空のプロパティ名と、行が無いことを区別する）。
    """
    account = _meta_line(raw, 'アカウント')
    prop = _meta_line(raw, 'プロパティ')
    start = _meta_line(raw, '開始日')
    end = _meta_line(raw, '終了日')
    return {
        'account': account or '',
        'property': prop or '',
        'property_line': prop is not None,
        'start': start if start and re.fullmatch(r'\d{8}', start) else '',
        'end': end if end and re.fullmatch(r'\d{8}', end) else '',
    }


def expected_day_keys(start: str, end: str):
    """開始日〜終了日から期待する「N 日目」（0始まり）の集合。日付が読めなければ None。"""
    s, e = parse_yyyymmdd(start), parse_yyyymmdd(end)
    if not s or not e or s > e:
        return None
    return set(range((e - s).days + 1))


def _fmt_sec(avg_sec: float) -> str:
    m = int(avg_sec // 60)
    s = int(avg_sec % 60)
    return f"{m}分{s:02d}秒"


def parse_ga4_csv(filepath_or_bytes) -> dict:
    """GA4エクスポートCSVを解析してセクション別データを返す。

    filepath_or_bytes: ファイルパス(str) または bytes/BytesIO

    戻り値の 'issues' は、そのままPDFを作ると誤った数値になる問題（数値の欠損・
    複数ファイル間の表の不一致・日付のずれ）のリスト。input_validation が生成を止める。
    """
    raw = _decode_text(filepath_or_bytes)

    # ヘッダメタ情報取得
    meta = read_meta(raw)

    # GA4のどの画面から出力されたCSVかを識別する。
    # 「集客サマリー」は上位ページやエンゲージメント時間を含まないため、
    # Streamlit側で誤ったPDF生成を止める判定に使う。
    source_report = re.search(
        r'^#\s*(レポートのスナップショット|集客サマリー)\s*$',
        raw,
        re.MULTILINE,
    )
    meta['source_report'] = source_report.group(1) if source_report else ''

    # 日付表示用（暦月でない期間は「9/15〜10/12」のように期間で表示する）
    meta.update(period_labels(meta['start'], meta['end']))

    lines = raw.splitlines()

    # 複数CSVを結合した入力（csv_merge.merge_ga4_csvs）では、各ファイル冒頭の
    # 「# ----」直後の行に出力元レポート名、その次の行にファイル名がある。
    # セクションがどのレポート・どのファイル由来かを判定するため、出現位置を控えておく。
    report_titles = []
    file_markers = []
    for i in range(len(lines)):
        s = lines[i].strip()
        if s.startswith(FILE_MARKER_PREFIX):
            file_markers.append((i, s[len(FILE_MARKER_PREFIX):].strip()))
        if i + 1 < len(lines) and re.match(r'^#\s*-{5,}\s*$', s):
            m = re.match(r'^#\s*(.+?)\s*$', lines[i + 1].strip())
            if m and not re.match(r'^(アカウント|プロパティ)\s*:', m.group(1)) \
                    and not re.match(r'^-{5,}$', m.group(1)) \
                    and not lines[i + 1].strip().startswith(FILE_MARKER_PREFIX):
                report_titles.append((i + 1, m.group(1)))

    def _last_before(items, line_index):
        val = ''
        for idx, name in items:
            if idx < line_index:
                val = name
            else:
                break
        return val

    def section_source(line_index: int) -> str:
        return _last_before(report_titles, line_index)

    def section_file(line_index: int) -> str:
        return _last_before(file_markers, line_index)

    issues = []

    def read_all(header_pattern: str, accept=None) -> list:
        """指定ヘッダ行に一致するCSVブロックをすべて読む。"""
        found = []
        for i, line in enumerate(lines):
            if not re.search(header_pattern, line.strip()):
                continue
            if accept is not None and not accept(i):
                continue
            col_names = [c.strip() for c in line.strip().split(',')]
            block = []
            for row in lines[i + 1:]:
                row = row.strip()
                if not row or row.startswith('#'):
                    break
                block.append(row)
            src = section_file(i) or section_source(i) or '入力CSV'
            if not block:
                # 見出しだけの表は「出力済み0件」。他ファイルの同じ表との一致検査に含める
                found.append({'df': pd.DataFrame(columns=col_names), 'src': src, 'error': False,
                              'empty': True})
                continue
            try:
                df = pd.read_csv(io.StringIO('\n'.join(block)), names=col_names, header=None,
                                 dtype=str, keep_default_na=False)
                found.append({'df': df, 'src': src, 'error': False})
            except Exception:
                found.append({'df': None, 'src': src, 'error': True})
        return found

    def pick_consistent(found: list, label: str, strict_rows: bool = False):
        """同じ表が複数あれば食い違いが無いかを確かめ、情報量の多い表を返す。

        空の表（出力済み0件）も比べる。すべて空の表なら None（見出しだけの表は従来どおり「無い」扱い。キーイベントは
        has_kev_header で「設定済み0件」と判定する）。
        """
        tables = [f for f in found if f['df'] is not None]
        if not tables:
            return None
        # 全ての組で食い違いが無いかを確かめる（行・列の過不足は食い違いにしない）
        for i, a in enumerate(tables):
            for b in tables[i + 1:]:
                if not tables_consistent(a['df'], b['df'], strict_rows=strict_rows):
                    empty_note = ''
                    if a.get('empty') or b.get('empty'):
                        empty_note = '一方は見出しだけ（0件）で、もう一方には行があります。'
                    elif strict_rows:
                        empty_note = '日付（N 日目）の行がそろっていないか、同じ日の値が違います。'
                    else:
                        empty_note = '同じ行・同じ列の値が違います。'
                    issues.append(
                        f'「{label}」の表が複数のファイルにあり、値が一致しません'
                        f'（{a["src"]} と {b["src"]}）。{empty_note}同じ期間・同じ条件で出力したファイルか'
                        '確認してください。どちらかを選んだり合算したりすると数値が変わるため、PDFは生成できません。'
                    )
                    return None if a.get('empty') else a
        if all(t.get('empty') for t in tables):
            return None
        # 採用するのは情報量の多い表（行が多い→列が多い）。入力順には依存しない
        return max((t for t in tables if not t.get('empty')),
                   key=lambda t: table_information_key(t['df']))

    expected_days = expected_day_keys(meta['start'], meta['end'])

    def read_section(header_pattern: str, label: str, accept=None,
                     value_cols=(1,), integer: bool = True, day_key: bool = False,
                     check: bool = True, expect_days: bool = False):
        """表を読み、複数ファイルにあれば一致を確かめ、数値の欠損を記録して返す。"""
        found = read_all(header_pattern, accept)
        for f in found:
            if f['error']:
                issues.append(f'「{label}」（{f["src"]}）の表を読み込めません。')
        first = pick_consistent(found, label, strict_rows=day_key)
        if first is None:
            return None
        df = first['df']
        if check:
            bad = bad_numeric_cells(df, value_cols, integer=integer)
            if day_key:
                bad += bad_numeric_cells(df, (0,), integer=True)
            if bad:
                ex = '、'.join(f'「{k}」の行の{c}「{v}」' for k, c, v in bad[:3])
                more = f' ほか{len(bad) - 3}件' if len(bad) > 3 else ''
                issues.append(
                    f'「{label}」（{first["src"]}）に数値として読めない値があります: {ex}{more}。'
                    '0件として扱うと誤った数値になるため、PDFは生成できません。'
                )
            if day_key and not bad:
                days = _to_numeric_series(df.iloc[:, 0])
                if days.duplicated().any():
                    issues.append(f'「{label}」（{first["src"]}）に同じ日（N 日目）の行が複数あります。')
                elif expect_days and expected_days is not None:
                    # 開始日〜終了日の全日がそろっているか（両方の表から同じ日が欠けても検知する）
                    got = {int(d) for d in days.dropna()}
                    missing = sorted(expected_days - got)
                    extra = sorted(got - expected_days)
                    if missing or extra:
                        parts = []
                        if missing:
                            parts.append('欠けている日: ' + ','.join(f'{d:04d}' for d in missing[:5])
                                         + (f' ほか{len(missing) - 5}日' if len(missing) > 5 else ''))
                        if extra:
                            parts.append('期間外の日: ' + ','.join(f'{d:04d}' for d in extra[:5]))
                        issues.append(
                            f'「{label}」（{first["src"]}）の日付（N 日目）が期間（開始日〜終了日）と'
                            f'合いません（{" / ".join(parts)}）。欠けた日を0件として扱うと誤った数値になるため、'
                            'PDFは生成できません。'
                        )
        # 以降の処理は数値で扱う（検証済み。不正値は NaN）
        for c in value_cols:
            if c < len(df.columns):
                df.isetitem(c, _to_numeric_series(df.iloc[:, c]))
        if day_key:
            df.isetitem(0, _to_numeric_series(df.iloc[:, 0]))
        return df

    def raw_rows(header_pattern: str, label: str, accept=None):
        """イベント表を文字列のまま読む（同名行の扱いを aggregate_event_rows で決めるため）。"""
        found = read_all(header_pattern, accept)
        for f in found:
            if f['error']:
                issues.append(f'「{label}」（{f["src"]}）の表を読み込めません。')
        first = pick_consistent(found, label)
        if first is None:
            return None, None
        df = first['df']
        bad = bad_numeric_cells(df, (1,), integer=True)
        if bad:
            ex = '、'.join(f'「{k}」の行の{c}「{v}」' for k, c, v in bad[:3])
            more = f' ほか{len(bad) - 3}件' if len(bad) > 3 else ''
            issues.append(
                f'「{label}」（{first["src"]}）に数値として読めない値があります: {ex}{more}。'
                '0件として扱うと誤った数値になるため、PDFは生成できません。'
            )
        users_col = None
        for ci, c in enumerate(df.columns):
            if str(c).strip() == '総ユーザー数':
                users_col = ci
        rows = []
        for _, r in df.iterrows():
            cells = tuple(str(x).strip() for x in r.tolist())
            name = cells[0]
            cnt = _to_numeric_series(pd.Series([cells[1]])).iloc[0] if len(cells) > 1 else np.nan
            if pd.isna(cnt) or not name:
                continue
            u = None
            if users_col is not None and users_col < len(cells):
                uv = _to_numeric_series(pd.Series([cells[users_col]])).iloc[0]
                if pd.notna(uv) and float(uv) >= 0 and float(uv) == int(uv):
                    u = int(uv)
            rows.append((name, int(cnt), u, cells))
        return df, rows

    # ─ 各セクション取得 ─
    df_summary = read_section(
        r'^アクティブ ユーザー,新規ユーザー数,アクティブ ユーザーあたりの平均エンゲージメント時間,イベント数$',
        'サマリー', check=False)
    df_active = read_section(r'N 日目,アクティブ ユーザー$', '日別アクティブユーザー', day_key=True,
                             expect_days=True)
    df_new    = read_section(r'N 日目,新規ユーザー数', '日別新規ユーザー', day_key=True,
                             expect_days=True)
    df_engage = read_section(r'N 日目,アクティブ ユーザーあたりの平均エンゲージメント時間',
                             '平均エンゲージメント時間', integer=False, day_key=True,
                             expect_days=True)
    # チャネル: GA4のチャネルグループを優先。無い場合は参照元/メディアで代替する。
    # 参照元/メディアの代替表の値は「アクティブユーザー」であり新規ユーザーではないため、
    # 指標の種類を保持して、新規ユーザーの代わりには使わない（ch_new_metric）。
    ch_new_metric, ch_new_dimension = 'new_users', 'channel_group'
    df_ch_new = read_section(r'ユーザーの最初のメインのチャネル グループ.*新規ユーザー数',
                             'チャネル別新規ユーザー')
    if df_ch_new is None:
        df_ch_new = read_section(r'ユーザーの最初の参照元 / メディア,アクティブ ユーザー$',
                                 '参照元/メディア別アクティブユーザー')
        ch_new_metric, ch_new_dimension = 'active_users', 'source_medium'
    ch_ses_dimension = 'channel_group'
    df_ch_ses = read_section(r'セッションのメインのチャネル グループ.*セッション', 'チャネル別セッション')
    if df_ch_ses is None:
        df_ch_ses = read_section(r'セッションの参照元 / メディア,セッション$', '参照元/メディア別セッション')
        ch_ses_dimension = 'source_medium'
    df_pages  = read_section(r'ページ タイトルとスクリーン クラス,表示回数', '上位ページ')
    df_events = read_section(r'イベント名,イベント数', 'イベント名別イベント数', check=False)
    df_kev, kev_rows = raw_rows(r'^イベント名,キーイベント$', 'キーイベント（イベント名別）')
    df_kev_platform = None
    if df_kev is None:
        df_kev_platform = read_section(r'プラットフォーム,キーイベント$', 'キーイベント（プラットフォーム別）')
    has_kev_header = bool(
        re.search(r'^イベント名,キーイベント[ \t\r]*$', raw, re.MULTILINE)
    )
    has_kev_platform_header = bool(
        re.search(r'^プラットフォーム,キーイベント[ \t\r]*$', raw, re.MULTILINE)
    )
    # キーイベントのイベント別内訳が無い場合に限り、イベント名から候補を判定する。
    # 判定元は「エンゲージメントの概要」または「イベント」レポート由来の
    # 「イベント名,イベント数」に限る（従来のスナップショット単体の挙動は変えない）。
    df_events_for_inference, inference_rows = None, None
    if not has_kev_header:
        df_events_for_inference, inference_rows = raw_rows(
            r'^イベント名,イベント数',
            'イベント名別イベント数',
            accept=lambda i: _is_event_inference_source(section_source(i)),
        )
    # 総ユーザー数（同数イベントの二重計測の確認に使う。「イベント」レポートにのみある）
    users_map, event_count_map = {}, {}
    _, user_rows = raw_rows(r'^イベント名,イベント数,総ユーザー数', 'イベント（総ユーザー数）')
    if user_rows:
        event_count_map, users_map = aggregate_event_rows(user_rows)

    df_trend  = read_section(r'N 日目,30 日,7 日,1 日', '30日・7日・1日アクティブユーザー',
                             value_cols=(1, 2, 3), day_key=True)
    # 国別アクティブユーザー（国 ID 優先、無ければ 国）
    df_country = read_section(r'国 ID,アクティブ ユーザー$', '国別アクティブユーザー', check=False)
    if df_country is None:
        df_country = read_section(r'^国,アクティブ ユーザー$', '国別アクティブユーザー', check=False)

    # ─ 国別サマリ（海外アクセスの有無を正確に把握するため） ─
    # 注意: KPI(セッション/ユーザー)はチャネル別・日別集計から算出されるため、
    # このCSVからは国別での再集計はできない。ここでは「海外が含まれているか」
    # 「JP比率がどの程度か」を注記に使うためだけに国別内訳を読み取る。
    country_info = {
        'available': False,
        'total': 0,
        'jp': 0,
        'overseas': 0,
        'jp_share': None,
        'overseas_present': False,
    }
    if df_country is not None and not df_country.empty:
        code_col = df_country.columns[0]
        val_col = df_country.columns[1]
        codes = df_country[code_col].astype(str).str.strip()
        vals = _to_numeric_series(df_country[val_col]).fillna(0)
        total = int(vals.sum())
        jp_mask = codes.str.upper().isin(['JP', 'JAPAN', '日本'])
        jp = int(vals[jp_mask].sum())
        overseas = max(0, total - jp)
        country_info = {
            'available': True,
            'total': total,
            'jp': jp,
            'overseas': overseas,
            'jp_share': (jp / total) if total > 0 else None,
            'overseas_present': overseas > 0,
        }
    meta['country'] = country_info

    # ─ 計測期間の完全性チェック（前月比を「参考値」とすべきか判定） ─
    # 日別アクティブユーザーの先頭が連続ゼロの場合、月の途中から計測開始した
    # 可能性が高く、月初〜その日までが未計測。前月比の分母が不完全になる。
    measure_info = {
        'incomplete': False,
        'leading_zero_days': 0,
        'measured_from_day': 1,
        'total_days': 0,
        'measured_days': 0,
    }
    if df_active is not None and not df_active.empty:
        active_vals = _to_numeric_series(df_active.iloc[:, 1]).fillna(0)
        total_days = int(len(active_vals))
        leading_zeros = 0
        for v in active_vals:
            if v <= 0:
                leading_zeros += 1
            else:
                break
        measured_days = int((active_vals > 0).sum())
        # 先頭ゼロが5日以上、または計測日が全体の半分未満なら不完全とみなす
        incomplete = (leading_zeros >= 5) or (
            total_days > 0 and measured_days < total_days * 0.5
        )
        measure_info = {
            'incomplete': bool(incomplete),
            'leading_zero_days': leading_zeros,
            'measured_from_day': leading_zeros + 1 if leading_zeros > 0 else 1,
            'total_days': total_days,
            'measured_days': measured_days,
        }
    meta['measure'] = measure_info

    # ─ KPI集計 ─
    kpis = {}

    # 月間アクティブユーザー: 30日間アクティブユーザーの期間末値を月間値の近似として採用。
    # 旧実装にあった「日別値の単純合計」フォールバックは同一ユーザーの重複計上で
    # 過大な値になるため廃止（サマリ行の実測値があればそちらを使う）。
    if df_trend is not None and '30 日' in df_trend.columns:
        trend_num = _to_numeric_series(df_trend['30 日'])
        kpis['active_users'] = int(trend_num.iloc[-1]) if not trend_num.empty and pd.notna(trend_num.iloc[-1]) else 0
    elif df_summary is not None and not df_summary.empty:
        val = _to_numeric_series(pd.Series([df_summary.iloc[0, 0]])).iloc[0]
        kpis['active_users'] = int(val) if pd.notna(val) else 0
    else:
        kpis['active_users'] = 0

    # 新規ユーザー: 日別新規ユーザーの合計。チャネル別の代替は「新規ユーザー」の表に限る
    # （参照元/メディア別のアクティブユーザーを新規ユーザーとして足さない）。
    if df_new is not None:
        kpis['new_users'] = int(_to_numeric_series(df_new.iloc[:, 1]).sum())
    elif df_ch_new is not None and ch_new_metric == 'new_users':
        kpis['new_users'] = int(_to_numeric_series(df_ch_new.iloc[:, 1]).sum())
    elif df_summary is not None and not df_summary.empty:
        val = _to_numeric_series(pd.Series([df_summary.iloc[0, 1]])).iloc[0]
        kpis['new_users'] = int(val) if pd.notna(val) else 0
    else:
        kpis['new_users'] = 0

    if df_ch_ses is not None:
        kpis['sessions'] = int(_to_numeric_series(df_ch_ses.iloc[:, 1]).sum())
    else:
        kpis['sessions'] = 0

    # 平均エンゲージメント時間: 日別値を同じ日（N 日目）の日別アクティブユーザーで加重平均する。
    # 行位置での対応づけ・単純平均への黙った切り替えは v9 で廃止。
    if df_engage is not None:
        avg_sec, problem = weighted_engagement(df_engage, df_active)
        if problem and df_active is not None:
            issues.append(f'平均エンゲージメント時間を日別アクティブユーザーで加重平均できません: {problem}。')
        if avg_sec is None:
            kpis['avg_engage_sec'] = 0
            kpis['avg_engage_str'] = '取得不能'
        else:
            kpis['avg_engage_sec'] = float(avg_sec)
            kpis['avg_engage_str'] = _fmt_sec(float(avg_sec))
    elif df_summary is not None and not df_summary.empty:
        avg_sec = _to_numeric_series(pd.Series([df_summary.iloc[0, 2]])).iloc[0]
        if pd.isna(avg_sec):
            avg_sec = 0
        kpis['avg_engage_sec'] = float(avg_sec)
        kpis['avg_engage_str'] = _fmt_sec(float(avg_sec))
    else:
        kpis['avg_engage_sec'] = 0
        kpis['avg_engage_str'] = '-'

    # キーイベント（患者アクション＝key_events / 求人関連＝key_events_recruit ほか）
    # classify_event_counts() の結果を全ページの単一ソースとする。
    # key_events_total は合計に入れる予約・電話等のみ（求人関連・完了到達・未分類・
    # 同数のため除外したイベントは含めない）。
    classification = None
    if kev_rows is not None:
        counts, _ = aggregate_event_rows(kev_rows)
        kpis['key_events_raw'] = counts
        classification = classify_event_counts(counts, mode='available', users=users_map,
                                              event_counts=event_count_map)
        kpis['key_events_status'] = 'available'
    elif inference_rows is not None:
        # GA4のキーイベント設定（どのイベントがキーイベントか）は出力に含まれないため、
        # イベント名から判定する。GA4のキーイベント明細（available）とは集計方法が異なるため、
        # 別状態として扱い、available との前月比較は行わない（compare_utils.key_events_comparison）。
        counts, inf_users = aggregate_event_rows(inference_rows)
        merged_users = dict(users_map)
        for k, v in inf_users.items():
            if v is not None:
                merged_users[k] = v
        kpis['key_events_raw'] = counts
        classification = classify_event_counts(counts, mode='inferred', users=merged_users)
        kpis['key_events_status'] = KEY_EVENTS_INFERRED
    elif df_kev_platform is not None:
        # イベント名の明細が無い場合は、予約・電話等と求人・その他を分けられない。
        # プラットフォーム合計は別の値（key_events_platform_total）として保持し、
        # 予約・電話アクションの件数としては使わない（内訳取得不能）。
        kev_vals = _to_numeric_series(df_kev_platform.iloc[:, 1])
        kpis['key_events_raw'] = {}
        kpis['key_events_platform_total'] = int(kev_vals.fillna(0).sum())
        kpis['key_events_status'] = KEY_EVENTS_FALLBACK
    elif has_kev_header or has_kev_platform_header:
        # ヘッダーがありデータ行が無い場合は「設定済み0件」として扱う。
        # セクション自体が無い「未出力」と区別し、誤った前月比を防ぐ。
        kpis['key_events_raw'] = {}
        kpis['key_events_status'] = 'configured_zero'
    else:
        kpis['key_events_raw'] = {}
        kpis['key_events_status'] = 'missing'

    if classification is None:
        classification = classify_event_counts({})
    kpis['key_events'] = classification['patient']
    kpis['key_events_total'] = sum(kpis['key_events'].values())
    # 求人関連イベント（別カテゴリ）。key_events_total には含めない。
    kpis['key_events_recruit'] = classification['recruit']
    kpis['key_events_recruit_total'] = sum(kpis['key_events_recruit'].values())
    # 完了到達（外部予約サイト等の完了画面）。予約成立数ではなく、クリックの後工程のため合計に含めない
    kpis['key_events_completion'] = classification['completion']
    kpis['key_events_completion_total'] = sum(kpis['key_events_completion'].values())
    kpis['key_events_completion_base'] = classification['completion_base']
    kpis['key_events_completion_counted'] = classification['completion_counted']
    # イベント名から予約・電話等と判定できなかったイベント（イベント名から判定の場合のみ）
    kpis['key_events_unclassified'] = classification['unclassified']
    kpis['key_events_unclassified_total'] = sum(kpis['key_events_unclassified'].values())
    # 同数のため合計から除外したイベント（同じ操作を2つのイベントで数えている疑い）
    kpis['key_events_dedup_excluded'] = classification['dedup_excluded']
    kpis['key_events_dedup_groups'] = classification['dedup_groups']
    kpis['key_events_dedup_unverified'] = classification['dedup_unverified']
    kpis.setdefault('key_events_platform_total', None)

    return {
        'meta': meta,
        'kpis': kpis,
        'issues': issues,
        'ch_new_metric': ch_new_metric,
        'ch_new_dimension': ch_new_dimension,
        'ch_ses_dimension': ch_ses_dimension,
        'df_active': df_active,
        'df_new': df_new,
        'df_engage': df_engage,
        'df_ch_new': df_ch_new,
        'df_ch_ses': df_ch_ses,
        'df_pages':  df_pages,
        'df_events': df_events,
        'df_kev':    df_kev,
        'df_trend':  df_trend,
        'df_country': df_country,
    }
