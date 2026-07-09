#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GA4 CSVパーサー — report_logic.py の parse_ga4_csv() を抽出
"""

import re
import io
import pandas as pd
import numpy as np

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
# キーイベント（コンバージョン）集計ルール
# 合計・比較表・グラフ・広告CV推計はすべて extract_cv_events() の結果を使う（単一ソース）
# ─────────────────────────────────────────────────────
# GA4の自動収集・システム系イベント（患者の成果行動ではないため集計から除外）
CV_EXCLUDE_EXACT = {
    'tap', 'click', 'scroll', 'page_view', 'session_start', 'first_visit',
    'user_engagement', 'view_search_results', 'file_download', 'download',
    'video_start', 'video_progress', 'video_complete',
    'form_start', 'view_promotion', 'select_promotion',
}
# 患者コンバージョンから除外する名称パターン（採用・求人系）
CV_EXCLUDE_PATTERNS = ('求人', '採用', 'recruit', 'saiyo')

# 既知イベントの表示名（未知のイベントは元の名称のまま表示する）
CV_DISPLAY_NAME_MAP = {
    'web予約':                          'WEB予約',
    'web予約_sp小児ページ':              'WEB予約（小児SP）',
    '小児web予約_sp固定タブ_':            'WEB予約（小児タブ）',
    '成人電話':                          '電話（成人）',
    '小児電話':                          '電話（小児）',
    '小児電話_小児ページからのアクセス_':    '電話（小児ページ）',
    'スマホ_tel':                        '電話（スマホ）',
    '問診票_成人':                       '問診票（成人）',
    '問診票ダウンロード_成人_':            '問診票DL（成人）',
    '問診票_小児':                       '問診票（小児）',
}


def extract_cv_events(kev_dict: dict) -> dict:
    """キーイベント辞書から患者コンバージョンのみを抽出する。

    - tap/click 等のシステムイベント・求人系イベントを除外
    - 表示名へ変換し、件数降順の辞書を返す
    - 当月・前月とも同一ルールで抽出するため、比較の整合が保証される
    """
    merged = {}
    for name, count in (kev_dict or {}).items():
        raw_name = str(name).strip()
        low = raw_name.lower()
        if low in CV_EXCLUDE_EXACT:
            continue
        if any(p in low for p in CV_EXCLUDE_PATTERNS):
            continue
        try:
            n = int(count)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        display = CV_DISPLAY_NAME_MAP.get(low, raw_name)
        merged[display] = merged.get(display, 0) + n
    return dict(sorted(merged.items(), key=lambda x: (-x[1], x[0])))


def parse_ga4_csv(filepath_or_bytes) -> dict:
    """GA4エクスポートCSVを解析してセクション別データを返す。

    filepath_or_bytes: ファイルパス(str) または bytes/BytesIO
    """
    raw = _decode_text(filepath_or_bytes)

    # ヘッダメタ情報取得
    account = re.search(r'アカウント:\s*(.+)', raw)
    prop    = re.search(r'プロパティ:\s*(.+)', raw)
    start_d = re.search(r'開始日:\s*(\d{8})', raw)
    end_d   = re.search(r'終了日:\s*(\d{8})', raw)

    meta = {
        'account':  account.group(1).strip() if account else '',
        'property': prop.group(1).strip() if prop else '',
        'start':    start_d.group(1) if start_d else '',
        'end':      end_d.group(1) if end_d else '',
    }

    # 日付表示用
    if meta['end']:
        d = meta['end']
        meta['month_label'] = f"{d[:4]}年{int(d[4:6])}月"
    else:
        meta['month_label'] = ''

    def read_section(header_pattern: str):
        """指定ヘッダ行以降のCSVブロックを読み込む"""
        lines = raw.splitlines()
        start_i = None
        col_names = None
        for i, line in enumerate(lines):
            if re.search(header_pattern, line.strip()):
                col_names = [c.strip() for c in line.strip().split(',')]
                start_i = i + 1
                break
        if start_i is None:
            return None
        block = []
        for line in lines[start_i:]:
            line = line.strip()
            if not line or line.startswith('#'):
                break
            block.append(line)
        if not block:
            return None
        try:
            return pd.read_csv(
                io.StringIO('\n'.join(block)),
                names=col_names,
                header=None,
            )
        except Exception:
            return None

    # ─ 各セクション取得 ─
    df_summary = read_section(r'^アクティブ ユーザー,新規ユーザー数,アクティブ ユーザーあたりの平均エンゲージメント時間,イベント数$')
    df_active = read_section(r'N 日目,アクティブ ユーザー$')
    df_new    = read_section(r'N 日目,新規ユーザー数')
    df_engage = read_section(r'N 日目,アクティブ ユーザーあたりの平均エンゲージメント時間')
    df_ch_new = read_section(r'ユーザーの最初のメインのチャネル グループ.*新規ユーザー数')
    df_ch_ses = read_section(r'セッションのメインのチャネル グループ.*セッション')
    if df_ch_new is None:
        df_ch_new = read_section(r'ユーザーの最初の参照元 / メディア,アクティブ ユーザー$')
    if df_ch_ses is None:
        df_ch_ses = read_section(r'セッションの参照元 / メディア,セッション$')
    df_pages  = read_section(r'ページ タイトルとスクリーン クラス,表示回数')
    df_events = read_section(r'イベント名,イベント数')
    df_kev    = read_section(r'イベント名,キーイベント')
    df_kev_platform = None
    if df_kev is None:
        df_kev_platform = read_section(r'プラットフォーム,キーイベント$')
    df_trend  = read_section(r'N 日目,30 日,7 日,1 日')

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

    if df_new is not None:
        kpis['new_users'] = int(_to_numeric_series(df_new.iloc[:, 1]).sum())
    elif df_ch_new is not None:
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

    if df_engage is not None:
        # 日別平均の単純平均はユーザー数の少ない日を過大評価するため、
        # 日別アクティブユーザー数による加重平均を採用（取得不可時は単純平均）
        eng_vals = _to_numeric_series(df_engage.iloc[:, 1])
        weights = None
        if df_active is not None and len(df_active) == len(df_engage):
            weights = _to_numeric_series(df_active.iloc[:, 1])
        avg_sec = None
        if weights is not None:
            valid = eng_vals.notna() & weights.notna() & (weights > 0)
            if valid.any() and weights[valid].sum() > 0:
                avg_sec = float((eng_vals[valid] * weights[valid]).sum() / weights[valid].sum())
        if avg_sec is None:
            avg_sec = eng_vals.mean()
        if pd.isna(avg_sec):
            avg_sec = 0
        kpis['avg_engage_sec'] = float(avg_sec)
        m = int(avg_sec // 60)
        s = int(avg_sec % 60)
        kpis['avg_engage_str'] = f"{m}分{s:02d}秒"
    elif df_summary is not None and not df_summary.empty:
        avg_sec = _to_numeric_series(pd.Series([df_summary.iloc[0, 2]])).iloc[0]
        if pd.isna(avg_sec):
            avg_sec = 0
        kpis['avg_engage_sec'] = float(avg_sec)
        m = int(avg_sec // 60)
        s = int(avg_sec % 60)
        kpis['avg_engage_str'] = f"{m}分{s:02d}秒"
    else:
        kpis['avg_engage_sec'] = 0
        kpis['avg_engage_str'] = '-'

    # キーイベント（患者コンバージョンのみ）
    # 旧実装は tap 等のシステムイベント・求人系まで合算しており過大だったため、
    # extract_cv_events() で除外・表示名統一した結果を全ページの単一ソースとする。
    if df_kev is not None:
        kev_raw = {}
        for _, row in df_kev.iterrows():
            value = _to_numeric_series(pd.Series([row.iloc[1]])).iloc[0]
            kev_raw[str(row.iloc[0])] = int(value) if pd.notna(value) else 0
        kpis['key_events_raw'] = kev_raw
        kpis['key_events'] = extract_cv_events(kev_raw)
        kpis['key_events_total'] = sum(kpis['key_events'].values())
    elif df_kev_platform is not None:
        # イベント名の明細が無い場合はシステムイベントを除外できないため、
        # プラットフォーム合計をそのまま使う（過大の可能性あり・明細出力を推奨）
        kev_vals = _to_numeric_series(df_kev_platform.iloc[:, 1])
        kpis['key_events_raw'] = {}
        kpis['key_events'] = {}
        kpis['key_events_total'] = int(kev_vals.sum())
    else:
        kpis['key_events_raw'] = {}
        kpis['key_events'] = {}
        kpis['key_events_total'] = 0

    return {
        'meta': meta,
        'kpis': kpis,
        'df_active': df_active,
        'df_new': df_new,
        'df_engage': df_engage,
        'df_ch_new': df_ch_new,
        'df_ch_ses': df_ch_ses,
        'df_pages':  df_pages,
        'df_events': df_events,
        'df_kev':    df_kev,
        'df_trend':  df_trend,
    }
