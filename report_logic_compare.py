#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GA4 月次比較レポート PDF生成 — CompareReportCanvas (7ページ)
"""

import os
import io
import re
import datetime as _dt
import pandas as pd
# 日本語フォント: リポジトリ同梱の ipaexg.ttf を直接登録する
# （japanize-matplotlib は Python 3.12+ で distutils 削除により動作しないため不使用）
import matplotlib
from matplotlib import font_manager as _fm
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_JP_FONT = os.path.join(_BASE_DIR, 'ipaexg.ttf')
if os.path.exists(LOCAL_JP_FONT):
    _fm.fontManager.addfont(LOCAL_JP_FONT)
    matplotlib.rcParams['font.family'] = 'IPAexGothic'
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image as PILImage

from csv_parser import (
    parse_ga4_csv, KEY_EVENTS_INFERRED, KEY_EVENTS_INFERRED_NOTE, KEY_EVENTS_FALLBACK,
    has_event_breakdown,
)
from event_classify import is_reservation_or_phone

# 出力PDFに刻む生成バージョン。
# デプロイが反映されたかを、出力物そのもので判定できるようにするため。
# ロジックを変更したら必ず更新すること。
REPORT_VERSION = '2026-10-13-v9'
from compare_utils import (
    calc_diff, format_delta, delta_color,
    compare_kpis, compare_dataframes, compare_key_events, compute_highlights,
    select_top_pages, key_events_comparison, incomparable_text, shorten_labels_unique,
    treatment_mismatch_events,
)
from chart_generator_compare import (
    make_daily_overlay_chart, make_channel_grouped_bar,
    make_key_events_grouped_bar, make_top_pages_compare_chart,
    make_engagement_overlay_chart, make_new_vs_returning_compare,
    make_cohort_chart, make_reservation_compare_chart,
    make_reservation_transition_rate_chart, make_paid_channels_grouped_bar,
)

# ─────────────────────────────────────────────────────
# フォント設定
# ─────────────────────────────────────────────────────
FONT_CANDIDATES = [
    LOCAL_JP_FONT,
    '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
    '/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc',
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
]
JP_FONT = None
for font_path in FONT_CANDIDATES:
    if os.path.exists(font_path):
        try:
            pdfmetrics.registerFont(TTFont('JP', font_path))
            JP_FONT = 'JP'
            break
        except Exception:
            continue
if JP_FONT is None:
    try:
        pdfmetrics.registerFont(UnicodeCIDFont('HeiseiKakuGo-W5'))
        JP_FONT = 'HeiseiKakuGo-W5'
    except Exception:
        JP_FONT = 'Helvetica'

# デザインカラー
COLOR_BG        = colors.HexColor('#EEF3F8')
COLOR_PRIMARY   = colors.HexColor('#1A6FAB')
COLOR_SECONDARY = colors.HexColor('#4DA3D4')
COLOR_DARK      = colors.HexColor('#1E2B3A')
COLOR_GRAY      = colors.HexColor('#8A9BB0')
COLOR_WHITE     = colors.white
COLOR_CARD_BG   = colors.HexColor('#FFFFFF')
COLOR_LIGHT     = colors.HexColor('#F5F8FC')

# 比較用追加カラー
COLOR_INCREASE  = colors.HexColor('#2E8B57')
COLOR_DECREASE  = colors.HexColor('#D9534F')
COLOR_PREV_DATA = colors.HexColor('#B0D4F1')

PAGE_W, PAGE_H = A4[1], A4[0]  # landscape A4


# ─────────────────────────────────────────────────────
# データ連動の注記テキスト生成
# ─────────────────────────────────────────────────────
def country_note_text(meta: dict, compact: bool = False) -> str:
    """国別データに基づく正確な対象範囲の注記を返す。

    このCSVからはKPIの国別再集計ができないため、
    「海外が含まれているか」を事実として明示する（虚偽の『海外除外済み』は使わない）。
    """
    c = (meta or {}).get('country') or {}
    if not c.get('available'):
        return ('※ 国別データが取得できませんでした。GA4側で国フィルタを適用していない場合、'
                '海外・ボット等のアクセスが少量含まれることがあります。')
    share = c.get('jp_share')
    if c.get('overseas_present'):
        # 母数を明示する。この比率の分母はGA4の国別内訳の合計であり、
        # KPIカードの「30日アクティブユーザー」とは集計方法が異なるため一致しない。
        # 分母を書かないと、KPIカードの値で割って計算が合わない（100%超になる）読者を生む。
        if share is not None:
            jp = c.get('jp') or 0
            total = c.get('total') or 0
            if compact:
                # 用語解説ページなど行幅の限られる箇所用。分母は示すが説明は省く。
                share_txt = f'（国別内訳の国内JPは約{share * 100:.0f}%・{jp:,}/{total:,}人）'
            else:
                share_txt = (f'（当月の国別内訳では国内JPが約{share * 100:.0f}%・{jp:,}/{total:,}人。'
                             'この分母はKPIカードの「30日アクティブユーザー」とは集計方法が異なります）')
        else:
            share_txt = ''
    if not c.get('overseas_present'):
        return '※ 本レポートの対象期間は、国内（日本／JP）からのアクセスが100%でした。'
    return ('※ 本レポートは国フィルタ未適用のため、海外・ボット等のアクセスが少量含まれます'
            f'{share_txt}。国内のみで集計する場合はGA4側で国フィルタを適用し再エクスポートしてください。')


def rolling_window_note(cur_meta: dict, prev_meta: dict) -> str:
    """「30日アクティブユーザー」の実際の集計窓を明示する注記を返す。

    この指標はGA4「アクティブ ユーザーの傾向」の30日列の期間末値であり、
    期間末日から遡る直近30日間の値。暦月が31日ある月では、暦月の初日が
    集計窓から外れるため、暦月の集計とは一致しない。
    値は変えず、何を見ている数字なのかを読み手に開示する。
    """
    def _window(meta):
        end = (meta or {}).get('end') or ''
        if len(end) != 8:
            return None, False
        try:
            end_d = _dt.date(int(end[:4]), int(end[4:6]), int(end[6:]))
        except ValueError:
            return None, False
        start_d = end_d - _dt.timedelta(days=29)
        start_meta = (meta or {}).get('start') or ''
        aligned = start_meta == start_d.strftime('%Y%m%d')
        return f'{start_d.month}/{start_d.day}–{end_d.month}/{end_d.day}', aligned

    cur_w, cur_aligned = _window(cur_meta)
    prev_w, prev_aligned = _window(prev_meta)
    if not cur_w or not prev_w:
        return ''
    if cur_aligned and prev_aligned:
        # 両月とも暦月＝集計窓。注記不要。
        return ''
    return ('※「30日アクティブユーザー」は期間末時点の直近30日間の値です'
            f'（当月 {cur_w} / 前月 {prev_w}）。31日ある月は暦月の初日が集計窓から外れるため、'
            '暦月そのものの集計とは一致せず、前月比は参考値です。')


def incomplete_note_text(cur_meta: dict, prev_meta: dict,
                         cur_label: str, prev_label: str,
                         single_mode: bool = False) -> str:
    """計測期間が不完全な月がある場合、前月比を参考値とする注記を返す（無ければ空文字）。"""
    notes = []
    prev_m = (prev_meta or {}).get('measure') or {}
    cur_m = (cur_meta or {}).get('measure') or {}
    if (not single_mode) and prev_m.get('incomplete'):
        untracked = max(0, int(prev_m.get('measured_from_day', 1)) - 1)
        notes.append(
            f'前月（{prev_label}）は月初〜{untracked}日目が未計測（{prev_m.get("measured_from_day", 1)}日目以降のみ計測）'
            'のため、前月比の増減率は参考値です。'
        )
    if cur_m.get('incomplete'):
        untracked = max(0, int(cur_m.get('measured_from_day', 1)) - 1)
        notes.append(
            f'当月（{cur_label}）は月初〜{untracked}日目が未計測の可能性があり、数値が過小になる場合があります。'
        )
    return '　'.join(notes)


def key_events_status_text(kpis: dict) -> str:
    """キーイベント発生回数の表示文字列。未出力・内訳取得不能は0件と書かない。"""
    kpis = kpis or {}
    status = kpis.get('key_events_status', 'missing')
    if status == 'missing':
        return '未出力'
    if status == KEY_EVENTS_FALLBACK:
        return '内訳取得不能'
    total = int(kpis.get('key_events_total', 0))
    if status == KEY_EVENTS_INFERRED:
        return f'{total:,}件（イベント名から判定）'
    return f'{total:,}件'


def key_events_inferred_note(cur_kpis: dict, prev_kpis: dict) -> str:
    """イベント名から判定した月がある場合の注記（無ければ空文字）。"""
    cur_inf = (cur_kpis or {}).get('key_events_status') == KEY_EVENTS_INFERRED
    prev_inf = (prev_kpis or {}).get('key_events_status') == KEY_EVENTS_INFERRED
    if not (cur_inf or prev_inf):
        return ''
    if cur_inf and prev_inf:
        scope = ''
    elif cur_inf:
        scope = '当月は'
    else:
        scope = '前月は'
    return (f'※ キーイベント: {scope}{KEY_EVENTS_INFERRED_NOTE}'
            '（tap・click等の自動計測は除外。求人関連は予約・電話等と分けて別集計）。')


def has_recruit_events(*kpis_list) -> bool:
    """いずれかの月に求人関連イベントがあるか。"""
    return any(int((k or {}).get('key_events_recruit_total', 0)) > 0 for k in kpis_list)


def _status_count_text(kpis: dict, total_key: str) -> str:
    """別集計（求人関連・完了到達・未分類）の件数表示。内訳が無い月は0件と書かない。"""
    kpis = kpis or {}
    status = kpis.get('key_events_status', 'missing')
    if status == 'missing':
        return '未出力'
    if not has_event_breakdown(status):
        return '取得不能'
    return f'{int(kpis.get(total_key, 0)):,}件'


def _side_counts_text(cur_kpis, prev_kpis, total_key, single_mode=False) -> str:
    cur_txt = _status_count_text(cur_kpis, total_key)
    if single_mode:
        return f'当月 {cur_txt}'
    prev_txt = _status_count_text(prev_kpis, total_key)
    comparable, reason = key_events_comparison(
        (cur_kpis or {}).get('key_events_status'), (prev_kpis or {}).get('key_events_status'))
    if comparable:
        cur_t = int((cur_kpis or {}).get(total_key, 0))
        prev_t = int((prev_kpis or {}).get(total_key, 0))
        delta = format_delta(calc_diff(cur_t, prev_t))
        return f'当月 {cur_txt} / 前月 {prev_txt}（{delta}）'
    return f'当月 {cur_txt} / 前月 {prev_txt}（比較不能）'


def recruit_events_note(cur_kpis: dict, prev_kpis: dict, single_mode: bool = False) -> str:
    """KPIサマリ（P2）用: 求人関連イベントを別集計したことの注記（無ければ空文字）。

    求人関連は計測対象だが、予約・電話アクションの合計（バナーの値）には含めない。
    前月が未出力の場合は「0件」ではなく「未出力」と書く。
    """
    if not has_recruit_events(cur_kpis, prev_kpis):
        return ''
    counts = _side_counts_text(cur_kpis, prev_kpis, 'key_events_recruit_total', single_mode)
    return f'※ 求人関連のイベント（求人応募ボタン・求人サイトへのリンク等）は上記に含めず別集計: {counts}'


def completion_events_note(cur_kpis: dict, prev_kpis: dict, single_mode: bool = False) -> str:
    """完了到達（外部予約サイト等の完了画面）を合計に含めず別に示したことの注記。"""
    if not any(int((k or {}).get('key_events_completion_total', 0)) > 0
               for k in (cur_kpis, prev_kpis)):
        return ''
    counts = _side_counts_text(cur_kpis, prev_kpis, 'key_events_completion_total', single_mode)
    return ('※ 外部の予約サイト等の完了画面に到達した回数（イベント名の末尾が「完了」等のもの）は、'
            '予約成立数ではなく、クリックの後の段階を数えたものです。上記に含めず別に示しています: '
            f'{counts}')


def completion_counted_note(cur_kpis: dict, prev_kpis: dict) -> str:
    """対応するクリックのイベントが無いため、完了到達を合計に含めた場合の注記。"""
    names = []
    for k in (cur_kpis, prev_kpis):
        for n in (k or {}).get('key_events_completion_counted') or []:
            if n not in names:
                names.append(n)
    if not names:
        return ''
    joined = '」「'.join(names[:3])
    return (f'※「{joined}」は対応するクリックのイベントが無く、これが唯一の計測のため合計に含めています'
            '（予約成立数ではありません）。')


def unclassified_events_note(cur_kpis: dict, prev_kpis: dict, single_mode: bool = False) -> str:
    """イベント名から判定できなかったイベントを合計に含めていないことの注記。"""
    if not any(int((k or {}).get('key_events_unclassified_total', 0)) > 0
               for k in (cur_kpis, prev_kpis)):
        return ''
    names = []
    for k in (cur_kpis, prev_kpis):
        for n in ((k or {}).get('key_events_unclassified') or {}):
            if n not in names:
                names.append(n)
    ex = '・'.join(names[:3]) + ('等' if len(names) > 3 else '')
    counts = _side_counts_text(cur_kpis, prev_kpis, 'key_events_unclassified_total', single_mode)
    return (f'※ イベント名から予約・電話等と判定できなかったイベント（{ex}）は上記に含めていません: '
            f'{counts}')


def _group_label(g: dict) -> str:
    names = [g['kept']] + list(g['excluded'])
    return 'と'.join(f'「{n}」' for n in names)


def dedupe_note_text(cur_kpis: dict, prev_kpis: dict, single_mode: bool = False,
                     with_mark: bool = True) -> str:
    """同数イベントの一方を合計から除外したことの注記（無ければ空文字）。"""
    def groups(k):
        return [(g['kept'], tuple(g['excluded'])) for g in (k or {}).get('key_events_dedup_groups') or []]

    cur_g = groups(cur_kpis)
    prev_g = [] if single_mode else groups(prev_kpis)
    items = []
    for g in cur_g:
        items.append((g, '' if (single_mode or g in prev_g) else '当月は'))
    for g in prev_g:
        if g not in cur_g:
            items.append((g, '前月は'))
    if not items:
        return ''
    parts = []
    for (kept, excluded), scope in items:
        label = _group_label({'kept': kept, 'excluded': excluded})
        parts.append(f'{scope}同数の{label}')
    head = '※ ' if with_mark else ''
    return (head + '、'.join(parts) + 'は、同じ操作を2つのイベントで数えている可能性があるため、'
            '合計には1つ分のみ含めています。')


def dedupe_unverified_note(cur_kpis: dict, prev_kpis: dict, single_mode: bool = False) -> str:
    """件数は同じだが総ユーザー数が無く確かめられない組の注記（合計からは除外しない）。"""
    seen = []
    for k in ((cur_kpis,) if single_mode else (cur_kpis, prev_kpis)):
        for u in (k or {}).get('key_events_dedup_unverified') or []:
            label = 'と'.join(f'「{n}」' for n in u['names'])
            if label not in seen:
                seen.append(label)
    if not seen:
        return ''
    return ('※ ' + '、'.join(f'同数の{x}' for x in seen)
            + 'は、同じ操作を2つのイベントで数えている可能性がありますが、総ユーザー数が出力されて'
            'いないため確かめられず、どちらも合計に含めています（「イベント」レポートのCSVを追加すると確認できます）。')


def platform_total_note(cur_kpis: dict, prev_kpis: dict, single_mode: bool = False) -> str:
    """イベント別の内訳が無く、プラットフォーム合計しか無い月がある場合の注記。"""
    months = [('当月', cur_kpis)] + ([] if single_mode else [('前月', prev_kpis)])
    fb = [(lab, k) for lab, k in months if (k or {}).get('key_events_status') == KEY_EVENTS_FALLBACK]
    if not fb:
        return ''
    totals = '・'.join(f"{lab} {int(k.get('key_events_platform_total') or 0):,}件" for lab, k in fb)
    return ('※ キーイベントのイベント別の内訳が出力されていないため、予約・電話アクションの件数は'
            f'算出していません（GA4のプラットフォーム別キーイベント合計: {totals}。求人やその他の'
            'イベントを含むため、予約・電話の件数としては使えません）。「エンゲージメントの概要」または'
            '「イベント」のCSVを追加してください。')


def side_event_items(cur_d: dict, prev_d: dict, cur_status: str, prev_status: str,
                     single_mode: bool = False) -> list:
    """別表（求人関連・完了到達・未分類）の行。

    比較できる月どうしは compare_key_events と同じ形式。前月が比較できない場合も
    当月の明細は表示し、前月の値は「未出力」等、増減は「比較不能」とする。
    当月にイベント別の内訳が無い場合は空。
    """
    if not has_event_breakdown(cur_status):
        return []
    comparable, reason = key_events_comparison(cur_status, prev_status)
    if single_mode or comparable:
        return compare_key_events(cur_d or {}, prev_d or {})
    prev_text = {'missing': '未出力', 'breakdown_unavailable': '取得不能'}.get(reason, '—')
    items = [{'name': k, 'current': int(v), 'previous': None, 'prev_text': prev_text}
             for k, v in (cur_d or {}).items()]
    items.sort(key=lambda x: (-x['current'], str(x['name'])))
    return items


# P4 のレイアウト上限
P4_MAX_EXCLUDED_ROWS = 3        # 左表に残す「同数のため合計から除外」の行（超過分は1行に集約）
P4_BOTTOM_LIMIT = 12 * mm        # 別表・注記をこれより下に描かない（フッター線は10mm）


def allocate_side_rows(item_counts: list, capacity: int):
    """右側の別表どうしで、明細行の数（小計を除く）を分け合う。

    各表に最低1行、残りを行の多い表から順に1行ずつ配る。capacity が表の数に満たなければ None。
    割り当てが明細数より少ない表は、最後の1行が「他N件」の集約行になる。
    """
    n = len(item_counts)
    if n == 0:
        return []
    if capacity < n:
        return None
    alloc = [1] * n
    left = capacity - n
    while left > 0:
        need = [i for i in range(n) if alloc[i] < item_counts[i]]
        if not need:
            break
        for i in sorted(need, key=lambda k: (alloc[k], -item_counts[k], k)):
            if left <= 0:
                break
            alloc[i] += 1
            left -= 1
    return [min(a, max(c, 1)) for a, c in zip(alloc, item_counts)]


def fold_items(items: list, max_rows: int) -> tuple:
    """max_rows 行に収める: (表示する行, 「他N件」に集約する行)。集約行も1行に数える。"""
    items = list(items)
    if max_rows <= 0 or len(items) <= max_rows:
        return items, []
    keep = max(max_rows - 1, 0)
    return items[:keep], items[keep:]


def side_table_summary_text(table: dict, cur_label: str, prev_label: str) -> str:
    """別表が収まらないときの1文（小計の当月・前月を落とさない）。"""
    items = table['items']
    cur_t = sum(int(i['current']) for i in items)
    if any(i.get('previous') is None for i in items):
        prev_txt = next((i.get('prev_text') for i in items if i.get('previous') is None), '—')
        prev_part = f'{prev_label} {prev_txt}（比較不能）'
    else:
        prev_part = f"{prev_label} {sum(int(i['previous']) for i in items):,}件"
    return (f"{table['subtotal']}: {cur_label} {cur_t:,}件 / {prev_part}"
            f"（{len(items)}種。{table['note'].lstrip('※ ')}）")


def treatment_mismatch_note(cur_kpis: dict, prev_kpis: dict, single_mode: bool = False) -> str:
    """片方の月だけ合計から外したイベントがあり、合計・小計の前月比を出さないことの注記。"""
    if single_mode:
        return ''
    comparable, _ = key_events_comparison((cur_kpis or {}).get('key_events_status'),
                                          (prev_kpis or {}).get('key_events_status'))
    if not comparable:
        return ''
    mm_list = treatment_mismatch_events(cur_kpis, prev_kpis)
    if not mm_list:
        return ''
    parts = []
    for m in mm_list[:3]:
        cur_txt = '含め' if m['cur_included'] else '含めず'
        prev_txt = '含めた' if m['prev_included'] else '含めなかった'
        parts.append(f'「{m["name"]}」は当月は合計に{cur_txt}、前月は{prev_txt}')
    more = f'（ほか{len(mm_list) - 3}件）' if len(mm_list) > 3 else ''
    return ('※ ' + '、'.join(parts) + more
            + 'ため（同数の別イベントがある・完了画面への到達として別に数えた、の扱いが月で異なる）、'
            '合計・小計の前月比は出していません。当月・前月の値は、それぞれの月の規則で集計したものです。')


# P4 右側の別表の定義（左表の合計・参考比率には含めない）
COMPLETION_SIDE_TABLE = {
    'kind': 'completion',
    'title': '完了画面への到達',
    'subtotal': '完了到達 小計',
    'note': '※ 外部の予約サイト等の完了画面に到達した回数（イベント名の末尾が「完了」等）。'
            '予約成立数ではありません。クリックの後の段階のため左表の合計に含めず、'
            'クリック件数からも差し引いていません。',
}
RECRUIT_SIDE_TABLE = {
    'kind': 'recruit',
    'title': '求人関連イベント',
    'subtotal': '求人関連 小計',
    'note': '※ 求人関連: 求人応募ボタン・求人サイトへのリンク等（イベント名に「求人」「採用」'
            '「recruit」「まいなび」等を含むもの）。左表の合計・参考比率には含めず別集計。',
}
UNCLASSIFIED_SIDE_TABLE = {
    'kind': 'unclassified',
    'title': '未分類のイベント',
    'subtotal': '未分類 小計',
    'note': '※ イベント名から予約・電話等と判定できなかったイベント（その他のイベント・未分類）。'
            '左表の合計・参考比率には含めていません。',
}


def build_side_tables(cur_kpis: dict, prev_kpis: dict, single_mode: bool = False) -> list:
    """P4 右側の別表（完了到達・求人関連・未分類）を作る。行が無い表は含めない。"""
    cur_status = (cur_kpis or {}).get('key_events_status', 'missing')
    prev_status = (prev_kpis or {}).get('key_events_status', 'missing')
    tables = []
    for spec, key in ((COMPLETION_SIDE_TABLE, 'key_events_completion'),
                      (RECRUIT_SIDE_TABLE, 'key_events_recruit'),
                      (UNCLASSIFIED_SIDE_TABLE, 'key_events_unclassified')):
        items = side_event_items((cur_kpis or {}).get(key) or {}, (prev_kpis or {}).get(key) or {},
                                 cur_status, prev_status, single_mode)
        if items:
            tables.append(dict(spec, items=items))
    return tables


class CompareReportCanvas:
    """月次比較レポート用キャンバス（7ページ構成）"""

    def __init__(self, filepath: str):
        self.c = canvas.Canvas(filepath, pagesize=(PAGE_W, PAGE_H))
        self.c.setTitle('GA4 月次比較レポート')

    def save(self):
        self.c.save()

    def new_page(self):
        self.c.showPage()

    # ─── 基本描画メソッド ───

    def _bg(self, color=None):
        clr = color or COLOR_BG
        self.c.setFillColor(clr)
        self.c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    def _text(self, x, y, text, font_size=10, color=COLOR_DARK, align='left'):
        self.c.setFillColor(color)
        self.c.setFont(JP_FONT, font_size)
        if align == 'center':
            self.c.drawCentredString(x, y, str(text))
        elif align == 'right':
            self.c.drawRightString(x, y, str(text))
        else:
            self.c.drawString(x, y, str(text))

    @staticmethod
    def _wrap_lines(text, max_width, font_size, max_lines=4):
        """max_width(pt) に収まるよう日本語文字列を折り返した行リストを返す。

        描画前に行数を測れるようにするため、分割だけを行う。
        日本語は単語境界がないため1文字単位で幅を測って折る。
        """
        text = str(text)
        if not text:
            return []
        lines, cur = [], ''
        for ch in text:
            trial = cur + ch
            if pdfmetrics.stringWidth(trial, JP_FONT, font_size) > max_width and cur:
                lines.append(cur)
                cur = ch
                if len(lines) >= max_lines:
                    return lines
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines

    def _text_wrapped(self, x, y, text, max_width, font_size=10,
                      color=COLOR_DARK, line_gap=1.35, max_lines=4):
        """max_width(pt) を超えたら折り返して描画し、使った行数を返す。

        従来 `_text` は1行で描き切るため、注記が長いと右端で文字が切れていた
        （表紙の国フィルタ注記、総括ページの次月アクションで発生）。
        """
        lines = self._wrap_lines(text, max_width, font_size, max_lines)
        if not lines:
            return 0
        self.c.setFillColor(color)
        self.c.setFont(JP_FONT, font_size)
        for i, line in enumerate(lines):
            self.c.drawString(x, y - i * font_size * line_gap, line)
        return len(lines)

    def _rect(self, x, y, w, h, fill_color=COLOR_PRIMARY, stroke_color=None, radius=3):
        self.c.setFillColor(fill_color)
        if stroke_color:
            self.c.setStrokeColor(stroke_color)
            self.c.roundRect(x, y, w, h, radius, fill=1, stroke=1)
        else:
            self.c.setStrokeColor(fill_color)
            self.c.roundRect(x, y, w, h, radius, fill=1, stroke=0)

    def _line(self, x1, y1, x2, y2, color=COLOR_GRAY, width=0.5):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(x1, y1, x2, y2)

    def _image_bytes(self, img_bytes, x, y, w, h=None):
        buf = io.BytesIO(img_bytes)
        pil_img = PILImage.open(buf)
        pw, ph = pil_img.size
        aspect = ph / pw
        actual_h = h if h else w * aspect
        buf.seek(0)
        ir = ImageReader(buf)
        self.c.drawImage(ir, x, y - actual_h, width=w, height=actual_h,
                         preserveAspectRatio=True, mask='auto')

    def _footer(self, company_name='', page_num=1):
        self._line(10 * mm, 10 * mm, PAGE_W - 10 * mm, 10 * mm,
                   color=COLOR_GRAY, width=0.4)
        self._text(12 * mm, 7 * mm, company_name, font_size=7, color=COLOR_GRAY)
        self._text(PAGE_W - 12 * mm, 7 * mm, str(page_num), font_size=7,
                   color=COLOR_GRAY, align='right')

    def _section_header(self, x, y, title: str, subtitle: str = ''):
        self._rect(x, y - 1 * mm, 3.5 * mm, 7 * mm, fill_color=COLOR_PRIMARY)
        self._text(x + 5 * mm, y + 3 * mm, title, font_size=13, color=COLOR_DARK)
        if subtitle:
            self._text(x + 5 * mm, y - 1 * mm, subtitle, font_size=8, color=COLOR_GRAY)

    def _missing_data_notice(self, x, y, w, h, text='計測データ未出力'):
        self.c.setFillColor(colors.HexColor('#FFF7E8'))
        self.c.setStrokeColor(colors.HexColor('#F0C36D'))
        self.c.roundRect(x, y - h, w, h, 3, fill=1, stroke=1)
        self._text(x + 4 * mm, y - (h / 2) + 1.5 * mm, text, font_size=9, color=colors.HexColor('#7A5A1F'))

    def _page_title_bar(self, title: str, right_text: str = ''):
        self._rect(0, PAGE_H - 14 * mm, PAGE_W, 14 * mm, fill_color=COLOR_PRIMARY)
        self._text(12 * mm, PAGE_H - 9 * mm, title, font_size=14, color=COLOR_WHITE)
        if right_text:
            self._text(PAGE_W - 12 * mm, PAGE_H - 9 * mm, right_text,
                       font_size=9, color=COLOR_WHITE, align='right')

    # ─── 比較専用描画メソッド ───

    def _delta_text(self, x, y, diff_dict: dict, font_size=10):
        """増減テキスト描画（色分け付き）"""
        text = format_delta(diff_dict)
        clr = colors.HexColor(delta_color(diff_dict['direction']))
        self._text(x, y, text, font_size=font_size, color=clr)

    def _comparison_table(self, x, y, headers: list, rows: list, col_widths: list,
                          row_h=7 * mm):
        """比較テーブル描画。rows = [[cell, cell, ...], ...]

        行は dict でもよい: {'cells': [...], 'muted': True, 'span': (列番号, 文字列)}
        muted は灰色の文字、span は指定列から右端までを1つのセルとして左寄せで描く
        （同数のため合計から除外した行などに使う）。
        """
        total_w = sum(col_widths)
        text_dy = 2 * mm if row_h >= 7 * mm else 1.6 * mm

        for ri, row in enumerate([headers] + rows):
            row_y = y - ri * row_h
            is_header = ri == 0
            spec = row if isinstance(row, dict) else {'cells': row}
            cells = spec['cells']

            bg = COLOR_PRIMARY if is_header else \
                (COLOR_CARD_BG if ri % 2 == 1 else colors.HexColor('#F5F8FC'))
            self.c.setFillColor(bg)
            self.c.rect(x, row_y - row_h, total_w, row_h, fill=1, stroke=0)

            txt_color = COLOR_WHITE if is_header else (
                COLOR_GRAY if spec.get('muted') else COLOR_DARK)
            span_col, span_text = spec.get('span') or (None, None)
            cx = x
            for ci, (cell, cw) in enumerate(zip(cells, col_widths)):
                if span_col is not None and ci >= span_col:
                    break
                align = 'right' if ci >= 1 and not is_header else 'left'
                pad = 3 * mm if align == 'left' else -3 * mm
                font_size = 8
                if is_header:
                    # 見出し（期間ラベル等）が列幅を超えて隣の列と重ならないよう縮める
                    room = cw - 4 * mm
                    while font_size > 5.5 and pdfmetrics.stringWidth(str(cell), JP_FONT, font_size) > room:
                        font_size -= 0.25
                self._text(cx + (cw + pad if align == 'right' else pad),
                           row_y - row_h + text_dy, str(cell),
                           font_size=font_size, color=txt_color, align=align)
                cx += cw
            if span_col is not None:
                sx = x + sum(col_widths[:span_col])
                self._text(sx + 3 * mm, row_y - row_h + text_dy, str(span_text),
                           font_size=7.5, color=COLOR_GRAY)

    def _kpi_comparison_card(self, x, y, w, h, label: str,
                             cur_val: str, prev_val: str, diff_dict: dict, unit: str = ''):
        """KPI比較カード"""
        # 白カード
        self.c.setFillColor(COLOR_CARD_BG)
        self.c.setStrokeColor(colors.HexColor('#DDE6EF'))
        self.c.roundRect(x, y - h, w, h, 4, fill=1, stroke=1)

        # アクセントバー
        self._rect(x, y - 2.5 * mm, w, 2.5 * mm, fill_color=COLOR_PRIMARY, radius=0)

        # ラベル
        self._text(x + w / 2, y - 7 * mm, label, font_size=7, color=COLOR_GRAY, align='center')

        # 当月値
        self._text(x + w / 2, y - 15 * mm, cur_val, font_size=14, color=COLOR_PRIMARY, align='center')

        # 単位
        if unit:
            self._text(x + w / 2, y - 19.5 * mm, unit, font_size=6, color=COLOR_GRAY, align='center')

        # 前月値
        self._text(x + w / 2, y - 24 * mm, f'前月: {prev_val}',
                   font_size=6, color=COLOR_GRAY, align='center')

        # 増減
        delta_str = format_delta(diff_dict)
        clr = colors.HexColor(delta_color(diff_dict['direction']))
        self._text(x + w / 2, y - 29 * mm, delta_str,
                   font_size=7, color=clr, align='center')

    # ─── 7ページの描画メソッド ───

    def draw_cover(self, cur_meta: dict, prev_meta: dict,
                   company_name: str = '', department: str = '', logo_path: str = None,
                   single_mode: bool = False, site_url: str = '',
                   country_note: str = '', incomplete_note: str = ''):
        """P1: 表紙（比較期間表示）"""
        self._bg(COLOR_BG)

        if logo_path and os.path.exists(str(logo_path)):
            try:
                self.c.drawImage(logo_path, 15 * mm, PAGE_H - 25 * mm,
                                 width=52.5 * mm, height=15 * mm,
                                 preserveAspectRatio=True, mask='auto')
            except Exception:
                pass

        client_label = cur_meta.get('property', '').replace(' - GA4', '')
        self._text(27 * mm, PAGE_H * 0.62,
                   client_label + '　御中', font_size=27, color=COLOR_DARK)

        self._text(27 * mm, PAGE_H * 0.48,
                   'GA4 月次比較レポート', font_size=42, color=COLOR_DARK)

        # 比較期間
        cur_label = cur_meta.get('month_label', '')
        prev_label = prev_meta.get('month_label', '')
        # 暦月でない期間（例 9/15〜10/12）は月名で呼ばず、年つきの期間で表示する
        if not cur_meta.get('is_calendar_month', True) and cur_meta.get('period_label'):
            cur_label = cur_meta['period_label']
        if not prev_meta.get('is_calendar_month', True) and prev_meta.get('period_label'):
            prev_label = prev_meta['period_label']
        if single_mode or (prev_label and cur_label and prev_label == cur_label):
            period_text = f'{cur_label}'
        else:
            period_text = f'{prev_label}  →  {cur_label}'
        self._text(27 * mm, PAGE_H * 0.38, period_text, font_size=20, color=COLOR_GRAY)

        # 対象サイトURL
        if site_url:
            self._text(27 * mm, PAGE_H * 0.33, f'対象サイト: {site_url}',
                       font_size=12, color=COLOR_PRIMARY)

        self._line(27 * mm, PAGE_H * 0.30, 150 * mm, PAGE_H * 0.30,
                   color=COLOR_PRIMARY, width=2.0)

        bottom_x = PAGE_W - 20 * mm
        self._text(bottom_x, PAGE_H * 0.2 + 9 * mm,
                   company_name, font_size=15, color=COLOR_DARK, align='right')
        if department:
            self._text(bottom_x, PAGE_H * 0.2,
                       department, font_size=15, color=COLOR_DARK, align='right')

        # 計測期間が不完全な場合の警告バンド（表紙で最初に目に入る位置）
        if incomplete_note:
            band_h = 12 * mm
            band_y = PAGE_H * 0.2 - 4 * mm
            self.c.setFillColor(colors.HexColor('#FFF7E8'))
            self.c.setStrokeColor(colors.HexColor('#F0C36D'))
            self.c.roundRect(27 * mm, band_y - band_h, 200 * mm, band_h, 3, fill=1, stroke=1)
            self._text(30 * mm, band_y - band_h / 2 - 1 * mm,
                       f'⚠ {incomplete_note}',
                       font_size=7.5, color=colors.HexColor('#7A5A1F'))

        # 注釈（国別データに基づく正確な対象範囲）
        note = country_note or '※ 本レポートのデータはGA4からエクスポートされたCSVに基づいています。'
        # 1行描画では右端で文字が切れていたため折り返す
        self._text_wrapped(27 * mm, PAGE_H * 0.12, note,
                           max_width=PAGE_W - 27 * mm - 20 * mm,
                           font_size=8, color=COLOR_GRAY, max_lines=3)

        self._footer(company_name, 1)

    def draw_summary_compare(self, cur_meta, prev_meta, cur_kpis, prev_kpis,
                             compared_kpis, daily_chart_bytes, company_name='',
                             incomplete_note='', single_mode: bool = False):
        """P2: KPI比較サマリ（元の単月版レイアウト準拠）"""
        self._bg(COLOR_LIGHT)

        period = ''
        if cur_meta.get('start') and cur_meta.get('end'):
            s, e = cur_meta['start'], cur_meta['end']
            period = f"{s[:4]}/{s[4:6]}/{s[6:]} ─ {e[:4]}/{e[4:6]}/{e[6:]}"
        self._page_title_bar('KPI 比較サマリ', period)

        # KPIカード（4枚）— 元のレイアウト準拠
        card_y = PAGE_H - 22 * mm
        card_h = 28 * mm
        margin = 10 * mm
        # 「月間アクティブユーザー」は誤称だった。実体はGA4「30日アクティブユーザー」の
        # 期間末値（＝期間末日から遡る直近30日）であり、暦月の集計ではない。
        # 値は変えずラベルのみ実体に合わせる（集計窓は下部の注記で明示）。
        card_labels = ['30日アクティブユーザー', '新規ユーザー', 'セッション数', '平均エンゲージメント時間']
        card_cur_values = [
            f"{cur_kpis['active_users']:,}",
            f"{cur_kpis['new_users']:,}",
            f"{cur_kpis['sessions']:,}",
            cur_kpis['avg_engage_str'],
        ]
        card_prev_values = [
            f"{prev_kpis['active_users']:,}",
            f"{prev_kpis['new_users']:,}",
            f"{prev_kpis['sessions']:,}",
            prev_kpis['avg_engage_str'],
        ]
        card_units = ['人', '人', '件', '']
        card_cmp_keys = ['active_users', 'new_users', 'sessions', 'avg_engage_sec']

        num_cards = 4
        total_gap = (num_cards - 1) * 4 * mm
        card_w = (PAGE_W - 2 * margin - total_gap) / num_cards

        for i in range(num_cards):
            cx = margin + i * (card_w + 4 * mm)
            # 白カード
            self.c.setFillColor(COLOR_CARD_BG)
            self.c.setStrokeColor(colors.HexColor('#DDE6EF'))
            self.c.roundRect(cx, card_y - card_h, card_w, card_h, 4, fill=1, stroke=1)
            # アクセントバー
            self._rect(cx, card_y - 3 * mm, card_w, 3 * mm,
                       fill_color=COLOR_PRIMARY, radius=0)
            # ラベル
            self._text(cx + card_w / 2, card_y - 8 * mm,
                       card_labels[i], font_size=8, color=COLOR_GRAY, align='center')
            # 当月値
            self._text(cx + card_w / 2, card_y - 16 * mm,
                       card_cur_values[i], font_size=18, color=COLOR_PRIMARY, align='center')
            # 単位 + 前月
            self._text(cx + card_w / 2, card_y - 21 * mm,
                       card_units[i], font_size=8, color=COLOR_GRAY, align='center')
            # 増減
            diff = compared_kpis[card_cmp_keys[i]]
            delta_str = format_delta(diff)
            clr = colors.HexColor(delta_color(diff['direction']))
            self._text(cx + card_w / 2, card_y - 26 * mm,
                       f"前月{card_prev_values[i]}  {delta_str}",
                       font_size=7, color=clr, align='center')

        # キーイベント発生回数バナー。未出力を0件として比較しない。
        banner_y = card_y - card_h - 6 * mm
        banner_w = PAGE_W - 24 * mm
        self._rect(12 * mm, banner_y - 12 * mm, banner_w, 12 * mm,
                   fill_color=COLOR_PRIMARY, radius=3)
        kev_diff = compared_kpis['key_events_total']
        if kev_diff.get('incomparable_reason') == 'treatment_mismatch':
            # 両月とも件数はあるが、合計に含めるイベントが月で異なる → 値は出し、差は出さない
            kev_label = ('キーイベント発生回数（イベント名から判定）'
                         if cur_kpis.get('key_events_status') == KEY_EVENTS_INFERRED
                         else 'キーイベント発生回数')
            banner_text = (
                f"{kev_label}: {cur_kpis['key_events_total']:,}件 "
                f"（前月{prev_kpis['key_events_total']:,}件 比較不能）"
            )
        elif kev_diff.get('comparable') is False:
            cur_text = key_events_status_text(cur_kpis)
            prev_text = key_events_status_text(prev_kpis)
            reason = {
                'method_mismatch': '集計方法が異なるため比較不能',
                'breakdown_unavailable': '内訳が無いため比較不能',
            }.get(kev_diff.get('incomparable_reason'), '比較不能')
            if single_mode:
                banner_text = f'キーイベント発生回数: 当月 {cur_text}'
            else:
                banner_text = f'キーイベント発生回数: 当月 {cur_text} / 前月 {prev_text}（{reason}）'
        else:
            kev_delta = format_delta(kev_diff)
            kev_label = ('キーイベント発生回数（イベント名から判定）'
                         if cur_kpis.get('key_events_status') == KEY_EVENTS_INFERRED
                         else 'キーイベント発生回数')
            banner_text = (
                f"{kev_label}: {cur_kpis['key_events_total']:,}件 "
                f"（前月{prev_kpis['key_events_total']:,}件 {kev_delta}）"
            )
        self._text(PAGE_W / 2, banner_y - 6 * mm, banner_text,
                   font_size=12, color=COLOR_WHITE, align='center')

        # バナー下の注記。いずれも行数を測って折り返し、その分グラフを下げる。
        # 1行目の注記はバナー下端（banner_y - 12mm）から文字高さ分離す。
        # -13mm だと文字の上端がバナーに隠れていた。
        notes = []
        fallback_note = platform_total_note(cur_kpis, prev_kpis, single_mode)
        if fallback_note:
            notes.append((fallback_note, colors.HexColor('#B5791F')))
        for text in (
            treatment_mismatch_note(cur_kpis, prev_kpis, single_mode),
            key_events_inferred_note(cur_kpis, prev_kpis),
            # 同数の2イベントは1つ分のみ合計に含める（同じ操作の重複計測の疑い）
            dedupe_note_text(cur_kpis, prev_kpis, single_mode),
            dedupe_unverified_note(cur_kpis, prev_kpis, single_mode),
            # 完了到達（予約成立数ではない）・求人関連・未分類は合計に混ぜず別集計で示す
            completion_events_note(cur_kpis, prev_kpis, single_mode),
            completion_counted_note(cur_kpis, prev_kpis),
            recruit_events_note(cur_kpis, prev_kpis, single_mode),
            unclassified_events_note(cur_kpis, prev_kpis, single_mode),
        ):
            if text:
                notes.append((text, COLOR_GRAY))
        if incomplete_note:
            notes.append((f'⚠ {incomplete_note}', colors.HexColor('#B5791F')))
        # 30日アクティブユーザーの集計窓の注記（暦月と一致しない月のみ表示）
        window_note = rolling_window_note(cur_meta, prev_meta)
        if window_note:
            notes.append((window_note, COLOR_GRAY))

        chart_offset = 0
        note_y = banner_y - 15 * mm
        for text, clr in notes:
            used = self._text_wrapped(12 * mm, note_y, text,
                                      max_width=PAGE_W - 24 * mm,
                                      font_size=7, color=clr, max_lines=2)
            chart_offset += max(used, 1) * 3.6 * mm + 0.6 * mm
            note_y -= max(used, 1) * 3.6 * mm + 0.6 * mm
        if chart_offset:
            chart_offset += 2 * mm  # 注記の開始位置を下げた分

        # 日別ユーザー推移グラフ（バナーの下、フッターの上に画面いっぱい）
        if daily_chart_bytes:
            chart_top = banner_y - 14 * mm - chart_offset
            chart_w = PAGE_W - 16 * mm  # 左右8mmマージン
            chart_h = chart_top - 14 * mm  # フッター上端まで
            self._image_bytes(daily_chart_bytes, 8 * mm, chart_top, chart_w, h=chart_h)

        self._footer(company_name, 2)

    def draw_channels_compare(self, channel_chart_bytes, compared_channels,
                              cur_label, prev_label, company_name='',
                              channel_mismatch: bool = False,
                              cur_dimension: str = '', prev_dimension: str = ''):
        """P3: チャネル比較"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('流入チャネル比較')

        content_top = PAGE_H - 30 * mm

        if channel_mismatch:
            names = {'channel_group': 'チャネルグループ', 'source_medium': '参照元/メディア'}
            self._missing_data_notice(
                12 * mm, content_top - 2 * mm, PAGE_W - 24 * mm, 10 * mm,
                f'チャネル比較不能（当月: {names.get(cur_dimension, "不明")} / '
                f'前月: {names.get(prev_dimension, "不明")}）。'
                '月によって流入元の分類方法が異なるため、チャネル別の増減は比較していません。'
            )

        # チャネル比較グラフ
        if channel_chart_bytes:
            self._section_header(10 * mm, content_top, 'チャネル別セッション数', '当月 vs 前月')
            self._image_bytes(channel_chart_bytes, 10 * mm, content_top - 5 * mm,
                              PAGE_W * 0.55, h=75 * mm)

        # 比較テーブル
        if not compared_channels.empty:
            table_x = PAGE_W * 0.6
            table_y = content_top - 5 * mm
            key_col = compared_channels.columns[0]

            rename_map = {
                'Organic Search': 'オーガニック検索',
                'Paid Search':    '有料検索(広告)',
                'Direct':         'ダイレクト',
                'Referral':       '参照元',
                'Organic Social': 'オーガニックSNS',
                'Unassigned':     '未割り当て',
                'Cross-network':  'クロスネットワーク',
            }

            headers = ['チャネル', cur_label, prev_label, '増減']
            rows = []
            for _, r in compared_channels.head(8).iterrows():
                ch_name = rename_map.get(r[key_col], r[key_col])
                diff = r['diff']
                sign = '+' if diff > 0 else ''
                rows.append([
                    str(ch_name)[:12],
                    f"{int(r['cur_val']):,}",
                    f"{int(r['prev_val']):,}",
                    f"{sign}{int(diff):,}",
                ])
            col_ws = [32 * mm, 22 * mm, 22 * mm, 22 * mm]
            self._comparison_table(table_x, table_y, headers, rows, col_ws)

        self._footer(company_name, 3)

    def draw_key_events_compare(self, kev_chart_bytes, compared_kevents,
                                cur_label, prev_label, company_name='',
                                transition_rate_chart_bytes=None,
                                cur_kpis=None, prev_kpis=None,
                                compared_recruit=None, side_tables=None,
                                single_mode: bool = False):
        """P4: キーイベント発生回数とセッション比の比較。

        compared_kevents: 合計に入れる予約・電話等の比較結果（compare_key_events の形式）。
        side_tables: 右側の別表 [{'kind','title','subtotal','note','items'}]。
            完了到達・求人関連・未分類は左表の合計・参考比率に含めず、ここで別に示す。
        compared_recruit: 互換用（side_tables が無い場合に求人関連の別表として使う）。
        """
        self._bg(COLOR_LIGHT)
        self._page_title_bar('キーイベント発生回数比較')

        content_top = PAGE_H - 30 * mm
        margin = 10 * mm
        cur_kpis = cur_kpis or {}
        prev_kpis = prev_kpis or {}

        if side_tables is None:
            side_tables = []
            if compared_recruit:
                side_tables.append(dict(RECRUIT_SIDE_TABLE, items=compared_recruit))
        side_tables = [t for t in side_tables if t.get('items')]
        has_recruit_table = any(t.get('kind') == 'recruit' for t in side_tables)

        # 同数のため合計から除外したイベント（左表に残し、合計には入れない）
        cur_status = cur_kpis.get('key_events_status', 'missing')
        prev_status = prev_kpis.get('key_events_status', 'missing')
        comparison_available, incomparable_reason = key_events_comparison(cur_status, prev_status)
        excluded = compare_key_events(
            cur_kpis.get('key_events_dedup_excluded') or {},
            prev_kpis.get('key_events_dedup_excluded') or {},
        ) if comparison_available else []

        has_kev_data = bool(compared_kevents) and any(
            (int(i.get('current', 0)) > 0 or int(i.get('previous', 0)) > 0)
            for i in compared_kevents
        )

        def status_label(status):
            if status == 'missing':
                return '未出力'
            if status == KEY_EVENTS_INFERRED:
                return 'イベント名から判定'
            if status == KEY_EVENTS_FALLBACK:
                return '内訳なし（合計のみ）'
            return 'GA4のキーイベント明細'

        # --- 上段: 発生回数比較（左）+ セッション比（右） ---
        chart_h = 58 * mm
        if kev_chart_bytes and transition_rate_chart_bytes:
            self._section_header(
                margin, content_top,
                'キーイベント発生回数 / 予約・電話アクションのセッション比（参考）',
                '当月 vs 前月',
            )
            left_w = 165 * mm
            right_w = PAGE_W - (margin * 2) - left_w - 6 * mm
            self._image_bytes(kev_chart_bytes, margin, content_top - 5 * mm,
                              left_w, h=chart_h)
            self._image_bytes(transition_rate_chart_bytes,
                              margin + left_w + 6 * mm, content_top - 5 * mm,
                              right_w, h=chart_h)
        elif kev_chart_bytes:
            self._section_header(margin, content_top, 'キーイベント発生回数比較', '当月 vs 前月')
            self._image_bytes(kev_chart_bytes, margin, content_top - 5 * mm,
                              PAGE_W - 20 * mm, h=chart_h)

        months = (f'当月: {status_label(cur_status)}' if single_mode else
                  f'当月: {status_label(cur_status)} / 前月: {status_label(prev_status)}')
        notice = None
        if not comparison_available and incomparable_reason == 'method_mismatch':
            notice = f'キーイベント比較不能（{months}）。集計方法が異なるため、増減を比較していません。'
        elif not comparison_available and incomparable_reason == 'breakdown_unavailable':
            notice = (f'キーイベント比較不能（{months}）。イベント別の内訳が無いため、'
                      '件数と前月比は出していません。')
        elif not comparison_available:
            notice = f'キーイベント比較不能（{months}）。未出力を0件として扱っていません。'
        elif not has_kev_data and not excluded:
            notice = 'キーイベント発生回数は当月・前月とも0件です。'
        if notice:
            self._missing_data_notice(
                margin + 2 * mm, content_top - 10 * mm, PAGE_W - 24 * mm, 10 * mm, notice)

        # --- 下段: キーイベントテーブル（セッション比の参考値付き） ---
        col_ws = [42 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 20 * mm, 20 * mm]
        note_w = sum(col_ws) if side_tables else PAGE_W - 20 * mm
        # 片方の月だけ合計から外したイベントがある場合、合計・小計の前月差は出さない
        mismatch = ([] if (single_mode or not comparison_available)
                    else treatment_mismatch_events(cur_kpis, prev_kpis))
        table_notes = []
        if compared_kevents or excluded:
            table_notes.append(
                '※ 参考比率 = キーイベント発生回数 ÷ セッション数。GA4のセッションCVRではなく、同一セッション内の複数回発生を含みます。'
            )
        for text in (
            treatment_mismatch_note(cur_kpis, prev_kpis, single_mode),
            key_events_inferred_note(cur_kpis, prev_kpis),
            dedupe_note_text(cur_kpis, prev_kpis, single_mode),
            dedupe_unverified_note(cur_kpis, prev_kpis, single_mode),
            completion_counted_note(cur_kpis, prev_kpis),
        ):
            if text:
                table_notes.append(text)

        if compared_kevents or excluded:
            table_y = content_top - 72 * mm
            cur_sessions = int(cur_kpis.get('sessions', 0))
            prev_sessions = int(prev_kpis.get('sessions', 0))

            def reference_rate_str(event_count, sessions):
                if sessions > 0:
                    return f'{event_count / sessions * 100:.2f}%'
                return '-'

            headers = ['イベント名', cur_label, prev_label, '増減', '増減率', '当月参考比率', '前月参考比率']
            res_cur_total = sum(i['current'] for i in compared_kevents
                                if is_reservation_or_phone(i['name']))
            res_prev_total = sum(i['previous'] for i in compared_kevents
                                 if is_reservation_or_phone(i['name']))
            has_res_row = res_cur_total > 0 or res_prev_total > 0

            # 同数のため合計から除外した行にも上限を設け、超過分は1行に集約する
            excluded_shown, excluded_folded = list(excluded), []
            if len(excluded_shown) > P4_MAX_EXCLUDED_ROWS:
                excluded_shown = excluded[:P4_MAX_EXCLUDED_ROWS - 1]
                excluded_folded = excluded[P4_MAX_EXCLUDED_ROWS - 1:]

            # イベント名から判定した月はイベント数が多くなりやすい。表がフッターに
            # 食い込まないよう、収まらない分は「その他」1行にまとめる（合計値は全件で計算）。
            # 別表を右に置く場合、注記は左表の幅に収めて別表と重ねない。
            note_line_counts = [max(len(self._wrap_lines(n, note_w, 6.3, max_lines=3)), 1)
                                for n in table_notes]
            note_lines = sum(note_line_counts)
            bottom_limit = 11 * mm + note_lines * 4 * mm + 4 * mm
            max_rows = int((table_y - 5 * mm - bottom_limit) // (7 * mm)) - 1  # 見出し行を除く
            fixed_rows = (1 + (1 if has_res_row else 0) + len(excluded_shown)
                          + (1 if excluded_folded else 0))  # 合計 + 小計 + 除外行
            max_event_rows = max(1, max_rows - fixed_rows)
            shown = list(compared_kevents)
            folded = []
            if len(shown) > max_event_rows:
                shown, folded = shown[:max_event_rows - 1], shown[max_event_rows - 1:]

            # 長いイベント名は列幅を実測して省略し、省略後も互いに区別できるようにする
            name_w = col_ws[0] - 5 * mm
            labels = shorten_labels_unique(
                [str(i['name']) for i in shown] + [str(i['name']) for i in excluded_shown],
                name_w, measure=lambda t: pdfmetrics.stringWidth(t, JP_FONT, 8))
            shown_labels, excluded_labels = labels[:len(shown)], labels[len(shown):]

            def event_row(name, cur_v, prev_v, comparable=True):
                d = calc_diff(cur_v, prev_v)
                sign = '+' if d['diff'] > 0 else ''
                pct_str = f"{sign}{d['pct']:.1f}%" if d['pct'] is not None else 'N/A'
                diff_str = f"{sign}{int(d['diff']):,}"
                if not comparable:
                    diff_str, pct_str = '比較不能', '比較不能'
                return [
                    name,
                    f"{int(cur_v):,}",
                    f"{int(prev_v):,}",
                    diff_str,
                    pct_str,
                    reference_rate_str(cur_v, cur_sessions),
                    reference_rate_str(prev_v, prev_sessions),
                ]

            rows = []
            # 片方の月だけ合計から外したイベントの行も、増減は比較不能（前月比を作らない）
            mismatch_names = {m['name'] for m in mismatch}
            for item, label in zip(shown, shown_labels):
                rows.append(event_row(label, item['current'], item['previous'],
                                      comparable=item['name'] not in mismatch_names))
            if folded:
                rows.append(event_row(
                    f'その他（{len(folded)}種）',
                    sum(i['current'] for i in folded),
                    sum(i['previous'] for i in folded),
                ))
            # 同数のため合計から除外した行は件数を括弧で示し、合計・小計・参考比率に入れない
            for item, label in zip(excluded_shown, excluded_labels):
                rows.append({
                    'cells': [label, f"({int(item['current']):,})", f"({int(item['previous']):,})"],
                    'muted': True,
                    'span': (3, '同数のため合計から除外'),
                })
            if excluded_folded:
                rows.append({
                    'cells': [f'他{len(excluded_folded)}件（合計に含めない）',
                              f"({sum(int(i['current']) for i in excluded_folded):,})",
                              f"({sum(int(i['previous']) for i in excluded_folded):,})"],
                    'muted': True,
                    'span': (3, '同数のため合計から除外'),
                })
            # クリック・電話タップ等であり、実際の予約成立とは限らないため「アクション」と表記する。
            if has_res_row:
                row = event_row('予約・電話アクション 小計', res_cur_total, res_prev_total,
                                comparable=not mismatch)
                rows.append(row)
            # 合計行（左表で合計に入れたイベントの和。別表・除外行は含めない）
            total_cur = sum(i['current'] for i in compared_kevents)
            total_prev = sum(i['previous'] for i in compared_kevents)
            rows.append(event_row('合計（求人関連を除く）' if has_recruit_table else '合計',
                                  total_cur, total_prev, comparable=not mismatch))

            self._comparison_table(margin, table_y - 5 * mm, headers, rows, col_ws)
            # 表は table_y - 5mm から描くため、下端はその分も差し引く
            # （差し引かないと注記が最終行「合計」に重なっていた）。
            note_y = table_y - 5 * mm - (len(rows) + 1) * 7 * mm - 4 * mm
            for note, n_lines in zip(table_notes, note_line_counts):
                self._text_wrapped(
                    margin, note_y, note,
                    max_width=note_w,
                    font_size=6.3,
                    color=COLOR_GRAY,
                    max_lines=3,
                )
                note_y -= n_lines * 4 * mm
        else:
            # 比較不能でも、イベント名から判定した月・同数の除外があることは明示する
            note_y = content_top - 25 * mm
            for note in table_notes:
                used = self._text_wrapped(
                    margin + 2 * mm, note_y, note,
                    max_width=note_w - 2 * mm, font_size=7,
                    color=COLOR_GRAY, max_lines=3,
                )
                note_y -= max(used, 1) * 4 * mm

        # --- 右下: 完了到達・求人関連・未分類（別カテゴリ）。左表の合計・参考比率には含めない ---
        if side_tables:
            self._draw_side_tables(side_tables, cur_label, prev_label,
                                   x=margin + 176 * mm, y=content_top - 72 * mm - 5 * mm,
                                   bottom=P4_BOTTOM_LIMIT)

        self._footer(company_name, 4)

    def _side_note_lines(self, table, width):
        return max(len(self._wrap_lines(table['note'], width, 6.3, max_lines=3)), 1)

    def _draw_side_tables(self, tables, cur_label, prev_label, x, y, bottom):
        """右側の別表を、先に全表の高さを割り当ててから描く（後の表がフッターに落ちないように）。

        各表は見出し・小計・注記の高さを確保し、残りの行を表どうしで分け合う。
        割り当てを超える行は「他N件（小計に含む）」の1行に集約する。
        全表に1行も割り当てられない場合は、表をやめて件数を文章で示す（前月値も残す）。
        """
        width = PAGE_W - 10 * mm - x
        gap = 3 * mm
        for row_h in (7 * mm, 6 * mm, 5.2 * mm, 4.6 * mm):
            # 見出し＋小計の2行、表と注記の間 4mm、注記（行数を実測）、注記の下端 1.5mm、表どうしの間
            fixed = [2 * row_h + 4 * mm + (self._side_note_lines(t, width) - 1) * 3.2 * mm
                     + 1.5 * mm + gap for t in tables]
            capacity = int((y - bottom - sum(fixed) + gap) // row_h)
            alloc = allocate_side_rows([len(t['items']) for t in tables], capacity)
            if alloc is not None:
                for table, rows_n in zip(tables, alloc):
                    y = self._draw_side_table(table, cur_label, prev_label, x, y,
                                              row_h=row_h, max_event_rows=rows_n)
                    y -= gap
                return
        # 表として収まらない: 各別表の小計を文章で示す（前月値・比較不能も落とさない）
        for table in tables:
            used = self._text_wrapped(x, y - 3 * mm, side_table_summary_text(table, cur_label, prev_label),
                                      max_width=width, font_size=6.3, color=COLOR_GRAY, max_lines=4)
            y -= max(used, 1) * 3.2 * mm + 2 * mm

    def _draw_side_table(self, table, cur_label, prev_label, x, y, row_h=7 * mm,
                         max_event_rows=None):
        """別表（行＋小計）と注記を描き、注記の下端の y を返す。

        比較できない月の行（previous が None）は、前月を「未出力」等、増減を「比較不能」とする。
        max_event_rows を超える行は「他N件（小計に含む）」の1行にまとめる。
        """
        items = list(table['items'])
        width = PAGE_W - 10 * mm - x
        col_ws = [width - 54 * mm, 18 * mm, 18 * mm, 18 * mm]
        note_lines = self._side_note_lines(table, width)
        shown, folded = fold_items(items, max_event_rows if max_event_rows is not None else len(items))

        name_w = col_ws[0] - 4 * mm
        labels = shorten_labels_unique([str(i['name']) for i in shown], name_w,
                                       measure=lambda t: pdfmetrics.stringWidth(t, JP_FONT, 8))
        incomparable = any(i.get('previous') is None for i in items)
        prev_text = next((i.get('prev_text') for i in items if i.get('previous') is None), '—')

        def row(name, cur_v, prev_v):
            if incomparable:
                return [name, f'{int(cur_v):,}', prev_text, '比較不能']
            d = int(cur_v) - int(prev_v)
            sign = '+' if d > 0 else ''
            return [name, f'{int(cur_v):,}', f'{int(prev_v):,}', f'{sign}{d:,}']

        rows = [row(lab, i['current'], i.get('previous') or 0) for lab, i in zip(labels, shown)]
        if folded:
            rows.append(row(f'他{len(folded)}件（小計に含む）',
                            sum(i['current'] for i in folded),
                            sum((i.get('previous') or 0) for i in folded)))
        rows.append(row(table['subtotal'],
                        sum(i['current'] for i in items),
                        sum((i.get('previous') or 0) for i in items)))
        self._comparison_table(x, y, [table['title'], cur_label, prev_label, '増減'],
                               rows, col_ws, row_h=row_h)
        note_y = y - (len(rows) + 1) * row_h - 4 * mm
        self._text_wrapped(x, note_y, table['note'],
                           max_width=width, font_size=6.3, color=COLOR_GRAY, max_lines=3)
        return note_y - (note_lines - 1) * 3.2 * mm - 1 * mm

    def draw_pages_compare(self, pages_chart_bytes, cur_pages, prev_pages, company_name=''):
        """P5: 上位ページ比較（20件テーブル）"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('上位ページ比較（上位20ページ）')

        content_top = PAGE_H - 30 * mm
        margin = 10 * mm

        # グラフ（左半分）
        if pages_chart_bytes:
            self._section_header(margin, content_top, '上位20ページ 表示回数比較')
            chart_w = PAGE_W * 0.48
            chart_h = PAGE_H - 50 * mm  # フッターまでの高さ
            self._image_bytes(pages_chart_bytes, margin, content_top - 5 * mm,
                              chart_w, h=chart_h)

        # テーブル（右半分に20件）
        # 選定基準は max(当月PV, 前月PV)。当月PV降順だと当月0PVになったページが
        # 表から消え、消失を検知できなかった（前月218PV→当月0PVの実例あり）。
        selected = select_top_pages(cur_pages, prev_pages, 20)
        if not selected.empty:

            def shorten(title, max_len=14):
                title = str(title)
                parts = [p.strip() for p in re.split(r'[｜|]', title) if p.strip()]
                if len(parts) >= 2:
                    s = parts[0]
                else:
                    s = parts[-1] if parts else title
                return s[:max_len] + '…' if len(s) > max_len else s

            table_x = PAGE_W * 0.52
            table_y = content_top - 5 * mm
            headers = ['#', 'ページ', '当月', '前月', '増減']
            rows = []
            status_marks = {'new': '新', 'lost': '消'}
            for idx, (_, r) in enumerate(selected.iterrows(), start=1):
                cur_v = int(r['cur'])
                prev_v = int(r['prev'])
                diff = int(r['diff'])
                sign = '+' if diff > 0 else ''
                mark = status_marks.get(r['status'], '')
                name = shorten(r['page'])
                if mark:
                    name = f'[{mark}] {name}'
                rows.append([
                    str(idx),
                    name,
                    f"{cur_v:,}",
                    f"{prev_v:,}",
                    f"{sign}{diff:,}",
                ])

            # 20行が収まるよう行高さを調整
            row_h = 6.5 * mm
            col_ws = [8 * mm, 38 * mm, 16 * mm, 16 * mm, 16 * mm]
            total_w = sum(col_ws)

            for ri, row in enumerate([headers] + rows):
                row_y = table_y - ri * row_h
                is_header = ri == 0

                bg = COLOR_PRIMARY if is_header else \
                    (COLOR_CARD_BG if ri % 2 == 1 else colors.HexColor('#F5F8FC'))
                self.c.setFillColor(bg)
                self.c.rect(table_x, row_y - row_h, total_w, row_h, fill=1, stroke=0)

                txt_color = COLOR_WHITE if is_header else COLOR_DARK
                cx = table_x
                for ci, (cell, cw) in enumerate(zip(row, col_ws)):
                    if ci == 0:
                        align = 'center'
                    elif ci >= 2:
                        align = 'right'
                    else:
                        align = 'left'
                    if align == 'center':
                        self._text(cx + cw / 2, row_y - row_h + 1.5 * mm, cell,
                                   font_size=6.5, color=txt_color, align='center')
                    elif align == 'right':
                        self._text(cx + cw - 2 * mm, row_y - row_h + 1.5 * mm, cell,
                                   font_size=6.5, color=txt_color, align='right')
                    else:
                        self._text(cx + 1.5 * mm, row_y - row_h + 1.5 * mm, cell,
                                   font_size=6.5, color=txt_color)
                    cx += cw

            # 選定基準と記号の凡例
            legend_y = table_y - (len(rows) + 1) * row_h - 3 * mm
            self._text(table_x, legend_y,
                       '※ 当月PVと前月PVの大きい方で上位20件を選定。[新]=当月から発生 / [消]=当月0件',
                       font_size=5.8, color=COLOR_GRAY)

        self._footer(company_name, 5)

    def draw_page_engagement_page(self, cur_pages, prev_pages, cur_kpis, prev_kpis, company_name=''):
        """P6: 上位ページ別 推定接触量（PV按分）"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('上位ページ 推定接触量（PV按分・実測滞在時間ではありません）')

        margin = 12 * mm
        content_top = PAGE_H - 30 * mm

        def sum_top_pv(df, top_n=20):
            if df is None:
                return 0
            return int(df.iloc[:, 1].head(top_n).sum())

        def fmt_sec(sec):
            # 上位ページのPVが無い月は1PVあたりの時間を算出できない。0秒と書かず取得不能とする。
            if sec is None:
                return '取得不能'
            sec = max(0, float(sec))
            m = int(sec // 60)
            s = int(sec % 60)
            if m > 0:
                return f'{m}分{s:02d}秒'
            return f'{s}秒'

        cur_top_pv = sum_top_pv(cur_pages, 20)
        prev_top_pv = sum_top_pv(prev_pages, 20)
        cur_total_eng = float(cur_kpis.get('avg_engage_sec', 0)) * float(cur_kpis.get('active_users', 0))
        prev_total_eng = float(prev_kpis.get('avg_engage_sec', 0)) * float(prev_kpis.get('active_users', 0))
        cur_eng_per_pv = (cur_total_eng / cur_top_pv) if cur_top_pv > 0 else None
        prev_eng_per_pv = (prev_total_eng / prev_top_pv) if prev_top_pv > 0 else None

        # 1PVあたり平均滞在時間（目安）をタイトル横に表示
        table_y = content_top
        avg_label = f'1PVあたり平均滞在時間（目安）: 当月 {fmt_sec(cur_eng_per_pv)} / 前月 {fmt_sec(prev_eng_per_pv)}'
        self._section_header(margin, table_y, '上位20ページ別 推定接触量（PV按分・当月 vs 前月）', avg_label)

        headers = ['#', 'ページ', '当月', '前月', '増減']
        rows = []
        prev_map = {}
        if prev_pages is not None:
            prev_map = dict(zip(prev_pages.iloc[:, 0], prev_pages.iloc[:, 1]))

        def short_page(title, max_len=35):
            t = str(title)
            parts = [p.strip() for p in re.split(r'[｜|]', t) if p.strip()]
            base = parts[0] if parts else t
            return base[:max_len] + '…' if len(base) > max_len else base

        row_diffs = []  # 各行の diff_sec を保持（増減列の色分け用）
        # 前ページの上位20表と同じ選定基準（max(当月PV, 前月PV)）を使う。
        # 2ページで異なるページ集合を出すと読者が混乱するため揃える。
        selected = select_top_pages(cur_pages, prev_pages, 20)
        if not selected.empty:
            for idx, (_, r) in enumerate(selected.iterrows(), start=1):
                name = r['page']
                cur_pv = int(r['cur'])
                prev_pv = int(r['prev'])
                cur_est_sec = cur_pv * cur_eng_per_pv if cur_eng_per_pv is not None else None
                prev_est_sec = prev_pv * prev_eng_per_pv if prev_eng_per_pv is not None else None
                if cur_est_sec is None or prev_est_sec is None:
                    diff_sec = 0
                    diff_txt = '取得不能'
                else:
                    diff_sec = cur_est_sec - prev_est_sec
                    diff_txt = f'{fmt_sec(abs(diff_sec))}{"↑" if diff_sec > 0 else "↓" if diff_sec < 0 else "→"}'
                rows.append([
                    str(idx),
                    short_page(name),
                    fmt_sec(cur_est_sec),
                    fmt_sec(prev_est_sec),
                    diff_txt,
                ])
                row_diffs.append(diff_sec)

        # テーブルをページ全体に拡大
        total_w = PAGE_W - margin * 2
        col_ws = [10 * mm, total_w - 10 * mm - 45 * mm - 45 * mm - 50 * mm, 45 * mm, 45 * mm, 50 * mm]
        num_rows = len(rows) + 1  # +1 for header
        available_h = content_top - 5 * mm - 22 * mm  # フッター・注釈分を確保
        row_h = min(available_h / num_rows, 8 * mm)
        fs = 8.0 if row_h >= 7 * mm else 7.0

        tx = margin
        for ri, row in enumerate([headers] + rows):
            row_y = table_y - 5 * mm - ri * row_h
            is_header = ri == 0
            bg = COLOR_PRIMARY if is_header else (COLOR_CARD_BG if ri % 2 == 1 else colors.HexColor('#F5F8FC'))
            self.c.setFillColor(bg)
            self.c.rect(tx, row_y - row_h, total_w, row_h, fill=1, stroke=0)
            txt_color = COLOR_WHITE if is_header else COLOR_DARK
            txt_y = row_y - row_h + (row_h - fs * 0.35) / 2
            cx = tx
            for ci, (cell, cw) in enumerate(zip(row, col_ws)):
                # 増減列（ci==4）で減少時は赤字
                cell_color = txt_color
                if not is_header and ci == 4 and (ri - 1) < len(row_diffs):
                    if row_diffs[ri - 1] < 0:
                        cell_color = COLOR_DECREASE
                    elif row_diffs[ri - 1] > 0:
                        cell_color = COLOR_INCREASE
                if ci == 0:
                    self._text(cx + cw / 2, txt_y, cell, font_size=fs, color=cell_color, align='center')
                elif ci >= 2:
                    self._text(cx + cw - 2 * mm, txt_y, cell, font_size=fs, color=cell_color, align='right')
                else:
                    self._text(cx + 2 * mm, txt_y, cell, font_size=fs, color=cell_color)
                cx += cw

        note_y = table_y - 5 * mm - num_rows * row_h - 2 * mm
        notes = [
            '※ 推定エンゲージ時間 = そのページのPV数 × 1PVあたり平均滞在時間（全体エンゲージ時間 ÷ 上位20ページ合計PV）',
            '　 各ページにユーザーが費やした総時間の推定値です。値が大きいほどそのページに多くの時間が使われています。',
            '　 GA4のCSVにはページ別の滞在時間データがないため、全体のエンゲージ時間をPV比で按分した推定値です。',
        ]
        for i, note in enumerate(notes):
            self._text(margin, note_y - i * 4 * mm, note, font_size=6.5, color=COLOR_GRAY)
        self._footer(company_name, 6)

    def draw_user_behavior_compare(self, engage_chart_bytes, user_compare_chart_bytes,
                                   cur_kpis, prev_kpis, compared_kpis, company_name='',
                                   page_num=7):
        """P6: ユーザー行動比較"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('ユーザー行動比較')

        content_top = PAGE_H - 30 * mm
        col_w = (PAGE_W - 36 * mm) / 2
        col1_x = 12 * mm
        col2_x = col1_x + col_w + 12 * mm

        # 左: エンゲージメント推移
        self._section_header(col1_x, content_top, 'エンゲージメント時間', '日別推移')
        if engage_chart_bytes:
            self._image_bytes(engage_chart_bytes, col1_x, content_top - 5 * mm,
                              col_w, h=65 * mm)

        # 右: 新規vsリピーター
        self._section_header(col2_x, content_top, '新規 vs リピーター',
                             'リピーター = 30日アクティブ − 新規（集計窓が1日ずれるため概算）')
        if user_compare_chart_bytes:
            self._image_bytes(user_compare_chart_bytes, col2_x, content_top - 5 * mm,
                              col_w, h=65 * mm)

        # 下段: ユーザー行動サマリ
        summary_y = content_top - 80 * mm
        self._section_header(col1_x, summary_y, 'ユーザー行動サマリ')

        metrics = [
            ('平均エンゲージメント時間', cur_kpis['avg_engage_str'],
             prev_kpis['avg_engage_str'], compared_kpis['avg_engage_sec']),
            ('アクティブユーザー', f"{cur_kpis['active_users']:,}人",
             f"{prev_kpis['active_users']:,}人", compared_kpis['active_users']),
            ('新規ユーザー', f"{cur_kpis['new_users']:,}人",
             f"{prev_kpis['new_users']:,}人", compared_kpis['new_users']),
            ('セッション数', f"{cur_kpis['sessions']:,}件",
             f"{prev_kpis['sessions']:,}件", compared_kpis['sessions']),
        ]

        for i, (label, cur_str, prev_str, diff) in enumerate(metrics):
            my = summary_y - 12 * mm - i * 14 * mm
            # 白背景カード
            self._rect(col1_x, my - 10 * mm, PAGE_W - 24 * mm, 12 * mm,
                       fill_color=COLOR_CARD_BG, stroke_color=colors.HexColor('#DDE6EF'))
            self._text(col1_x + 5 * mm, my - 6 * mm, label, font_size=9, color=COLOR_DARK)
            self._text(col1_x + 80 * mm, my - 6 * mm, f'当月: {cur_str}',
                       font_size=9, color=COLOR_PRIMARY)
            self._text(col1_x + 140 * mm, my - 6 * mm, f'前月: {prev_str}',
                       font_size=9, color=COLOR_GRAY)
            self._delta_text(col1_x + 200 * mm, my - 6 * mm, diff, font_size=9)

        self._footer(company_name, page_num)

    def draw_summary_page(self, highlights: dict, cur_meta: dict, prev_meta: dict,
                          company_name='', page_num=7, single_mode: bool = False,
                          cur_kpis: dict | None = None,
                          country_note: str = '', incomplete_note: str = '',
                          prev_kpis: dict | None = None):
        """P7: 総括"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('月次比較 総括')

        content_top = PAGE_H - 30 * mm
        margin = 12 * mm

        # 期間表示
        if single_mode:
            self._text(
                margin,
                content_top,
                (f"対象月: {cur_meta.get('month_label', '')}" if cur_meta.get('is_calendar_month', True)
                 else f"対象期間: {cur_meta.get('period_label') or cur_meta.get('month_label', '')}"),
                font_size=12,
                color=COLOR_DARK,
            )
        else:
            self._text(
                margin,
                content_top,
                f"比較期間: {prev_meta.get('month_label', '')} → {cur_meta.get('month_label', '')}",
                font_size=12,
                color=COLOR_DARK,
            )

        # 今月の結論（3行）
        headline_lines = highlights.get('headline_lines', [])
        for i, line in enumerate(headline_lines[:3]):
            hy = content_top - 4 * mm - i * 4.2 * mm
            self._text(margin + 2 * mm, hy, f'・{line}', font_size=7.6, color=COLOR_GRAY)

        # ハイライトカード
        card_y = content_top - 20 * mm
        card_w = (PAGE_W - 36 * mm) / 2
        card_h = 26 * mm

        # 1枚目カード: best が実際に増加なら「最も増加した指標」。
        # 全KPIが下落した月に減少値を「改善」と緑色表示していた不具合の修正。
        best_kpi = highlights.get('best_kpi', {})
        best_is_gain = bool(best_kpi.get('is_gain'))
        best_is_flat = bool(best_kpi.get('is_flat'))
        if best_is_gain:
            card1_title = '最も増加した指標'
            card1_bar = COLOR_INCREASE
            card1_delta_color = COLOR_INCREASE
            card1_label = best_kpi.get('label', '-')
            card1_delta = best_kpi.get('delta', '-')
        elif best_is_flat:
            card1_title = '増加した指標'
            card1_bar = COLOR_GRAY
            card1_delta_color = COLOR_GRAY
            card1_label = 'なし（横ばい）'
            card1_delta = f"最小の変動: {best_kpi.get('label', '-')} {best_kpi.get('delta', '-')}"
        else:
            # 全KPIが減少している月
            card1_title = '増加した指標'
            card1_bar = COLOR_GRAY
            card1_delta_color = COLOR_GRAY
            card1_label = 'なし'
            card1_delta = f"下落が最小: {best_kpi.get('label', '-')} {best_kpi.get('delta', '-')}"

        self._rect(margin, card_y - card_h, card_w, card_h,
                   fill_color=COLOR_CARD_BG, stroke_color=colors.HexColor('#DDE6EF'))
        self._rect(margin, card_y - 3 * mm, card_w, 3 * mm,
                   fill_color=card1_bar, radius=0)
        self._text(margin + 5 * mm, card_y - 10 * mm,
                   card1_title, font_size=9, color=COLOR_GRAY)
        self._text(margin + 5 * mm, card_y - 17 * mm,
                   card1_label, font_size=13, color=COLOR_DARK)
        self._text(margin + 5 * mm, card_y - 23 * mm,
                   card1_delta, font_size=8,
                   color=card1_delta_color)

        # 2枚目カード: worst が実際に減少なら「注意が必要な指標」、
        # 全KPI増加など worst も増加している場合は「伸びが最も小さい指標」として中立表示
        worst_is_decline = bool(highlights.get('worst_kpi', {}).get('is_decline'))
        cx2 = margin + card_w + 12 * mm
        card2_title = '注意が必要な指標' if worst_is_decline else '伸びが最も小さい指標'
        card2_bar = COLOR_DECREASE if worst_is_decline else COLOR_GRAY
        card2_delta_color = COLOR_DECREASE if worst_is_decline else COLOR_GRAY
        self._rect(cx2, card_y - card_h, card_w, card_h,
                   fill_color=COLOR_CARD_BG, stroke_color=colors.HexColor('#DDE6EF'))
        self._rect(cx2, card_y - 3 * mm, card_w, 3 * mm,
                   fill_color=card2_bar, radius=0)
        self._text(cx2 + 5 * mm, card_y - 10 * mm,
                   card2_title, font_size=9, color=COLOR_GRAY)
        self._text(cx2 + 5 * mm, card_y - 17 * mm,
                   highlights['worst_kpi']['label'], font_size=13, color=COLOR_DARK)
        self._text(cx2 + 5 * mm, card_y - 23 * mm,
                   highlights['worst_kpi']['delta'], font_size=9,
                   color=card2_delta_color)

        # 優先施策ハイライト
        hl_y = card_y - card_h - 10 * mm
        self._section_header(margin, hl_y, '次月の確認事項（根拠つき）')

        actions = highlights.get('action_items', [])
        if not actions:
            actions = ['主要指標に大きな変動はなし。次月も同じ指標で推移を確認する。']

        # 確認事項は根拠を併記するため長くなる。1行描画では右端で切れるので折り返す。
        action_w = PAGE_W - 24 * mm - 22 * mm - 3 * mm
        action_ys = []
        iy = hl_y - 10 * mm
        for i, action in enumerate(actions[:2]):
            n_lines = max(len(self._wrap_lines(action, action_w, 7.5, max_lines=2)), 1)
            box_h = 6 * mm + n_lines * 4.2 * mm
            self._rect(margin, iy - box_h + 1 * mm, PAGE_W - 24 * mm, box_h,
                       fill_color=COLOR_CARD_BG, stroke_color=colors.HexColor('#DDE6EF'))
            self._text(margin + 5 * mm, iy - 5 * mm, f'確認{i+1}',
                       font_size=8, color=COLOR_GRAY)
            self._text_wrapped(margin + 22 * mm, iy - 5 * mm, action,
                               max_width=action_w,
                               font_size=7.5, color=COLOR_DARK, max_lines=2)
            action_ys.append(iy)
            iy -= box_h + 2 * mm

        # KPI変動一覧（左）と総括コメント（右）
        section_y = iy - 4 * mm
        col_gap = 0 * mm
        left_w = (PAGE_W - 24 * mm) * 0.45
        right_x = margin + left_w - 4 * mm

        self._section_header(margin, section_y, 'KPI変動一覧')
        bullets = highlights.get('summary_bullets', [])
        for i, bullet in enumerate(bullets[:5]):
            by = section_y - 10 * mm - i * 7 * mm
            self._text(margin + 8 * mm, by, f'• {bullet}', font_size=8, color=COLOR_DARK)

        self._section_header(right_x, section_y, '総括コメント')
        ry = section_y - 10 * mm

        if single_mode:
            kpis = cur_kpis or {}
            sessions = int(kpis.get('sessions', 0))
            active_users = int(kpis.get('active_users', 0))
            new_users = int(kpis.get('new_users', 0))
            key_events_total = int(kpis.get('key_events_total', 0))
            key_events_status = kpis.get('key_events_status', 'missing')
            recruit_total = int(kpis.get('key_events_recruit_total', 0))

            if key_events_status == 'missing':
                key_event_comment = 'キーイベント明細はCSVに未出力のため、発生回数は評価できません。'
            elif key_events_status == KEY_EVENTS_FALLBACK:
                key_event_comment = ('キーイベントのイベント別の内訳が出力されていないため、'
                                     '予約・電話などの発生回数は算出していません。')
            elif key_events_status == KEY_EVENTS_INFERRED:
                key_event_comment = f'イベント名から判定した予約・電話などの発生回数は合計 {key_events_total:,} 件でした。'
            else:
                key_event_comment = f'予約・電話などのキーイベント発生回数は合計 {key_events_total:,} 件でした。'
            if recruit_total > 0:
                key_event_comment += f'求人関連のイベント {recruit_total:,} 件は含めず別に集計しています。'
            # 値は「アクティブユーザーあたりの平均エンゲージメント時間」（KPIカードと同じ値）。
            # 旧表記「1セッションあたり」は指標の実体と異なっていた。
            single_comments = [
                f"今月のアクセス数は {sessions:,} 件、アクティブユーザーは {active_users:,} 人でした。",
                f"新しく来訪したユーザーは {new_users:,} 人でした。{key_event_comment}",
            ]
            # 同数の2イベントは1つ分のみ合計に含めた旨（平易な文で）
            dedupe_txt = dedupe_note_text(kpis, kpis, single_mode=True, with_mark=False)
            if dedupe_txt:
                single_comments.append(dedupe_txt)
            single_comments += [
                f"アクティブユーザーあたりの平均エンゲージメント時間は {kpis.get('avg_engage_str', '-')}でした。",
                "次月は、成果につながったページの見出しと導線を優先的に整え、予約ボタン周辺の文言を統一して改善を進めることを推奨します。",
            ]
            # 1行描画だと右端ぎりぎりまで伸びていたため、右余白を残して折り返す
            # （行頭の「•」の右に本文を揃え、折り返し行も同じ位置から始める）
            text_x = right_x + 5 * mm
            single_max_w = PAGE_W - text_x - 12 * mm
            line_h = 7.8 * 1.35
            y = ry
            for txt in single_comments:
                self._text(right_x + 2 * mm, y, '•', font_size=7.8, color=COLOR_DARK)
                used = self._text_wrapped(text_x, y, txt,
                                          max_width=single_max_w, font_size=7.8,
                                          color=COLOR_DARK, max_lines=3)
                y -= max(used, 1) * line_h + (6.4 * mm - line_h)
            ay = y
        else:
            good_points = highlights.get('good_points', [])
            improve_points = highlights.get('improve_points', [])
            action_items = highlights.get('action_items', [])

            self._text(right_x + 2 * mm, ry, '増加した指標', font_size=7.5, color=COLOR_PRIMARY)
            for i, txt in enumerate(good_points[:2]):
                y = ry - 5 * mm - i * 5 * mm
                self._text(right_x + 6 * mm, y, f'• {txt}', font_size=6.8, color=COLOR_DARK)

            iy = ry - 5 * mm - min(len(good_points), 2) * 5 * mm - 1.5 * mm
            self._text(right_x + 2 * mm, iy, '減少した指標', font_size=7.5, color=colors.HexColor('#C27116'))
            for i, txt in enumerate(improve_points[:2]):
                y = iy - 5 * mm - i * 5 * mm
                self._text(right_x + 6 * mm, y, f'• {txt}', font_size=6.8, color=COLOR_DARK)

            ay = iy - 5 * mm - min(len(improve_points), 2) * 5 * mm - 1.5 * mm
            self._text(right_x + 2 * mm, ay, '次月の確認事項', font_size=7.5, color=colors.HexColor('#2E8B57'))
            # 施策文は長く、1行描画では右端で文が途切れていた（「…をキーイ」で切断）ため折り返す
            action_max_w = PAGE_W - (right_x + 6 * mm) - 10 * mm
            action_lines = 0
            for txt in action_items[:2]:
                y = ay - 5 * mm - action_lines * 3.4 * mm
                used = self._text_wrapped(right_x + 6 * mm, y, f'• {txt}',
                                          max_width=action_max_w,
                                          font_size=6.8, color=COLOR_DARK,
                                          line_gap=1.35, max_lines=3)
                action_lines += used

        # 注釈（左右セクションの下）
        left_bottom = section_y - 10 * mm - min(len(bullets), 5) * 7 * mm
        if single_mode:
            right_bottom = ay - 2 * mm
        else:
            right_bottom = ay - 5 * mm - max(action_lines, 1) * 3.4 * mm
        # 下部の注記。行数を先に測り、フッター線より上に収まるよう開始位置を決める。
        notes = []
        if incomplete_note:
            notes.append((f'⚠ {incomplete_note}', colors.HexColor('#B5791F'), 1))
        if not single_mode:
            # キーイベントの合計に1つ分のみ含めた同数イベント（同じ操作の重複計測の疑い）
            dedupe_txt = dedupe_note_text(cur_kpis, prev_kpis)
            if dedupe_txt:
                notes.append((dedupe_txt, COLOR_GRAY, 2))
        notes.append((country_note or '※ 本レポートはGA4エクスポートCSVに基づいています。',
                      COLOR_GRAY, 3))
        note_w = PAGE_W - 2 * margin
        line_h = 3.6 * mm
        heights = [max(len(self._wrap_lines(t, note_w, 7, max_lines=ml)), 1) * line_h + 0.6 * mm
                   for t, _, ml in notes]
        upper = min(left_bottom, right_bottom) - 4 * mm   # 上の段と重ならない最も高い位置
        lowest_start = 12 * mm + sum(heights) - line_h    # 最終行がフッター線より上に来る開始位置
        note_y = min(upper, max(min(left_bottom, right_bottom) - 10 * mm, lowest_start))
        for (text, clr, ml), h in zip(notes, heights):
            self._text_wrapped(margin, note_y, text, max_width=note_w,
                               font_size=7, color=clr, max_lines=ml)
            note_y -= h

        self._footer(company_name, page_num)

    def draw_cohort_compare(self, cohort_chart_bytes, cur_trend, prev_trend,
                            cur_label, prev_label, company_name='',
                            page_num=8, incomplete_note=''):
        """P8: アクティブユーザー期間別比較（30日/7日/1日）"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('アクティブユーザー期間別比較（30日 / 7日 / 1日）')

        content_top = PAGE_H - 30 * mm
        margin = 10 * mm

        # 期間別アクティブユーザー推移（30日/7日/1日）
        self._section_header(margin, content_top, 'アクティブユーザー推移', '30日 / 7日 / 1日')
        if cohort_chart_bytes:
            self._image_bytes(cohort_chart_bytes, margin, content_top - 5 * mm,
                              PAGE_W - 20 * mm, h=48 * mm)

        def get_last_val(df, col_idx):
            if df is not None and col_idx < len(df.columns):
                return int(df.iloc[:, col_idx].iloc[-1])
            return 0

        # 計測不完全月の注意（前月の30日=7日が同値になる等、定着率が解釈困難になる）
        if incomplete_note:
            self._text(margin, content_top - 52 * mm,
                       f'⚠ {incomplete_note} 計測期間が短い月は定着率が正しく算出されない場合があります。',
                       font_size=6.8, color=colors.HexColor('#B5791F'))

        # スティッキネス（WAU/MAU・DAU/WAU） — ファネル形式
        # 注: これはコホート維持率ではない。上位期間の母集団に対する下位期間の重なり率。
        ret_y = content_top - 60 * mm
        self._section_header(margin, ret_y, 'スティッキネス（どれだけ繰り返し見られているか）',
                             '30日に来た人のうち、直近7日・直近1日にも来ていた人の割合')

        # データ準備
        funnel_data = []
        for label, ci in [('30日', 1), ('7日', 2), ('1日', 3)]:
            funnel_data.append({
                'label': label,
                'cur': get_last_val(cur_trend, ci),
                'prev': get_last_val(prev_trend, ci),
            })

        retention_steps = []
        for i in range(len(funnel_data) - 1):
            src = funnel_data[i]
            dst = funnel_data[i + 1]
            cur_rate = (dst['cur'] / src['cur'] * 100) if src['cur'] > 0 else 0
            prev_rate = (dst['prev'] / src['prev'] * 100) if src['prev'] > 0 else 0
            retention_steps.append({
                'from': src['label'], 'to': dst['label'],
                'cur_rate': cur_rate, 'prev_rate': prev_rate,
                'cur_from': src['cur'], 'cur_to': dst['cur'],
                'prev_from': src['prev'], 'prev_to': dst['prev'],
            })

        # --- ファネルボックス描画 ---
        fy = ret_y - 5 * mm
        total_w = PAGE_W - margin * 2
        box_w = 52 * mm
        arrow_w = (total_w - box_w * 3) / 2
        box_h = 25 * mm

        # 各ステージのボックス色（段階的に薄く）
        stage_colors = [
            colors.HexColor('#1A6FAB'),  # 30日: 濃いブルー
            colors.HexColor('#4DA3D4'),  # 7日: ミディアムブルー
            colors.HexColor('#B0D4F1'),  # 1日: ライトブルー
        ]

        for si, fd in enumerate(funnel_data):
            bx = margin + si * (box_w + arrow_w)
            # ボックス背景
            self._rect(bx, fy - box_h, box_w, box_h,
                       fill_color=colors.HexColor('#F5F8FC'),
                       stroke_color=colors.HexColor('#DDE6EF'))
            # 上部カラーバー
            self._rect(bx, fy - 2 * mm, box_w, 2 * mm,
                       fill_color=stage_colors[si], radius=0)
            # ラベル
            self._text(bx + box_w / 2, fy - 5 * mm, f'{fd["label"]}アクティブ',
                       font_size=7, color=COLOR_GRAY, align='center')
            # 当月値
            self._text(bx + box_w / 2, fy - 10 * mm, f'{fd["cur"]:,}',
                       font_size=12, color=COLOR_PRIMARY, align='center')
            self._text(bx + box_w / 2, fy - 14 * mm, f'（{cur_label}）',
                       font_size=5.5, color=COLOR_GRAY, align='center')
            # 前月値
            self._text(bx + box_w / 2, fy - 18 * mm, f'{fd["prev"]:,}',
                       font_size=9, color=COLOR_GRAY, align='center')
            self._text(bx + box_w / 2, fy - 21.5 * mm, f'（{prev_label}）',
                       font_size=5.5, color=COLOR_GRAY, align='center')

            # 矢印 + 重なり率（最後のボックスの後は描かない）
            if si < len(retention_steps):
                step = retention_steps[si]
                ax = bx + box_w + 2 * mm
                aw = arrow_w - 4 * mm
                ay_mid = fy - box_h / 2

                # 矢印の線
                self.c.setStrokeColor(colors.HexColor('#8A9BB0'))
                self.c.setLineWidth(1.2)
                self.c.line(ax, ay_mid, ax + aw, ay_mid)
                # 矢印の先端
                self.c.setFillColor(colors.HexColor('#8A9BB0'))
                arrow_path = self.c.beginPath()
                arrow_path.moveTo(ax + aw, ay_mid)
                arrow_path.lineTo(ax + aw - 3 * mm, ay_mid + 1.5 * mm)
                arrow_path.lineTo(ax + aw - 3 * mm, ay_mid - 1.5 * mm)
                arrow_path.close()
                self.c.drawPath(arrow_path, fill=1, stroke=0)

                # リテンション率（矢印の上）
                diff_rate = step['cur_rate'] - step['prev_rate']
                diff_sign = '+' if diff_rate > 0 else ''
                diff_clr = COLOR_INCREASE if diff_rate > 0 else COLOR_DECREASE if diff_rate < 0 else COLOR_GRAY
                self._text(ax + aw / 2, ay_mid + 5.5 * mm, f'{step["cur_rate"]:.1f}%',
                           font_size=11, color=COLOR_PRIMARY, align='center')
                self._text(ax + aw / 2, ay_mid + 1 * mm, f'前月 {step["prev_rate"]:.1f}%',
                           font_size=7, color=COLOR_GRAY, align='center')
                self._text(ax + aw / 2, ay_mid - 4 * mm, f'{diff_sign}{diff_rate:.1f}pt',
                           font_size=8, color=diff_clr, align='center')

        # --- 指標の解説表 ---
        explain_y = fy - box_h - 10 * mm
        self._section_header(margin, explain_y, '期間別アクティブユーザー指標の見方')

        cohort_terms = [
            ('30日アクティブ', '期間末日から遡る直近30日間に1回以上訪問したユーザー数（GA4のMAU）。暦月の集計とは一致しない'),
            ('7日アクティブ', '直近7日間に1回以上訪問したユーザー数（≒週間アクティブユーザー / WAU）'),
            ('1日アクティブ', 'その日に訪問したユーザー数（≒日次アクティブユーザー / DAU）'),
            ('重なり率（スティッキネス）', '30日アクティブのうち直近7日・1日にも訪問した人の割合（WAU/MAU・DAU/WAU）。コホート維持率ではない'),
            ('この数値の見方', '高い＝同じ人が何度も見に来ている／低い＝1回見て終わりの人が多い。どちらが良いかは目的による（下記参照）'),
        ]

        ex_row_h = 6.2 * mm
        ex_total_w = PAGE_W - margin * 2
        ex_col_ws = [42 * mm, ex_total_w - 42 * mm]
        ex_headers = ['指標', '意味']

        for ri, row_data in enumerate([ex_headers] + cohort_terms):
            ey = explain_y - 6 * mm - ri * ex_row_h
            is_header = ri == 0
            bg = COLOR_PRIMARY if is_header else (COLOR_CARD_BG if ri % 2 == 1 else colors.HexColor('#F5F8FC'))
            self.c.setFillColor(bg)
            self.c.rect(margin, ey - ex_row_h, ex_total_w, ex_row_h, fill=1, stroke=0)
            txt_color = COLOR_WHITE if is_header else COLOR_DARK
            label_color = COLOR_WHITE if is_header else COLOR_PRIMARY
            txt_y = ey - ex_row_h + (ex_row_h - 7 * 0.35) / 2
            self._text(margin + 3 * mm, txt_y, str(row_data[0]),
                       font_size=7, color=label_color)
            self._text(margin + ex_col_ws[0] + 3 * mm, txt_y, str(row_data[1]),
                       font_size=7, color=txt_color)

        # --- 「どうなれば良いのか」の解説ボックス ---
        # スティッキネスは高ければ良いとは限らない指標のため、目的別の読み方を示す。
        guide_top = explain_y - 6 * mm - (len(cohort_terms) + 1) * ex_row_h - 5 * mm
        guide_lines = [
            '新患を増やしたい場合: この割合よりも「30日アクティブ」「新規ユーザー」が増えているかを優先して見てください。'
            '歯科医院のサイトは毎日使うものではないため、割合が低いこと自体は問題ではありません。',
            '割合が上がったとき: 同じ人が何度も見に来ている状態。検討中の方が比較・確認している可能性があり、'
            '料金・アクセス・症例など「迷いを解消するページ」の充実が効きやすい状況です。',
            '割合が大きく下がったとき: 1回だけ見て離れる人が増えた状態。広告や検索からの新規流入が増えた直後にも起こるため、'
            '前ページの流入チャネルの増減とあわせて確認してください。',
        ]
        # 実際の折り返し行数を先に測ってボックス高さを決める（余白の出過ぎ・はみ出しを防ぐ）
        guide_w = PAGE_W - margin * 2 - 6 * mm
        guide_line_counts = [
            max(len(self._wrap_lines(f'• {t}', guide_w, 6.8, max_lines=2)), 1)
            for t in guide_lines
        ]
        guide_h = 9 * mm + sum(guide_line_counts) * 4.2 * mm
        self._rect(margin, guide_top - guide_h, PAGE_W - margin * 2, guide_h,
                   fill_color=COLOR_CARD_BG, stroke_color=colors.HexColor('#DDE6EF'))
        self._text(margin + 3 * mm, guide_top - 5 * mm,
                   'この数値はどうなれば良いのか', font_size=7.5, color=COLOR_PRIMARY)
        gy = guide_top - 10 * mm
        for line, n in zip(guide_lines, guide_line_counts):
            self._text_wrapped(margin + 3 * mm, gy, f'• {line}',
                               max_width=guide_w,
                               font_size=6.8, color=COLOR_DARK, max_lines=2)
            gy -= n * 4.2 * mm

        self._footer(company_name, page_num)

    def draw_reservation_compare(self, reservation_chart_bytes, transition_rate_chart_bytes,
                                 compared_kevents,
                                 cur_kpis, prev_kpis, cur_label, prev_label, company_name='',
                                 page_num=9):
        """予約・電話アクションの発生回数分析（現在は標準構成で未使用）。"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('予約・電話アクション分析')

        content_top = PAGE_H - 30 * mm
        margin = 10 * mm

        # 予約比較チャート（左）＋移行率チャート（右）
        self._section_header(
            margin, content_top,
            '予約・電話アクション発生回数 / セッション比（参考）',
            '当月 vs 前月',
        )
        chart_y = content_top - 5 * mm
        left_w = 165 * mm
        right_w = PAGE_W - (margin * 2) - left_w - 6 * mm
        has_res_data = bool(compared_kevents) and any(
            is_reservation_or_phone(i.get('name', ''))
            and (int(i.get('current', 0)) > 0 or int(i.get('previous', 0)) > 0)
            for i in compared_kevents
        )

        if reservation_chart_bytes:
            self._image_bytes(reservation_chart_bytes, margin, chart_y, left_w, h=58 * mm)
        if transition_rate_chart_bytes:
            self._image_bytes(
                transition_rate_chart_bytes,
                margin + left_w + 6 * mm,
                chart_y,
                right_w,
                h=58 * mm,
            )
        if not has_res_data:
            self._missing_data_notice(
                margin, chart_y - 2 * mm, PAGE_W - 20 * mm, 10 * mm,
                '計測データ未出力（予約・電話系キーイベントがCSVに含まれていません）'
            )

        # 予約系テーブル
        table_y = content_top - 72 * mm
        self._section_header(margin, table_y, '予約・電話イベント詳細')

        res_events = [e for e in compared_kevents if is_reservation_or_phone(e['name'])]
        if not res_events:
            res_events = compared_kevents  # フォールバック

        headers = ['イベント名', cur_label, prev_label, '増減', '増減率']
        rows = []
        for item in res_events:
            d = item['diff_dict']
            sign = '+' if d['diff'] > 0 else ''
            pct_str = f"{sign}{d['pct']:.1f}%" if d['pct'] is not None else 'N/A'
            rows.append([
                str(item['name'])[:20],
                f"{item['current']:,}",
                f"{item['previous']:,}",
                f"{sign}{int(d['diff']):,}",
                pct_str,
            ])
        # 小計
        total_cur = sum(i['current'] for i in res_events)
        total_prev = sum(i['previous'] for i in res_events)
        total_diff = total_cur - total_prev
        sign = '+' if total_diff > 0 else ''
        pct = f"{sign}{(total_diff / total_prev * 100):.1f}%" if total_prev > 0 else 'N/A'
        rows.append(['小計', f"{total_cur:,}", f"{total_prev:,}", f"{sign}{total_diff:,}", pct])

        col_ws = [50 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm]
        self._comparison_table(margin, table_y - 5 * mm, headers, rows, col_ws)
        if not has_res_data:
            self._missing_data_notice(
                margin, table_y - (len(rows) + 1) * 7 * mm - 4 * mm,
                165 * mm, 9 * mm,
                '計測データ未出力: GA4で予約/電話イベントをキーイベント設定してください'
            )

        self._footer(company_name, page_num)

    def draw_ad_analysis(self, paid_channel_chart_bytes, cur_data, prev_data,
                         cur_kpis, prev_kpis, cur_label, prev_label, company_name='',
                         page_num=10):
        """広告分析"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('広告流入分析（Paid Search / Paid Social）')

        margin = 10 * mm
        content_top = PAGE_H - 30 * mm

        def split_paid(df):
            if df is None or df.empty:
                return {'web': 0, 'sns': 0}
            ch_col, val_col = df.columns[0], df.columns[1]
            vals = {'web': 0, 'sns': 0}
            for _, row in df.iterrows():
                ch = str(row[ch_col]).strip()
                ch_l = ch.lower()
                try:
                    v = int(float(row[val_col]))
                except Exception:
                    v = 0

                # 旧形式: default channel group
                # 新形式: source / medium (e.g. google / cpc)
                if (
                    ch in {'Paid Search', 'Cross-network'}
                    or ' / cpc' in ch_l
                    or 'paid search' in ch_l
                    or 'google ads' in ch_l
                    or 'display' in ch_l
                ):
                    vals['web'] += v
                elif (
                    ch == 'Paid Social'
                    or 'paid social' in ch_l
                    or 'facebook' in ch_l
                    or 'instagram' in ch_l
                    or 'line' in ch_l
                    or 'tiktok' in ch_l
                    or 'x.com' in ch_l
                    or 'twitter' in ch_l
                    or 'threads' in ch_l
                ):
                    vals['sns'] += v
            return vals

        cur_paid_ses = split_paid(cur_data.get('df_ch_ses'))
        prev_paid_ses = split_paid(prev_data.get('df_ch_ses'))
        cur_paid_new = split_paid(cur_data.get('df_ch_new'))
        prev_paid_new = split_paid(prev_data.get('df_ch_new'))

        cur_total_ses = int(cur_kpis.get('sessions', 0))
        prev_total_ses = int(prev_kpis.get('sessions', 0))
        cur_total_new = int(cur_kpis.get('new_users', 0))
        prev_total_new = int(prev_kpis.get('new_users', 0))

        # 全体CV数
        cur_total_cv = sum(int(v) for v in (cur_kpis.get('key_events') or {}).values())
        prev_total_cv = sum(int(v) for v in (prev_kpis.get('key_events') or {}).values())

        # 広告セッション合計
        cur_paid_total = cur_paid_ses['web'] + cur_paid_ses['sns']
        prev_paid_total = prev_paid_ses['web'] + prev_paid_ses['sns']

        # 広告セッション比率（実測値。CVの推定には使わない）
        #
        # 旧実装は「推定広告CV = 全体CV × 広告セッション比率」で按分し、
        # そこから「推定広告CVR = 推定広告CV ÷ 広告セッション」を算出していた。
        # これは式を展開すると 推定広告CVR ≒ 全体CVR となり、
        # 「広告のCVRは全体CVRと同じ」と仮定した値を「広告の成果」として
        # 表示する循環推計だった。広告の良し悪しを一切表現しないため廃止し、
        # 広告経由CV・CVRは「取得不能」と明示する。
        cur_ad_ratio = (cur_paid_total / cur_total_ses) if cur_total_ses > 0 else 0
        prev_ad_ratio = (prev_paid_total / prev_total_ses) if prev_total_ses > 0 else 0

        card_w = (PAGE_W - margin * 2 - 12 * mm) / 3
        card_h = 34 * mm
        y_cards = content_top + 1 * mm
        self._kpi_comparison_card(
            margin, y_cards, card_w, card_h,
            'Web広告 セッション', f'{cur_paid_ses["web"]:,}', f'{prev_paid_ses["web"]:,}',
            calc_diff(cur_paid_ses['web'], prev_paid_ses['web']), unit='sessions'
        )
        self._kpi_comparison_card(
            margin + card_w + 6 * mm, y_cards, card_w, card_h,
            'SNS広告 セッション', f'{cur_paid_ses["sns"]:,}', f'{prev_paid_ses["sns"]:,}',
            calc_diff(cur_paid_ses['sns'], prev_paid_ses['sns']), unit='sessions'
        )
        # 推定CVカードは循環推計だったため、実測できる「広告セッション比率」に差し替え
        self._kpi_comparison_card(
            margin + (card_w + 6 * mm) * 2, y_cards, card_w, card_h,
            '広告セッション比率', f'{cur_ad_ratio * 100:.1f}', f'{prev_ad_ratio * 100:.1f}',
            calc_diff(round(cur_ad_ratio * 1000), round(prev_ad_ratio * 1000)), unit='%'
        )

        chart_top = content_top - 40 * mm
        self._section_header(margin, chart_top, '広告チャネル別 セッション比較', '当月 vs 前月')
        if paid_channel_chart_bytes:
            self._image_bytes(paid_channel_chart_bytes, margin, chart_top - 4 * mm,
                              PAGE_W - 20 * mm, h=54 * mm)

        table_top = chart_top - 66 * mm
        self._section_header(margin, table_top, '広告 比率サマリ',
                             'CV・CVR・広告費はこのCSVでは算出不能')
        headers = ['指標', cur_label, prev_label, '差分']
        cur_web_ses_share = (cur_paid_ses['web'] / cur_total_ses * 100) if cur_total_ses > 0 else 0
        prev_web_ses_share = (prev_paid_ses['web'] / prev_total_ses * 100) if prev_total_ses > 0 else 0
        cur_sns_ses_share = (cur_paid_ses['sns'] / cur_total_ses * 100) if cur_total_ses > 0 else 0
        prev_sns_ses_share = (prev_paid_ses['sns'] / prev_total_ses * 100) if prev_total_ses > 0 else 0
        cur_web_new_share = (cur_paid_new['web'] / cur_total_new * 100) if cur_total_new > 0 else 0
        prev_web_new_share = (prev_paid_new['web'] / prev_total_new * 100) if prev_total_new > 0 else 0
        cur_sns_new_share = (cur_paid_new['sns'] / cur_total_new * 100) if cur_total_new > 0 else 0
        prev_sns_new_share = (prev_paid_new['sns'] / prev_total_new * 100) if prev_total_new > 0 else 0
        # 参照元/メディア別の代替表は「アクティブユーザー」であり新規ユーザーではない。
        # 新規ユーザーの代わりには使わず、取得不能と表示する。
        cur_new_ok = cur_data.get('ch_new_metric', 'new_users') == 'new_users'
        prev_new_ok = prev_data.get('ch_new_metric', 'new_users') == 'new_users'
        na_new = '取得不能（チャネル別の新規ユーザーなし）'

        def new_share_row(label, kind, cur_share, prev_share):
            cur_txt = (f'{cur_share:.2f}% ({cur_paid_new[kind]:,}/{cur_total_new:,})'
                       if cur_new_ok else na_new)
            prev_txt = (f'{prev_share:.2f}% ({prev_paid_new[kind]:,}/{prev_total_new:,})'
                        if prev_new_ok else na_new)
            diff_txt = f'{cur_share - prev_share:+.2f}pt' if (cur_new_ok and prev_new_ok) else '—'
            return [label, cur_txt, prev_txt, diff_txt]

        rows = [
            [
                'Web広告 セッション比率',
                f'{cur_web_ses_share:.2f}% ({cur_paid_ses["web"]:,}/{cur_total_ses:,})',
                f'{prev_web_ses_share:.2f}% ({prev_paid_ses["web"]:,}/{prev_total_ses:,})',
                f'{cur_web_ses_share - prev_web_ses_share:+.2f}pt',
            ],
            [
                'SNS広告 セッション比率',
                f'{cur_sns_ses_share:.2f}% ({cur_paid_ses["sns"]:,}/{cur_total_ses:,})',
                f'{prev_sns_ses_share:.2f}% ({prev_paid_ses["sns"]:,}/{prev_total_ses:,})',
                f'{cur_sns_ses_share - prev_sns_ses_share:+.2f}pt',
            ],
            new_share_row('Web広告 新規ユーザー比率', 'web', cur_web_new_share, prev_web_new_share),
            new_share_row('SNS広告 新規ユーザー比率', 'sns', cur_sns_new_share, prev_sns_new_share),
            [
                '広告経由 CV / CVR / 広告費 *',
                '取得不能（GA4に含まれず）',
                '取得不能（GA4に含まれず）',
                '—',
            ],
        ]
        col_ws = [52 * mm, 68 * mm, 68 * mm, 32 * mm]
        self._comparison_table(margin, table_top - 5 * mm, headers, rows, col_ws)

        # 注意書き
        note_y = table_top - 5 * mm - (len(rows) + 1) * 7 * mm - 3 * mm
        key_events_missing = (
            cur_kpis.get('key_events_status') == 'missing'
            or prev_kpis.get('key_events_status') == 'missing'
        )
        # プラットフォーム合計しか無い月は、内訳が空でも0件ではない（内訳取得不能）
        key_events_no_breakdown = not key_events_missing and (
            cur_kpis.get('key_events_status') == KEY_EVENTS_FALLBACK
            or prev_kpis.get('key_events_status') == KEY_EVENTS_FALLBACK
        )
        key_events_zero = (
            not key_events_missing and not key_events_no_breakdown
            and cur_total_cv == 0 and prev_total_cv == 0
        )
        if key_events_no_breakdown:
            used = self._text_wrapped(
                margin, note_y,
                '⚠ キーイベントのイベント別の内訳がCSVに含まれていないため、予約・電話アクションの件数は'
                '算出していません（0件ではありません）。',
                max_width=PAGE_W - margin * 2,
                font_size=6.5, color=colors.HexColor('#B5791F'), max_lines=2,
            )
            note_y -= max(used, 1) * 4.5 * mm
        elif key_events_missing:
            used = self._text_wrapped(
                margin, note_y,
                '⚠ キーイベント明細がCSVに未出力です。0件とは判定せず、発生回数の比較を行っていません。',
                max_width=PAGE_W - margin * 2,
                font_size=6.5, color=colors.HexColor('#B5791F'), max_lines=2,
            )
            note_y -= max(used, 1) * 4.5 * mm
        elif key_events_zero:
            used = self._text_wrapped(
                margin, note_y,
                '⚠ キーイベント明細は出力されていますが、当月・前月とも発生回数は0件です。',
                max_width=PAGE_W - margin * 2,
                font_size=6.5, color=colors.HexColor('#B5791F'), max_lines=2,
            )
            note_y -= max(used, 1) * 4.5 * mm
        self._text_wrapped(
            margin, note_y,
            '* このCSVにはチャネル別CV数・広告費が含まれないため、広告経由のCV・CVR・CPA・ROASは算出できません。'
            '旧版の「全体CV×広告セッション比率」による按分は、CVRが全体CVRと同じ値になるだけで広告の成果を'
            '表さないため廃止しました。判定には広告管理画面の費用・CVとの突合が必要です。',
            max_width=PAGE_W - margin * 2,
            font_size=6.5, color=COLOR_GRAY, max_lines=2,
        )

        self._footer(company_name, page_num)

    def draw_glossary(self, company_name='', page_num=10, country_note='', incomplete_note='',
                      key_events_note=''):
        """P8: 用語解説"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('用語解説・注記')

        margin = 12 * mm
        content_top = PAGE_H - 30 * mm

        terms = [
            ('アクティブユーザー',
             'サイトにアクセスし1秒以上表示したユーザー数。MAUは30日間のユニークユーザー数。'),
            ('新規ユーザー',
             '計測期間中に初めてサイトを訪問したユーザー数。Cookie基準のため端末・ブラウザ別に計測。'),
            ('セッション',
             'ユーザーの訪問から離脱までの一連の行動。30分以上操作がないと新規セッション扱い。'),
            ('エンゲージメント時間',
             'ユーザーがサイトをアクティブに利用していた時間。タブがフォアグラウンドの間のみ計測。'),
            ('キーイベント',
             'WEB予約クリック・電話タップ等、GA4で重要と設定した行動の発生回数。実際の予約成立件数やセッションCVRとは異なる。'),
            ('チャネル',
             '流入経路の分類。オーガニック検索/ダイレクト/有料検索/参照元/オーガニックSNS等。'),
            ('表示回数（PV）',
             'ページが表示された回数。同一ユーザーの複数回閲覧もその都度カウント。'),
            ('リピーター',
             '過去に訪問歴があり再訪問したユーザー。本レポートでは「30日アクティブユーザー − 新規ユーザー」で算出。'
             '両者は集計窓が1日ずれるため概算値であり、GA4のReturning usersとは完全には一致しない。'),
            ('コホート',
             '同一期間に初回訪問したユーザー群を追跡し、その後の再訪問率・行動変化を分析する手法。'),
            ('重なり率（スティッキネス）',
             '30日に来た人のうち直近7日・1日にも来ていた人の割合（WAU/MAU・DAU/WAU）。'
             '高い＝同じ人が繰り返し訪問、低い＝1回きりの訪問が中心。歯科では低くても問題ではなく、'
             '新患獲得が目的なら30日アクティブ・新規ユーザーの増減を優先して見る。コホート維持率とは別の指標。'),
            ('30日/7日/1日アクティブ',
             '期間末日から遡る直近30日(MAU)/7日(WAU)/1日(DAU)に訪問したユーザー数。'
             '30日アクティブは暦月の集計とは一致しない（31日ある月は初日が窓から外れる）。'),
        ]

        # 注記（先に行数を測り、用語表と注記がフッターに食い込まないよう配置を決める）
        # 国別データに基づく正確な対象範囲の注記（虚偽の「海外除外済み」は使わない）
        country_line = (country_note or
                        '※ 本レポートはGA4エクスポートCSVに基づいています。').lstrip('※ ').strip()
        notes = [
            '• 本レポートのデータはGA4（Google Analytics 4）からエクスポートされたCSVに基づいています。',
            '• ' + country_line,
            '• ユーザー数はCookieベースの推定値であり、実際のユニークユーザー数とは異なる場合があります。',
            '• 「推定接触量」はページ別の実測滞在時間ではなく、全体のエンゲージ時間をPV比で按分した推定値です。',
        ]
        if key_events_note:
            notes.append('• ' + key_events_note.lstrip('※ ').strip())
        notes.append(f'• 本レポートの生成バージョン: v{REPORT_VERSION}')
        if incomplete_note:
            notes.append('• ' + incomplete_note.lstrip('※ ').strip())

        note_w = PAGE_W - margin - 5 * mm - 12 * mm
        note_lines = [max(len(self._wrap_lines(n, note_w, 6.5, max_lines=3)), 1) for n in notes]
        total_lines = sum(note_lines)

        # 最終行のベースラインはフッター線（10mm）より上に保つ。
        # 注記が増えたときは行間 → 用語表の行高の順に詰める（通常時は従来どおり）。
        last_baseline_min = 11.5 * mm
        row_h = 11.5 * mm
        line_gap = 4.2 * mm
        min_gap = 3.4 * mm

        def first_baseline(rh):
            return content_top - 14 * mm - len(terms) * rh - 4 * mm - 7 * mm

        steps = max(total_lines - 1, 1)
        if first_baseline(row_h) - (total_lines - 1) * line_gap < last_baseline_min:
            line_gap = max(min_gap, (first_baseline(row_h) - last_baseline_min) / steps)
            if first_baseline(row_h) - (total_lines - 1) * line_gap < last_baseline_min:
                row_h = max(
                    10.8 * mm,
                    (content_top - 25 * mm - last_baseline_min - (total_lines - 1) * line_gap)
                    / len(terms),
                )

        self._section_header(margin, content_top, '用語解説')

        for i, (term, desc) in enumerate(terms):
            ty = content_top - 14 * mm - i * row_h

            bg = COLOR_CARD_BG if i % 2 == 0 else colors.HexColor('#F5F8FC')
            self._rect(margin, ty - row_h + 4 * mm, PAGE_W - 24 * mm, row_h,
                       fill_color=bg, stroke_color=colors.HexColor('#DDE6EF'))

            # 用語名
            self._text(margin + 4 * mm, ty, term,
                       font_size=8.5, color=COLOR_PRIMARY)
            # 説明（1行で表示）
            self._text(margin + 4 * mm, ty - 6.2 * mm, desc,
                       font_size=6.1, color=COLOR_DARK)

        # 注記セクション
        note_top = content_top - 14 * mm - len(terms) * row_h - 4 * mm
        self._section_header(margin, note_top, '注記')

        # 国別注記が2行になっても、生成バージョンまでフッターより上に収める。
        ny = note_top - 7 * mm
        for note in notes:
            used = self._text_wrapped(margin + 5 * mm, ny, note,
                                      max_width=note_w,
                                      font_size=6.5, color=COLOR_DARK, max_lines=3)
            ny -= max(used, 1) * line_gap

        self._footer(company_name, page_num)


# ─────────────────────────────────────────────────────
# メイン生成関数
# ─────────────────────────────────────────────────────
def generate_compare_report(cur_csv, prev_csv, output_path: str,
                            company_name: str = '',
                            department: str = '',
                            logo_path: str = None,
                            detailed: bool = False,
                            single_mode: bool = False,
                            site_url: str = '') -> str:
    """2つのGA4 CSVから比較PDFレポートを生成。

    cur_csv / prev_csv: ファイルパス(str) or bytes
    """
    # 1. 両CSVをparse
    cur_data = parse_ga4_csv(cur_csv)
    prev_data = parse_ga4_csv(prev_csv)

    cur_meta = cur_data['meta']
    prev_meta = prev_data['meta']
    cur_kpis = cur_data['kpis']
    prev_kpis = prev_data['kpis']

    cur_label = cur_meta.get('month_label', '当月')
    prev_label = prev_meta.get('month_label', '前月')

    # 2. 差分計算
    compared_kpis = compare_kpis(cur_kpis, prev_kpis)

    # チャネルの分類方法（チャネルグループ / 参照元・メディア）が月ごとに違う場合は比較しない
    # （列名が違うと KeyError になり、同名でも別の分類を前月比として並べることになるため）
    channel_mismatch = (
        not single_mode
        and cur_data['df_ch_ses'] is not None and prev_data['df_ch_ses'] is not None
        and cur_data.get('ch_ses_dimension') != prev_data.get('ch_ses_dimension')
    )
    if channel_mismatch:
        compared_channels = pd.DataFrame()
    else:
        compared_channels = compare_dataframes(
            cur_data['df_ch_ses'], prev_data['df_ch_ses'],
            cur_data['df_ch_ses'].columns[0] if cur_data['df_ch_ses'] is not None else 'channel',
            cur_data['df_ch_ses'].columns[1] if cur_data['df_ch_ses'] is not None else 'value',
        ) if cur_data['df_ch_ses'] is not None or prev_data['df_ch_ses'] is not None else pd.DataFrame()

    key_events_comparable, _ = key_events_comparison(
        cur_kpis.get('key_events_status'), prev_kpis.get('key_events_status'))
    compared_kevents = compare_key_events(
        cur_kpis.get('key_events', {}),
        prev_kpis.get('key_events', {}),
    ) if key_events_comparable else []
    # 完了到達・求人関連・未分類（別カテゴリ）。予約・電話等とは別表・別集計で示す。
    # 前月が比較できない場合も当月の明細は表示し、増減だけ「比較不能」にする。
    side_tables = build_side_tables(cur_kpis, prev_kpis, single_mode)
    compared_recruit = next((t['items'] for t in side_tables if t['kind'] == 'recruit'), [])

    # 3. チャート生成
    daily_chart = make_daily_overlay_chart(
        cur_data['df_active'], prev_data['df_active'], cur_label, prev_label)

    channel_chart = None if channel_mismatch else make_channel_grouped_bar(
        cur_data['df_ch_ses'], prev_data['df_ch_ses'], cur_label, prev_label)

    kev_chart = make_key_events_grouped_bar(
        cur_kpis.get('key_events', {}), prev_kpis.get('key_events', {}),
        cur_label, prev_label,
        title=('キーイベント発生回数比較（求人関連を除く）' if compared_recruit
               else 'キーイベント発生回数比較'),
    ) if key_events_comparable else None

    pages_chart = make_top_pages_compare_chart(
        cur_data['df_pages'], prev_data['df_pages'])

    engage_chart = make_engagement_overlay_chart(
        cur_data['df_engage'], prev_data['df_engage'], cur_label, prev_label)

    # KPIカードと同じユーザー数を使う（日別合計は重複計上のため使わない）
    user_chart = make_new_vs_returning_compare(
        cur_kpis.get('new_users', 0), cur_kpis.get('active_users', 0),
        prev_kpis.get('new_users', 0), prev_kpis.get('active_users', 0),
        cur_label, prev_label)

    cohort_chart = make_cohort_chart(
        cur_data['df_trend'], prev_data['df_trend'], cur_label, prev_label)

    reservation_chart = make_reservation_compare_chart(
        cur_kpis.get('key_events', {}), prev_kpis.get('key_events', {}),
        cur_label, prev_label)
    transition_rate_chart = make_reservation_transition_rate_chart(
        cur_kpis.get('key_events', {}),
        prev_kpis.get('key_events', {}),
        int(cur_kpis.get('sessions', 0)),
        int(prev_kpis.get('sessions', 0)),
        cur_label,
        prev_label,
        show_diff=compared_kpis['key_events_total'].get('incomparable_reason') != 'treatment_mismatch',
    ) if key_events_comparable else None
    paid_channel_chart = make_paid_channels_grouped_bar(
        cur_data.get('df_ch_ses'),
        prev_data.get('df_ch_ses'),
        cur_label,
        prev_label,
    )

    # 4. ハイライト計算
    highlights = compute_highlights(compared_kpis, compared_channels, compared_kevents,
                                    cur_pages=cur_data.get('df_pages'),
                                    prev_pages=prev_data.get('df_pages'))

    # 4.5 データ連動の注記（国別の対象範囲・計測期間の完全性）
    country_note = country_note_text(cur_meta)
    # 用語解説ページは行幅が狭いため簡潔版を使う
    country_note_short = country_note_text(cur_meta, compact=True)
    incomplete_note = incomplete_note_text(cur_meta, prev_meta, cur_label, prev_label,
                                           single_mode=single_mode)
    key_events_note = key_events_inferred_note(cur_kpis, prev_kpis)

    # 5. PDF生成
    rc = CompareReportCanvas(output_path)

    # P1: 表紙
    rc.draw_cover(cur_meta, prev_meta, company_name, department, logo_path,
                  single_mode=single_mode, site_url=site_url,
                  country_note=country_note, incomplete_note=incomplete_note)
    rc.new_page()

    # P2: KPI比較サマリ
    rc.draw_summary_compare(cur_meta, prev_meta, cur_kpis, prev_kpis,
                            compared_kpis, daily_chart, company_name,
                            incomplete_note=incomplete_note, single_mode=single_mode)
    rc.new_page()

    # P3: チャネル比較
    rc.draw_channels_compare(channel_chart, compared_channels,
                             cur_label, prev_label, company_name,
                             channel_mismatch=channel_mismatch,
                             cur_dimension=cur_data.get('ch_ses_dimension'),
                             prev_dimension=prev_data.get('ch_ses_dimension'))
    rc.new_page()

    # P4: キーイベント比較 + 移行率
    rc.draw_key_events_compare(kev_chart, compared_kevents,
                               cur_label, prev_label, company_name,
                               transition_rate_chart_bytes=transition_rate_chart,
                               cur_kpis=cur_kpis, prev_kpis=prev_kpis,
                               side_tables=side_tables, single_mode=single_mode)
    rc.new_page()

    # P5: ページ比較
    rc.draw_pages_compare(
        pages_chart,
        cur_data['df_pages'],
        prev_data['df_pages'],
        company_name,
    )
    rc.new_page()

    if detailed:
        # P6: 人気ページエンゲージ時間（推定）
        rc.draw_page_engagement_page(
            cur_data['df_pages'],
            prev_data['df_pages'],
            cur_kpis,
            prev_kpis,
            company_name,
        )
        rc.new_page()

        # P7: ユーザー行動比較
        rc.draw_user_behavior_compare(engage_chart, user_chart,
                                      cur_kpis, prev_kpis, compared_kpis, company_name, page_num=7)
        rc.new_page()

        # P8: アクティブユーザー期間別比較
        rc.draw_cohort_compare(cohort_chart, cur_data['df_trend'], prev_data['df_trend'],
                               cur_label, prev_label, company_name, page_num=8,
                               incomplete_note=incomplete_note)
        rc.new_page()

        # P9: 広告分析
        rc.draw_ad_analysis(
            paid_channel_chart,
            cur_data,
            prev_data,
            cur_kpis,
            prev_kpis,
            cur_label,
            prev_label,
            company_name,
            page_num=9,
        )
        rc.new_page()

        # P10: 総括（最後の用語解説の直前）
        rc.draw_summary_page(
            highlights,
            cur_meta,
            prev_meta,
            company_name=company_name,
            page_num=10,
            single_mode=single_mode,
            cur_kpis=cur_kpis,
            prev_kpis=prev_kpis,
            country_note=country_note,
            incomplete_note=incomplete_note,
        )
        rc.new_page()

        # P11: 用語解説・注記
        rc.draw_glossary(company_name=company_name, page_num=11,
                         country_note=country_note_short, incomplete_note=incomplete_note,
                         key_events_note=key_events_note)
    else:
        # P6: 上位ページ エンゲージ時間分析
        rc.draw_page_engagement_page(
            cur_data['df_pages'],
            prev_data['df_pages'],
            cur_kpis,
            prev_kpis,
            company_name,
        )
        rc.new_page()

        # P7: ユーザー行動比較
        rc.draw_user_behavior_compare(engage_chart, user_chart,
                                      cur_kpis, prev_kpis, compared_kpis, company_name, page_num=7)
        rc.new_page()

        # P8: 総括（最後の用語解説の直前）
        rc.draw_summary_page(
            highlights,
            cur_meta,
            prev_meta,
            company_name=company_name,
            page_num=8,
            single_mode=single_mode,
            cur_kpis=cur_kpis,
            prev_kpis=prev_kpis,
            country_note=country_note,
            incomplete_note=incomplete_note,
        )
        rc.new_page()

        # P9: 用語解説・注記（標準版）
        rc.draw_glossary(company_name=company_name, page_num=9,
                         country_note=country_note_short, incomplete_note=incomplete_note,
                         key_events_note=key_events_note)

    rc.save()
    return output_path
