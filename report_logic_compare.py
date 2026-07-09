#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GA4 月次比較レポート PDF生成 — CompareReportCanvas (7ページ)
"""

import os
import io
import re
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

from csv_parser import parse_ga4_csv
from compare_utils import (
    calc_diff, format_delta, delta_color,
    compare_kpis, compare_dataframes, compare_key_events, compute_highlights,
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

    def _comparison_table(self, x, y, headers: list, rows: list, col_widths: list):
        """比較テーブル描画。rows = [[cell, cell, ...], ...]"""
        row_h = 7 * mm
        total_w = sum(col_widths)

        for ri, row in enumerate([headers] + rows):
            row_y = y - ri * row_h
            is_header = ri == 0

            bg = COLOR_PRIMARY if is_header else \
                (COLOR_CARD_BG if ri % 2 == 1 else colors.HexColor('#F5F8FC'))
            self.c.setFillColor(bg)
            self.c.rect(x, row_y - row_h, total_w, row_h, fill=1, stroke=0)

            txt_color = COLOR_WHITE if is_header else COLOR_DARK
            cx = x
            for ci, (cell, cw) in enumerate(zip(row, col_widths)):
                align = 'right' if ci >= 1 and not is_header else 'left'
                pad = 3 * mm if align == 'left' else -3 * mm
                self._text(cx + (cw + pad if align == 'right' else pad),
                           row_y - row_h + 2 * mm, str(cell),
                           font_size=8, color=txt_color, align=align)
                cx += cw

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
                   single_mode: bool = False, site_url: str = ''):
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

        # 注釈
        self._text(27 * mm, PAGE_H * 0.12,
                   '※ 本レポートのデータは日本国内からのアクセスのみを対象としています。',
                   font_size=8, color=COLOR_GRAY)

        self._footer(company_name, 1)

    def draw_summary_compare(self, cur_meta, prev_meta, cur_kpis, prev_kpis,
                             compared_kpis, daily_chart_bytes, company_name=''):
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
        card_labels = ['月間アクティブユーザー', '新規ユーザー', 'セッション数', '平均エンゲージメント時間']
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

        # キーイベント合計バナー
        banner_y = card_y - card_h - 6 * mm
        banner_w = PAGE_W - 24 * mm
        self._rect(12 * mm, banner_y - 12 * mm, banner_w, 12 * mm,
                   fill_color=COLOR_PRIMARY, radius=3)
        kev_diff = compared_kpis['key_events_total']
        kev_delta = format_delta(kev_diff)
        self._text(PAGE_W / 2, banner_y - 6 * mm,
                   f"月間キーイベント合計:  {cur_kpis['key_events_total']:,} 件"
                   f"（前月{prev_kpis['key_events_total']:,}  {kev_delta}）",
                   font_size=12, color=COLOR_WHITE, align='center')

        # 日別ユーザー推移グラフ（バナーの下、フッターの上に画面いっぱい）
        if daily_chart_bytes:
            chart_top = banner_y - 14 * mm
            chart_w = PAGE_W - 16 * mm  # 左右8mmマージン
            chart_h = chart_top - 14 * mm  # フッター上端まで
            self._image_bytes(daily_chart_bytes, 8 * mm, chart_top, chart_w, h=chart_h)

        self._footer(company_name, 2)

    def draw_channels_compare(self, channel_chart_bytes, compared_channels,
                              cur_label, prev_label, company_name=''):
        """P3: チャネル比較"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('流入チャネル比較')

        content_top = PAGE_H - 30 * mm

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
                                cur_kpis=None, prev_kpis=None):
        """P4: キーイベント（コンバージョン）比較 + 移行率"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('キーイベント（コンバージョン）比較')

        content_top = PAGE_H - 30 * mm
        margin = 10 * mm

        has_kev_data = bool(compared_kevents) and any(
            (int(i.get('current', 0)) > 0 or int(i.get('previous', 0)) > 0)
            for i in compared_kevents
        )

        # --- 上段: コンバージョン比較チャート（左）+ 移行率チャート（右） ---
        chart_h = 58 * mm
        if kev_chart_bytes and transition_rate_chart_bytes:
            self._section_header(margin, content_top, 'コンバージョン比較 / 予約完了までの移行率', '当月 vs 前月')
            left_w = 165 * mm
            right_w = PAGE_W - (margin * 2) - left_w - 6 * mm
            self._image_bytes(kev_chart_bytes, margin, content_top - 5 * mm,
                              left_w, h=chart_h)
            self._image_bytes(transition_rate_chart_bytes,
                              margin + left_w + 6 * mm, content_top - 5 * mm,
                              right_w, h=chart_h)
        elif kev_chart_bytes:
            self._section_header(margin, content_top, 'コンバージョン比較', '当月 vs 前月')
            self._image_bytes(kev_chart_bytes, margin, content_top - 5 * mm,
                              PAGE_W - 20 * mm, h=chart_h)

        if not has_kev_data:
            self._missing_data_notice(
                margin + 2 * mm, content_top - 10 * mm, PAGE_W - 24 * mm, 10 * mm,
                '計測データ未出力（キーイベント明細がCSVに含まれていません）'
            )

        # --- 下段: キーイベントテーブル（CVR列付き） ---
        if compared_kevents:
            table_y = content_top - 72 * mm
            cur_sessions = int(cur_kpis.get('sessions', 0)) if cur_kpis else 0
            prev_sessions = int(prev_kpis.get('sessions', 0)) if prev_kpis else 0

            def cvr_str(cv, sessions):
                if sessions > 0:
                    return f'{cv / sessions * 100:.2f}%'
                return '-'

            headers = ['イベント名', cur_label, prev_label, '増減', '増減率', '当月CVR', '前月CVR']
            rows = []
            reservation_keywords = ['予約', '電話', 'tel']
            res_cur_total = 0
            res_prev_total = 0
            for item in compared_kevents:
                d = item['diff_dict']
                sign = '+' if d['diff'] > 0 else ''
                pct_str = f"{sign}{d['pct']:.1f}%" if d['pct'] is not None else 'N/A'
                rows.append([
                    str(item['name'])[:20],
                    f"{item['current']:,}",
                    f"{item['previous']:,}",
                    f"{sign}{int(d['diff']):,}",
                    pct_str,
                    cvr_str(item['current'], cur_sessions),
                    cvr_str(item['previous'], prev_sessions),
                ])
                if any(kw in str(item['name']).lower() for kw in reservation_keywords):
                    res_cur_total += item['current']
                    res_prev_total += item['previous']
            # 予約完了小計行
            if res_cur_total > 0 or res_prev_total > 0:
                res_diff = calc_diff(res_cur_total, res_prev_total)
                sign = '+' if res_diff['diff'] > 0 else ''
                pct_str = f"{sign}{res_diff['pct']:.1f}%" if res_diff['pct'] is not None else 'N/A'
                rows.append([
                    '予約完了 小計',
                    f"{res_cur_total:,}",
                    f"{res_prev_total:,}",
                    f"{sign}{int(res_diff['diff']):,}",
                    pct_str,
                    cvr_str(res_cur_total, cur_sessions),
                    cvr_str(res_prev_total, prev_sessions),
                ])
            # 合計行
            total_cur = sum(i['current'] for i in compared_kevents)
            total_prev = sum(i['previous'] for i in compared_kevents)
            total_diff = calc_diff(total_cur, total_prev)
            sign = '+' if total_diff['diff'] > 0 else ''
            pct_str = f"{sign}{total_diff['pct']:.1f}%" if total_diff['pct'] is not None else 'N/A'
            rows.append([
                '合計',
                f"{total_cur:,}",
                f"{total_prev:,}",
                f"{sign}{int(total_diff['diff']):,}",
                pct_str,
                cvr_str(total_cur, cur_sessions),
                cvr_str(total_prev, prev_sessions),
            ])

            col_ws = [42 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm, 20 * mm, 20 * mm]
            self._comparison_table(margin, table_y - 5 * mm, headers, rows, col_ws)
            if not has_kev_data:
                self._missing_data_notice(
                    margin, table_y - (len(rows) + 1) * 7 * mm - 4 * mm,
                    150 * mm, 9 * mm,
                    '計測データ未出力: GA4エクスポートにキーイベント明細を追加してください'
                )

        self._footer(company_name, 4)

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
        if cur_pages is not None:
            col_p = cur_pages.columns[0]
            col_v = cur_pages.columns[1]
            prev_map = {}
            if prev_pages is not None:
                prev_map = dict(zip(prev_pages[prev_pages.columns[0]],
                                    prev_pages[prev_pages.columns[1]]))

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
            for idx, (_, r) in enumerate(cur_pages.head(20).iterrows(), start=1):
                cur_v = int(r[col_v])
                prev_v = int(prev_map.get(r[col_p], 0))
                diff = cur_v - prev_v
                sign = '+' if diff > 0 else ''
                rows.append([
                    str(idx),
                    shorten(r[col_p]),
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

        self._footer(company_name, 5)

    def draw_page_engagement_page(self, cur_pages, prev_pages, cur_kpis, prev_kpis, company_name=''):
        """P6: 上位ページ別エンゲージ時間（推定）"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('上位ページ エンゲージ時間分析（推定）')

        margin = 12 * mm
        content_top = PAGE_H - 30 * mm

        def sum_top_pv(df, top_n=20):
            if df is None:
                return 0
            return int(df.iloc[:, 1].head(top_n).sum())

        def fmt_sec(sec):
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
        cur_eng_per_pv = (cur_total_eng / cur_top_pv) if cur_top_pv > 0 else 0
        prev_eng_per_pv = (prev_total_eng / prev_top_pv) if prev_top_pv > 0 else 0

        # 1PVあたり平均滞在時間（目安）をタイトル横に表示
        table_y = content_top
        avg_label = f'1PVあたり平均滞在時間（目安）: 当月 {fmt_sec(cur_eng_per_pv)} / 前月 {fmt_sec(prev_eng_per_pv)}'
        self._section_header(margin, table_y, '上位20ページ別 推定エンゲージ時間（当月 vs 前月）', avg_label)

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
        if cur_pages is not None:
            for idx, (_, r) in enumerate(cur_pages.head(20).iterrows(), start=1):
                name = r.iloc[0]
                cur_pv = int(r.iloc[1])
                prev_pv = int(prev_map.get(name, 0))
                cur_est_sec = cur_pv * cur_eng_per_pv
                prev_est_sec = prev_pv * prev_eng_per_pv
                diff_sec = cur_est_sec - prev_est_sec
                rows.append([
                    str(idx),
                    short_page(name),
                    fmt_sec(cur_est_sec),
                    fmt_sec(prev_est_sec),
                    f'{fmt_sec(abs(diff_sec))}{"↑" if diff_sec > 0 else "↓" if diff_sec < 0 else "→"}',
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
        self._section_header(col2_x, content_top, '新規 vs リピーター')
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
                          cur_kpis: dict | None = None):
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
                f"対象月: {cur_meta.get('month_label', '')}",
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

        # 最も改善した指標
        self._rect(margin, card_y - card_h, card_w, card_h,
                   fill_color=COLOR_CARD_BG, stroke_color=colors.HexColor('#DDE6EF'))
        self._rect(margin, card_y - 3 * mm, card_w, 3 * mm,
                   fill_color=COLOR_INCREASE, radius=0)
        self._text(margin + 5 * mm, card_y - 10 * mm,
                   '最も改善した指標', font_size=9, color=COLOR_GRAY)
        self._text(margin + 5 * mm, card_y - 17 * mm,
                   highlights['best_kpi']['label'], font_size=13, color=COLOR_DARK)
        self._text(margin + 5 * mm, card_y - 23 * mm,
                   highlights['best_kpi']['delta'], font_size=9,
                   color=COLOR_INCREASE)

        # 最も注意が必要な指標
        cx2 = margin + card_w + 12 * mm
        self._rect(cx2, card_y - card_h, card_w, card_h,
                   fill_color=COLOR_CARD_BG, stroke_color=colors.HexColor('#DDE6EF'))
        self._rect(cx2, card_y - 3 * mm, card_w, 3 * mm,
                   fill_color=COLOR_DECREASE, radius=0)
        self._text(cx2 + 5 * mm, card_y - 10 * mm,
                   '注意が必要な指標', font_size=9, color=COLOR_GRAY)
        self._text(cx2 + 5 * mm, card_y - 17 * mm,
                   highlights['worst_kpi']['label'], font_size=13, color=COLOR_DARK)
        self._text(cx2 + 5 * mm, card_y - 23 * mm,
                   highlights['worst_kpi']['delta'], font_size=9,
                   color=COLOR_DECREASE)

        # 優先施策ハイライト
        hl_y = card_y - card_h - 10 * mm
        self._section_header(margin, hl_y, '優先施策ハイライト')

        actions = highlights.get('action_items', [])
        if not actions:
            actions = ['上位ページの見出しと導線を見直し、反応率の改善を優先。']

        action_row_h = 12 * mm
        for i, action in enumerate(actions[:2]):
            iy = hl_y - 10 * mm - i * action_row_h
            self._rect(margin, iy - 9 * mm, PAGE_W - 24 * mm, 10 * mm,
                       fill_color=COLOR_CARD_BG, stroke_color=colors.HexColor('#DDE6EF'))
            self._text(margin + 5 * mm, iy - 5 * mm, f'施策{i+1}',
                       font_size=8, color=COLOR_GRAY)
            self._text(margin + 22 * mm, iy - 5 * mm, action,
                       font_size=7.5, color=COLOR_DARK)

        # KPI変動一覧（左）と総括コメント（右）
        num_actions = min(len(actions), 2)
        section_y = hl_y - 10 * mm - num_actions * action_row_h - 6 * mm
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
            avg_engage_sec = float(kpis.get('avg_engage_sec', 0))

            single_comments = [
                f"今月のアクセス数は {sessions:,} 件、アクティブユーザーは {active_users:,} 人でした。",
                f"新しく来訪したユーザーは {new_users:,} 人で、予約・電話などの成果は合計 {key_events_total:,} 件でした。",
                f"1セッションあたりの平均エンゲージ時間は {avg_engage_sec:.1f} 秒でした。",
                "次月は、成果につながったページの見出しと導線を優先的に整え、予約ボタン周辺の文言を統一して改善を進めることを推奨します。",
            ]
            for i, txt in enumerate(single_comments):
                y = ry - i * 6.4 * mm
                self._text(right_x + 2 * mm, y, f'• {txt}', font_size=7.8, color=COLOR_DARK)
            ay = ry - len(single_comments) * 6.4 * mm
        else:
            good_points = highlights.get('good_points', [])
            improve_points = highlights.get('improve_points', [])
            action_items = highlights.get('action_items', [])

            self._text(right_x + 2 * mm, ry, '良かった点', font_size=7.5, color=COLOR_PRIMARY)
            for i, txt in enumerate(good_points[:2]):
                y = ry - 5 * mm - i * 5 * mm
                self._text(right_x + 6 * mm, y, f'• {txt}', font_size=6.8, color=COLOR_DARK)

            iy = ry - 5 * mm - min(len(good_points), 2) * 5 * mm - 1.5 * mm
            self._text(right_x + 2 * mm, iy, '改善ポイント', font_size=7.5, color=colors.HexColor('#C27116'))
            for i, txt in enumerate(improve_points[:2]):
                y = iy - 5 * mm - i * 5 * mm
                self._text(right_x + 6 * mm, y, f'• {txt}', font_size=6.8, color=COLOR_DARK)

            ay = iy - 5 * mm - min(len(improve_points), 2) * 5 * mm - 1.5 * mm
            self._text(right_x + 2 * mm, ay, '次月アクション', font_size=7.5, color=colors.HexColor('#2E8B57'))
            for i, txt in enumerate(action_items[:2]):
                y = ay - 5 * mm - i * 5 * mm
                self._text(right_x + 6 * mm, y, f'• {txt}', font_size=6.8, color=COLOR_DARK)

        # 注釈（左右セクションの下）
        left_bottom = section_y - 10 * mm - min(len(bullets), 5) * 7 * mm
        if single_mode:
            right_bottom = ay - 2 * mm
        else:
            right_bottom = ay - 5 * mm - min(len(action_items), 2) * 5 * mm
        note_y = min(left_bottom, right_bottom) - 10 * mm
        self._text(margin, note_y,
                   '※ 日本国内からのアクセスのみを対象。海外からのアクセスは除外済み。',
                   font_size=7, color=COLOR_GRAY)

        self._footer(company_name, page_num)

    def draw_cohort_compare(self, cohort_chart_bytes, cur_trend, prev_trend,
                            cur_label, prev_label, company_name='',
                            page_num=8):
        """P8: コホート分析"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('コホート分析')

        content_top = PAGE_H - 30 * mm
        margin = 10 * mm

        # コホートチャート（30日/7日/1日）
        self._section_header(margin, content_top, 'アクティブユーザー推移', '30日 / 7日 / 1日')
        if cohort_chart_bytes:
            self._image_bytes(cohort_chart_bytes, margin, content_top - 5 * mm,
                              PAGE_W - 20 * mm, h=48 * mm)

        def get_last_val(df, col_idx):
            if df is not None and col_idx < len(df.columns):
                return int(df.iloc[:, col_idx].iloc[-1])
            return 0

        # リテンション率 — ファネル形式
        ret_y = content_top - 60 * mm
        self._section_header(margin, ret_y, 'リテンション率ファネル', '30日 → 7日 → 1日 の定着率')

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

            # 矢印 + リテンション率（最後のボックスの後は描かない）
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
        self._section_header(margin, explain_y, 'コホート指標の見方')

        cohort_terms = [
            ('30日アクティブ', '直近30日間に1回以上訪問したユーザー数（≒月間アクティブユーザー / MAU）'),
            ('7日アクティブ', '直近7日間に1回以上訪問したユーザー数（≒週間アクティブユーザー / WAU）'),
            ('1日アクティブ', 'その日に訪問したユーザー数（≒日次アクティブユーザー / DAU）'),
            ('リテンション率', '上位期間のユーザーが下位期間でも訪問している割合。高いほどユーザーの定着度が良好'),
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

        self._footer(company_name, page_num)

    def draw_reservation_compare(self, reservation_chart_bytes, transition_rate_chart_bytes,
                                 compared_kevents,
                                 cur_kpis, prev_kpis, cur_label, prev_label, company_name='',
                                 page_num=9):
        """P9: 予約完了分析"""
        self._bg(COLOR_LIGHT)
        self._page_title_bar('予約・電話 コンバージョン分析')

        content_top = PAGE_H - 30 * mm
        margin = 10 * mm

        # 予約比較チャート（左）＋移行率チャート（右）
        self._section_header(margin, content_top, '予約・電話CV比較 / 予約完了までの移行率', '当月 vs 前月')
        chart_y = content_top - 5 * mm
        left_w = 165 * mm
        right_w = PAGE_W - (margin * 2) - left_w - 6 * mm
        has_res_data = bool(compared_kevents) and any(
            any(kw in str(i.get('name', '')).lower() for kw in ['予約', '電話', 'tel'])
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

        reservation_keywords = ['予約', '電話', 'tel']
        res_events = [e for e in compared_kevents
                      if any(kw in str(e['name']).lower() for kw in reservation_keywords)]
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

        # 広告経由 推定CV（広告セッション比率 × 全体CV）
        cur_ad_ratio = (cur_paid_total / cur_total_ses) if cur_total_ses > 0 else 0
        prev_ad_ratio = (prev_paid_total / prev_total_ses) if prev_total_ses > 0 else 0
        cur_est_cv = round(cur_total_cv * cur_ad_ratio)
        prev_est_cv = round(prev_total_cv * prev_ad_ratio)
        cur_web_est_cv = round(cur_total_cv * (cur_paid_ses['web'] / cur_total_ses)) if cur_total_ses > 0 else 0
        prev_web_est_cv = round(prev_total_cv * (prev_paid_ses['web'] / prev_total_ses)) if prev_total_ses > 0 else 0
        cur_sns_est_cv = round(cur_total_cv * (cur_paid_ses['sns'] / cur_total_ses)) if cur_total_ses > 0 else 0
        prev_sns_est_cv = round(prev_total_cv * (prev_paid_ses['sns'] / prev_total_ses)) if prev_total_ses > 0 else 0

        # 広告セッションあたりCV率
        cur_ad_cvr = (cur_est_cv / cur_paid_total * 100) if cur_paid_total > 0 else 0
        prev_ad_cvr = (prev_est_cv / prev_paid_total * 100) if prev_paid_total > 0 else 0

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
        self._kpi_comparison_card(
            margin + (card_w + 6 * mm) * 2, y_cards, card_w, card_h,
            '広告経由 推定CV', f'{cur_est_cv:,}', f'{prev_est_cv:,}',
            calc_diff(cur_est_cv, prev_est_cv), unit='件'
        )

        chart_top = content_top - 40 * mm
        self._section_header(margin, chart_top, '広告チャネル別 セッション比較', '当月 vs 前月')
        if paid_channel_chart_bytes:
            self._image_bytes(paid_channel_chart_bytes, margin, chart_top - 4 * mm,
                              PAGE_W - 20 * mm, h=54 * mm)

        table_top = chart_top - 66 * mm
        self._section_header(margin, table_top, '広告 比率サマリ / 広告経由 推定CV')
        headers = ['指標', cur_label, prev_label, '差分']
        cur_web_ses_share = (cur_paid_ses['web'] / cur_total_ses * 100) if cur_total_ses > 0 else 0
        prev_web_ses_share = (prev_paid_ses['web'] / prev_total_ses * 100) if prev_total_ses > 0 else 0
        cur_sns_ses_share = (cur_paid_ses['sns'] / cur_total_ses * 100) if cur_total_ses > 0 else 0
        prev_sns_ses_share = (prev_paid_ses['sns'] / prev_total_ses * 100) if prev_total_ses > 0 else 0
        cur_web_new_share = (cur_paid_new['web'] / cur_total_new * 100) if cur_total_new > 0 else 0
        prev_web_new_share = (prev_paid_new['web'] / prev_total_new * 100) if prev_total_new > 0 else 0
        cur_sns_new_share = (cur_paid_new['sns'] / cur_total_new * 100) if cur_total_new > 0 else 0
        prev_sns_new_share = (prev_paid_new['sns'] / prev_total_new * 100) if prev_total_new > 0 else 0
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
            [
                'Web広告 新規ユーザー比率',
                f'{cur_web_new_share:.2f}% ({cur_paid_new["web"]:,}/{cur_total_new:,})',
                f'{prev_web_new_share:.2f}% ({prev_paid_new["web"]:,}/{prev_total_new:,})',
                f'{cur_web_new_share - prev_web_new_share:+.2f}pt',
            ],
            [
                'SNS広告 新規ユーザー比率',
                f'{cur_sns_new_share:.2f}% ({cur_paid_new["sns"]:,}/{cur_total_new:,})',
                f'{prev_sns_new_share:.2f}% ({prev_paid_new["sns"]:,}/{prev_total_new:,})',
                f'{cur_sns_new_share - prev_sns_new_share:+.2f}pt',
            ],
            [
                'Web広告 推定CV数 *',
                f'{cur_web_est_cv:,}件',
                f'{prev_web_est_cv:,}件',
                f'{cur_web_est_cv - prev_web_est_cv:+,}件',
            ],
            [
                'SNS広告 推定CV数 *',
                f'{cur_sns_est_cv:,}件',
                f'{prev_sns_est_cv:,}件',
                f'{cur_sns_est_cv - prev_sns_est_cv:+,}件',
            ],
            [
                '広告経由 推定CVR *',
                f'{cur_ad_cvr:.2f}% ({cur_est_cv:,}/{cur_paid_total:,})',
                f'{prev_ad_cvr:.2f}% ({prev_est_cv:,}/{prev_paid_total:,})',
                f'{cur_ad_cvr - prev_ad_cvr:+.2f}pt',
            ],
        ]
        col_ws = [52 * mm, 68 * mm, 68 * mm, 32 * mm]
        self._comparison_table(margin, table_top - 5 * mm, headers, rows, col_ws)

        # 注意書き
        note_y = table_top - 5 * mm - (len(rows) + 1) * 7 * mm - 3 * mm
        self._text(
            margin, note_y,
            '* 推定値: GA4のCSVにはチャネル別CV数が含まれないため、広告セッション比率×全体CV数で按分した推定値です。実際の広告経由CV数とは異なる場合があります。',
            font_size=6.5,
            color=COLOR_GRAY,
        )

        self._footer(company_name, page_num)

    def draw_glossary(self, company_name='', page_num=10):
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
            ('キーイベント（CV）',
             'ビジネス目標達成に重要な行動（WEB予約・電話タップ等）。GA4でキーイベント設定された発生回数。'),
            ('チャネル',
             '流入経路の分類。オーガニック検索/ダイレクト/有料検索/参照元/オーガニックSNS等。'),
            ('表示回数（PV）',
             'ページが表示された回数。同一ユーザーの複数回閲覧もその都度カウント。'),
            ('リピーター',
             '過去に訪問歴があり再訪問したユーザー。アクティブユーザーから新規ユーザーを除いた数。'),
            ('コホート',
             '同一期間に初回訪問したユーザー群を追跡し、その後の再訪問率・行動変化を分析する手法。'),
            ('リテンション率',
             '上位期間のユーザーが下位期間でも訪問している割合。高いほどユーザーの定着度が良好であることを示す。'),
            ('30日/7日/1日アクティブ',
             '直近30日(MAU)/7日(WAU)/1日(DAU)に訪問したユーザー数。コホート分析で定着度を測る基本指標。'),
        ]

        self._section_header(margin, content_top, '用語解説')

        row_h = 11.5 * mm
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

        notes = [
            '• 本レポートのデータはGA4（Google Analytics 4）からエクスポートされたCSVに基づいています。',
            '• 日本国内からのアクセスのみを対象としています。海外からのアクセス（ボット・スパム等含む）は除外されています。',
            '• ユーザー数はCookieベースの推定値であり、実際のユニークユーザー数とは異なる場合があります。',
        ]

        for i, note in enumerate(notes):
            ny = note_top - 9 * mm - i * 5.2 * mm
            self._text(margin + 5 * mm, ny, note, font_size=6.5, color=COLOR_DARK)

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

    compared_channels = compare_dataframes(
        cur_data['df_ch_ses'], prev_data['df_ch_ses'],
        cur_data['df_ch_ses'].columns[0] if cur_data['df_ch_ses'] is not None else 'channel',
        cur_data['df_ch_ses'].columns[1] if cur_data['df_ch_ses'] is not None else 'value',
    ) if cur_data['df_ch_ses'] is not None or prev_data['df_ch_ses'] is not None else pd.DataFrame()

    compared_kevents = compare_key_events(
        cur_kpis.get('key_events', {}),
        prev_kpis.get('key_events', {}),
    )

    # 3. チャート生成
    daily_chart = make_daily_overlay_chart(
        cur_data['df_active'], prev_data['df_active'], cur_label, prev_label)

    channel_chart = make_channel_grouped_bar(
        cur_data['df_ch_ses'], prev_data['df_ch_ses'], cur_label, prev_label)

    kev_chart = make_key_events_grouped_bar(
        cur_kpis.get('key_events', {}), prev_kpis.get('key_events', {}),
        cur_label, prev_label)

    pages_chart = make_top_pages_compare_chart(
        cur_data['df_pages'], prev_data['df_pages'])

    engage_chart = make_engagement_overlay_chart(
        cur_data['df_engage'], prev_data['df_engage'], cur_label, prev_label)

    user_chart = make_new_vs_returning_compare(
        cur_data['df_new'], cur_data['df_active'],
        prev_data['df_new'], prev_data['df_active'],
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
    )
    paid_channel_chart = make_paid_channels_grouped_bar(
        cur_data.get('df_ch_ses'),
        prev_data.get('df_ch_ses'),
        cur_label,
        prev_label,
    )

    # 4. ハイライト計算
    highlights = compute_highlights(compared_kpis, compared_channels, compared_kevents)

    # 5. PDF生成
    rc = CompareReportCanvas(output_path)

    # P1: 表紙
    rc.draw_cover(cur_meta, prev_meta, company_name, department, logo_path, single_mode=single_mode, site_url=site_url)
    rc.new_page()

    # P2: KPI比較サマリ
    rc.draw_summary_compare(cur_meta, prev_meta, cur_kpis, prev_kpis,
                            compared_kpis, daily_chart, company_name)
    rc.new_page()

    # P3: チャネル比較
    rc.draw_channels_compare(channel_chart, compared_channels,
                             cur_label, prev_label, company_name)
    rc.new_page()

    # P4: キーイベント比較 + 移行率
    rc.draw_key_events_compare(kev_chart, compared_kevents,
                               cur_label, prev_label, company_name,
                               transition_rate_chart_bytes=transition_rate_chart,
                               cur_kpis=cur_kpis, prev_kpis=prev_kpis)
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

        # P8: コホート分析
        rc.draw_cohort_compare(cohort_chart, cur_data['df_trend'], prev_data['df_trend'],
                               cur_label, prev_label, company_name, page_num=8)
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
        )
        rc.new_page()

        # P11: 用語解説・注記
        rc.draw_glossary(company_name=company_name, page_num=11)
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
        )
        rc.new_page()

        # P9: 用語解説・注記（標準版）
        rc.draw_glossary(company_name=company_name, page_num=9)

    rc.save()
    return output_path
