#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
比較チャート生成 — 当月 vs 前月の2系列チャート
"""

import io
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import os
# 日本語フォント: リポジトリ同梱の ipaexg.ttf を直接登録する
from matplotlib import font_manager as _fm
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCAL_JP_FONT = os.path.join(_BASE_DIR, 'ipaexg.ttf')
if os.path.exists(_LOCAL_JP_FONT):
    _fm.fontManager.addfont(_LOCAL_JP_FONT)
    matplotlib.rcParams['font.family'] = 'IPAexGothic'

# matplotlib カラー
MPL_PRIMARY   = '#1A6FAB'
MPL_SECONDARY = '#4DA3D4'
MPL_ACCENT    = '#F4A836'
MPL_BG        = '#F5F8FC'
MPL_GRID      = '#DDE6EF'

# 比較用追加カラー
MPL_PREV      = '#B0D4F1'   # 前月データ
MPL_INCREASE  = '#2E8B57'   # 増加
MPL_DECREASE  = '#D9534F'   # 減少


def fig_to_bytes(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _common_style(ax):
    """共通のグラフスタイル設定"""
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color(MPL_GRID)
    ax.spines['bottom'].set_color(MPL_GRID)
    ax.tick_params(colors='#555', labelsize=8)


def make_daily_overlay_chart(cur_active, prev_active, cur_label: str, prev_label: str,
                             width_inch=11, height_inch=2.8) -> bytes:
    """日別アクティブユーザー重ね描き折れ線グラフ（横幅最大）"""
    fig, ax = plt.subplots(figsize=(width_inch, height_inch), facecolor=MPL_BG)
    ax.set_facecolor(MPL_BG)

    if cur_active is not None:
        days_cur = cur_active.iloc[:, 0].astype(int) + 1
        ax.plot(days_cur, cur_active.iloc[:, 1], color=MPL_PRIMARY, linewidth=2.2,
                marker='o', markersize=3, label=f'{cur_label}', zorder=3)
        ax.fill_between(days_cur, cur_active.iloc[:, 1], alpha=0.12, color=MPL_PRIMARY)

    if prev_active is not None:
        days_prev = prev_active.iloc[:, 0].astype(int) + 1
        ax.plot(days_prev, prev_active.iloc[:, 1], color=MPL_PREV, linewidth=1.8,
                marker='o', markersize=2, linestyle='--', label=f'{prev_label}', zorder=2)

    ax.set_xlabel('日', fontsize=8)
    ax.set_ylabel('ユーザー数', fontsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    ax.grid(True, color=MPL_GRID, linewidth=0.8, linestyle='-')
    _common_style(ax)
    ax.legend(fontsize=8, framealpha=0.9, facecolor='white', edgecolor='#DDE6EF',
              loc='lower right')
    fig.subplots_adjust(left=0.05, right=0.98, top=0.95, bottom=0.15)
    return fig_to_bytes(fig)


def make_channel_grouped_bar(cur_ch_ses, prev_ch_ses, cur_label: str, prev_label: str,
                             width_inch=8, height_inch=4) -> bytes:
    """チャネル別セッション比較横棒グラフ（グループ化）"""
    rename_map = {
        'Organic Search': 'オーガニック検索',
        'Paid Search':    '有料検索(広告)',
        'Direct':         'ダイレクト',
        'Referral':       '参照元',
        'Organic Social': 'オーガニックSNS',
        'Unassigned':     '未割り当て',
        'Cross-network':  'クロスネットワーク',
    }

    def prep_df(df):
        if df is None:
            return pd.DataFrame(columns=['channel', 'value'])
        col_ch, col_val = df.columns[0], df.columns[1]
        d = df[[col_ch, col_val]].copy()
        d.columns = ['channel', 'value']
        d['channel'] = d['channel'].map(lambda x: rename_map.get(x, x))
        return d

    cur = prep_df(cur_ch_ses)
    prev = prep_df(prev_ch_ses)

    # 外部結合
    merged = pd.merge(cur, prev, on='channel', how='outer', suffixes=('_cur', '_prev')).fillna(0)
    merged = merged.sort_values('value_cur', ascending=True)

    fig, ax = plt.subplots(figsize=(width_inch, height_inch), facecolor='white')
    ax.set_facecolor('white')

    y = np.arange(len(merged))
    bar_h = 0.35

    ax.barh(y + bar_h / 2, merged['value_cur'], bar_h, label=cur_label,
            color=MPL_PRIMARY, edgecolor='none')
    ax.barh(y - bar_h / 2, merged['value_prev'], bar_h, label=prev_label,
            color=MPL_PREV, edgecolor='none')

    ax.set_yticks(y)
    ax.set_yticklabels(merged['channel'])
    max_val = max(merged['value_cur'].max(), merged['value_prev'].max())
    ax.set_xlim(0, max_val * 1.25 if max_val > 0 else 1)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    ax.grid(True, axis='x', color=MPL_GRID, linewidth=0.8)
    _common_style(ax)
    ax.legend(fontsize=9, framealpha=0, loc='lower right')
    ax.set_title('チャネル別セッション数（当月 vs 前月）', fontsize=11, pad=8, color='#1E2B3A')
    fig.tight_layout()
    return fig_to_bytes(fig)


def make_key_events_grouped_bar(cur_kev, prev_kev, cur_label: str, prev_label: str,
                                width_inch=8, height_inch=3.5) -> bytes:
    """キーイベント比較縦棒グラフ"""
    all_keys = sorted(set(list(cur_kev.keys()) + list(prev_kev.keys())))
    if not all_keys:
        return None

    cur_vals = [cur_kev.get(k, 0) for k in all_keys]
    prev_vals = [prev_kev.get(k, 0) for k in all_keys]

    fig, ax = plt.subplots(figsize=(width_inch, height_inch), facecolor='white')
    ax.set_facecolor('white')

    x = np.arange(len(all_keys))
    bar_w = 0.35

    ax.bar(x - bar_w / 2, cur_vals, bar_w, label=cur_label, color=MPL_PRIMARY, edgecolor='none')
    ax.bar(x + bar_w / 2, prev_vals, bar_w, label=prev_label, color=MPL_PREV, edgecolor='none')

    # 値ラベル
    for i, (cv, pv) in enumerate(zip(cur_vals, prev_vals)):
        if cv > 0:
            ax.text(i - bar_w / 2, cv + 0.3, f'{cv:,}', ha='center', va='bottom',
                    fontsize=8, color=MPL_PRIMARY, fontweight='bold')
        if pv > 0:
            ax.text(i + bar_w / 2, pv + 0.3, f'{pv:,}', ha='center', va='bottom',
                    fontsize=8, color='#6B8EAD')

    ax.set_xticks(x)
    ax.set_xticklabels(all_keys, fontsize=8, rotation=15)
    max_val = max(max(cur_vals, default=0), max(prev_vals, default=0))
    ax.set_ylim(0, max_val * 1.3 if max_val > 0 else 1)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    ax.grid(True, axis='y', color=MPL_GRID, linewidth=0.8)
    _common_style(ax)
    ax.legend(fontsize=9, framealpha=0, loc='upper right')
    ax.set_title('キーイベント（コンバージョン）比較', fontsize=11, pad=8, color='#1E2B3A')
    fig.tight_layout()
    return fig_to_bytes(fig)


def make_top_pages_compare_chart(cur_pages, prev_pages, top_n=20,
                                 width_inch=7, height_inch=8) -> bytes:
    """上位ページ比較横棒グラフ"""
    if cur_pages is None:
        return None

    # 選定基準は表側（report_logic_compare）と共通で max(当月PV, 前月PV)。
    # 当月PV降順だと当月0PVのページがグラフからも消え、消失を検知できなかった。
    from compare_utils import select_top_pages
    selected = select_top_pages(cur_pages, prev_pages, top_n)
    if selected.empty:
        return None

    page_names = selected['page'].tolist()
    cur_vals = selected['cur'].tolist()
    prev_vals = selected['prev'].tolist()

    # ラベル短縮
    def shorten(title, max_len=20):
        import re
        title = str(title)
        parts = [p.strip() for p in re.split(r'[｜|]', title) if p.strip()]
        if len(parts) >= 2:
            s = parts[0]
        else:
            s = parts[-1] if parts else title
        return s[:max_len] + '…' if len(s) > max_len else s

    labels = [shorten(p) for p in page_names]

    fig, ax = plt.subplots(figsize=(width_inch, height_inch), facecolor='white')
    ax.set_facecolor('white')

    y = np.arange(len(labels))
    bar_h = 0.35

    # 逆順表示（上位が上に来る）
    labels_r = labels[::-1]
    cur_vals_r = cur_vals[::-1]
    prev_vals_r = prev_vals[::-1]
    y_r = np.arange(len(labels_r))

    ax.barh(y_r + bar_h / 2, cur_vals_r, bar_h, label='当月', color=MPL_PRIMARY, edgecolor='none')
    ax.barh(y_r - bar_h / 2, prev_vals_r, bar_h, label='前月', color=MPL_PREV, edgecolor='none')

    ax.set_yticks(y_r)
    ax.set_yticklabels(labels_r)
    max_val = max(max(cur_vals, default=0), max(prev_vals, default=0))
    ax.set_xlim(0, max_val * 1.2 if max_val > 0 else 1)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    ax.grid(True, axis='x', color=MPL_GRID, linewidth=0.8)
    _common_style(ax)
    ax.legend(fontsize=9, framealpha=0, loc='lower right')
    ax.set_title(f'上位{top_n}ページ 表示回数比較', fontsize=11, pad=8, color='#1E2B3A')
    fig.tight_layout()
    return fig_to_bytes(fig)


def make_engagement_overlay_chart(cur_engage, prev_engage, cur_label: str, prev_label: str,
                                  width_inch=9, height_inch=3) -> bytes:
    """エンゲージメント時間重ね描き折れ線"""
    fig, ax = plt.subplots(figsize=(width_inch, height_inch), facecolor=MPL_BG)
    ax.set_facecolor(MPL_BG)

    if cur_engage is not None:
        days = cur_engage.iloc[:, 0].astype(int) + 1
        ax.plot(days, cur_engage.iloc[:, 1], color=MPL_PRIMARY, linewidth=2.2,
                marker='o', markersize=3, label=cur_label, zorder=3)

    if prev_engage is not None:
        days_p = prev_engage.iloc[:, 0].astype(int) + 1
        ax.plot(days_p, prev_engage.iloc[:, 1], color=MPL_PREV, linewidth=1.8,
                marker='o', markersize=2, linestyle='--', label=prev_label, zorder=2)

    ax.set_xlabel('日', fontsize=9)
    ax.set_ylabel('平均エンゲージメント時間（秒）', fontsize=9)
    ax.grid(True, color=MPL_GRID, linewidth=0.8)
    _common_style(ax)
    ax.legend(fontsize=9, framealpha=0, loc='upper right')
    ax.set_title('日別エンゲージメント時間推移（当月 vs 前月）', fontsize=11, pad=8, color='#1E2B3A')
    fig.tight_layout()
    return fig_to_bytes(fig)


def make_new_vs_returning_compare(cur_new, cur_active, prev_new, prev_active,
                                  cur_label: str, prev_label: str,
                                  width_inch=6, height_inch=3.5) -> bytes:
    """新規 vs リピーター比較（積み上げ棒グラフ）"""
    def get_vals(df_new, df_active):
        new_total = int(df_new.iloc[:, 1].sum()) if df_new is not None else 0
        active_total = int(df_active.iloc[:, 1].sum()) if df_active is not None else 0
        returning = max(active_total - new_total, 0)
        return new_total, returning

    cur_new_v, cur_ret = get_vals(cur_new, cur_active)
    prev_new_v, prev_ret = get_vals(prev_new, prev_active)

    fig, ax = plt.subplots(figsize=(width_inch, height_inch), facecolor='white')
    ax.set_facecolor('white')

    labels = [cur_label, prev_label]
    new_vals = [cur_new_v, prev_new_v]
    ret_vals = [cur_ret, prev_ret]

    x = np.arange(len(labels))
    bar_w = 0.45

    bars1 = ax.bar(x, new_vals, bar_w, label='新規ユーザー', color=MPL_PRIMARY, edgecolor='none')
    bars2 = ax.bar(x, ret_vals, bar_w, bottom=new_vals, label='リピーター', color=MPL_SECONDARY, edgecolor='none')

    # 値ラベル
    for i, (nv, rv) in enumerate(zip(new_vals, ret_vals)):
        ax.text(i, nv / 2, f'{nv:,}', ha='center', va='center', fontsize=9, color='white', fontweight='bold')
        if rv > 0:
            ax.text(i, nv + rv / 2, f'{rv:,}', ha='center', va='center', fontsize=9, color='white', fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    ax.grid(True, axis='y', color=MPL_GRID, linewidth=0.8)
    _common_style(ax)
    ax.legend(fontsize=9, framealpha=0, loc='upper right')
    ax.set_title('新規 vs リピーター比較', fontsize=11, pad=8, color='#1E2B3A')
    fig.tight_layout()
    return fig_to_bytes(fig)


def make_cohort_chart(cur_trend, prev_trend, cur_label: str, prev_label: str,
                      width_inch=9.4, height_inch=3.5) -> bytes:
    """コホート分析チャート（30日/7日/1日アクティブユーザー推移の比較）"""
    fig, axes = plt.subplots(1, 3, figsize=(width_inch, height_inch), facecolor=MPL_BG)

    period_labels = ['30日アクティブ', '7日アクティブ', '1日アクティブ']
    col_indices = [1, 2, 3]  # 30日, 7日, 1日

    for idx, (ax, plabel, ci) in enumerate(zip(axes, period_labels, col_indices)):
        ax.set_facecolor(MPL_BG)

        if cur_trend is not None and ci < len(cur_trend.columns):
            days = cur_trend.iloc[:, 0].astype(int) + 1
            ax.plot(days, cur_trend.iloc[:, ci], color=MPL_PRIMARY, linewidth=2,
                    marker='o', markersize=2, label=cur_label, zorder=3)

        if prev_trend is not None and ci < len(prev_trend.columns):
            days_p = prev_trend.iloc[:, 0].astype(int) + 1
            ax.plot(days_p, prev_trend.iloc[:, ci], color=MPL_PREV, linewidth=1.5,
                    marker='o', markersize=1.5, linestyle='--', label=prev_label, zorder=2)

        ax.set_title(plabel, fontsize=9, color='#1E2B3A')
        ax.set_xlabel('日', fontsize=7)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
        ax.grid(True, color=MPL_GRID, linewidth=0.6)
        _common_style(ax)
        ax.tick_params(labelsize=7)
        if idx == 0:
            ax.legend(fontsize=7, framealpha=0)

    fig.suptitle('アクティブユーザー期間別比較（当月 vs 前月）', fontsize=11, color='#1E2B3A', y=1.04)
    # 3グラフ間の間隔を広めに確保
    fig.subplots_adjust(left=0.05, right=0.98, top=0.82, bottom=0.18, wspace=0.58)
    return fig_to_bytes(fig)


def make_reservation_compare_chart(cur_kev, prev_kev, cur_label: str, prev_label: str,
                                   width_inch=8, height_inch=4) -> bytes:
    """予約完了（キーイベント）比較チャート — WEB予約・電話系を抽出"""
    # 予約・電話系のイベントを抽出
    reservation_keywords = ['予約', '電話', 'tel', 'TEL']

    def filter_reservations(kev_dict):
        result = {}
        for k, v in kev_dict.items():
            if any(kw in str(k).lower() for kw in ['予約', '電話', 'tel']):
                result[k] = v
        if not result:
            result = kev_dict  # フォールバック: 全件
        return result

    cur_res = filter_reservations(cur_kev)
    prev_res = filter_reservations(prev_kev)
    all_keys = sorted(set(list(cur_res.keys()) + list(prev_res.keys())))

    if not all_keys:
        return None

    cur_vals = [cur_res.get(k, 0) for k in all_keys]
    prev_vals = [prev_res.get(k, 0) for k in all_keys]

    fig, ax = plt.subplots(figsize=(width_inch, height_inch), facecolor='white')
    ax.set_facecolor('white')

    x = np.arange(len(all_keys))
    bar_w = 0.35

    ax.bar(x - bar_w / 2, cur_vals, bar_w, label=cur_label, color=MPL_PRIMARY, edgecolor='none')
    ax.bar(x + bar_w / 2, prev_vals, bar_w, label=prev_label, color=MPL_PREV, edgecolor='none')

    for i, (cv, pv) in enumerate(zip(cur_vals, prev_vals)):
        if cv > 0:
            ax.text(i - bar_w / 2, cv + 0.3, f'{cv:,}', ha='center', va='bottom',
                    fontsize=9, color=MPL_PRIMARY, fontweight='bold')
        if pv > 0:
            ax.text(i + bar_w / 2, pv + 0.3, f'{pv:,}', ha='center', va='bottom',
                    fontsize=9, color='#6B8EAD')

    # 差分表示
    for i, (cv, pv) in enumerate(zip(cur_vals, prev_vals)):
        diff = cv - pv
        if diff != 0:
            sign = '+' if diff > 0 else ''
            clr = MPL_INCREASE if diff > 0 else MPL_DECREASE
            max_v = max(cv, pv)
            ax.text(i, max_v + max(max(cur_vals, default=0), max(prev_vals, default=0)) * 0.08,
                    f'{sign}{diff:,}', ha='center', va='bottom', fontsize=8, color=clr)

    ax.set_xticks(x)
    ax.set_xticklabels(all_keys, fontsize=8, rotation=15)
    max_val = max(max(cur_vals, default=0), max(prev_vals, default=0))
    ax.set_ylim(0, max_val * 1.4 if max_val > 0 else 1)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{int(x):,}'))
    ax.grid(True, axis='y', color=MPL_GRID, linewidth=0.8)
    _common_style(ax)
    ax.legend(fontsize=9, framealpha=0, loc='upper right')
    ax.set_title('予約・電話 コンバージョン比較', fontsize=11, pad=8, color='#1E2B3A')
    fig.tight_layout()
    return fig_to_bytes(fig)


def make_reservation_transition_rate_chart(
    cur_kev,
    prev_kev,
    cur_sessions: int,
    prev_sessions: int,
    cur_label: str,
    prev_label: str,
    width_inch=4.4,
    height_inch=3.0,
) -> bytes:
    """予約完了までの移行率（予約系CV / セッション）比較チャート"""

    def reservation_total(kev_dict):
        total = 0
        for name, count in kev_dict.items():
            key = str(name).lower()
            if '予約' in key or '電話' in key or 'tel' in key:
                total += int(count)
        return total

    cur_res_total = reservation_total(cur_kev or {})
    prev_res_total = reservation_total(prev_kev or {})

    cur_rate = (cur_res_total / cur_sessions * 100) if cur_sessions and cur_sessions > 0 else 0
    prev_rate = (prev_res_total / prev_sessions * 100) if prev_sessions and prev_sessions > 0 else 0
    rate_diff = cur_rate - prev_rate

    labels = [cur_label, prev_label]
    rates = [cur_rate, prev_rate]
    counts = [cur_res_total, prev_res_total]
    sessions = [int(cur_sessions or 0), int(prev_sessions or 0)]

    fig, ax = plt.subplots(figsize=(width_inch, height_inch), facecolor='white')
    ax.set_facecolor('white')

    x = np.arange(2)
    bars = ax.bar(x, rates, width=0.52, color=[MPL_PRIMARY, MPL_PREV], edgecolor='none')

    max_rate = max(rates) if rates else 0
    ymax = max(max_rate * 1.35, 1.2)
    ax.set_ylim(0, ymax)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f'{v:.1f}%'))
    ax.grid(True, axis='y', color=MPL_GRID, linewidth=0.8)
    _common_style(ax)
    ax.set_title('予約完了までの移行率', fontsize=11, pad=8, color='#1E2B3A')

    # 各棒の上に率、棒内に分母分子を表示（重なり回避）
    for i, b in enumerate(bars):
        h = b.get_height()
        ax.text(
            b.get_x() + b.get_width() / 2,
            h + ymax * 0.07,
            f'{rates[i]:.2f}%',
            ha='center',
            va='bottom',
            fontsize=9,
            color='#1E2B3A',
            fontweight='bold',
        )
        count_color = 'white' if i == 0 else '#1E2B3A'
        ax.text(
            b.get_x() + b.get_width() / 2,
            max(h * 0.1, 0.22),
            f'{counts[i]:,}/{sessions[i]:,}',
            ha='center',
            va='bottom',
            fontsize=7,
            color=count_color,
        )

    # 差分注記
    sign = '+' if rate_diff > 0 else ''
    diff_color = MPL_INCREASE if rate_diff > 0 else MPL_DECREASE if rate_diff < 0 else '#8A9BB0'
    ax.text(
        0.98,
        0.96,
        f'差分: {sign}{rate_diff:.2f}pt',
        transform=ax.transAxes,
        ha='right',
        va='top',
        fontsize=8,
        color=diff_color,
    )

    fig.tight_layout()
    return fig_to_bytes(fig)


def make_paid_channels_grouped_bar(cur_ch_ses, prev_ch_ses, cur_label: str, prev_label: str,
                                   width_inch=8.4, height_inch=3.6) -> bytes:
    """広告チャネル（Web広告 / SNS広告）比較"""
    target_channels = ['Paid Search', 'Paid Social', 'Cross-network']

    def pick(df):
        if df is None:
            return {k: 0 for k in target_channels}
        ch_col, val_col = df.columns[0], df.columns[1]
        vals = {k: 0 for k in target_channels}
        for _, row in df.iterrows():
            ch = str(row[ch_col]).strip()
            if ch in vals:
                vals[ch] = int(row[val_col])
        return vals

    cur_vals_map = pick(cur_ch_ses)
    prev_vals_map = pick(prev_ch_ses)
    cur_web = cur_vals_map['Paid Search'] + cur_vals_map['Cross-network']
    prev_web = prev_vals_map['Paid Search'] + prev_vals_map['Cross-network']
    cur_sns = cur_vals_map['Paid Social']
    prev_sns = prev_vals_map['Paid Social']

    cur_vals = [cur_web, cur_sns]
    prev_vals = [prev_web, prev_sns]
    labels = ['Web広告', 'SNS広告']

    fig, ax = plt.subplots(figsize=(width_inch, height_inch), facecolor='white')
    ax.set_facecolor('white')

    x = np.arange(len(labels))
    bar_w = 0.34
    ax.bar(x - bar_w / 2, cur_vals, bar_w, label=cur_label, color=MPL_PRIMARY, edgecolor='none')
    ax.bar(x + bar_w / 2, prev_vals, bar_w, label=prev_label, color=MPL_PREV, edgecolor='none')

    for i, (cv, pv) in enumerate(zip(cur_vals, prev_vals)):
        ymax = max(cv, pv)
        if cv > 0:
            ax.text(i - bar_w / 2, cv + max(ymax * 0.02, 2), f'{cv:,}',
                    ha='center', va='bottom', fontsize=8, color=MPL_PRIMARY, fontweight='bold')
        if pv > 0:
            ax.text(i + bar_w / 2, pv + max(ymax * 0.02, 2), f'{pv:,}',
                    ha='center', va='bottom', fontsize=8, color='#6B8EAD')

    max_v = max(max(cur_vals, default=0), max(prev_vals, default=0))
    ax.set_ylim(0, max(max_v * 1.28, 10))
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f'{int(v):,}'))
    ax.grid(True, axis='y', color=MPL_GRID, linewidth=0.8)
    _common_style(ax)
    ax.legend(fontsize=9, framealpha=0, loc='upper left')
    ax.set_title('広告流入 セッション比較（Web広告 / SNS広告）', fontsize=11, pad=8, color='#1E2B3A')
    fig.tight_layout()
    return fig_to_bytes(fig)
