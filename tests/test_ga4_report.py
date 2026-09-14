#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GA4月次比較レポートの回帰テスト。

過去に本番へ出てしまった以下の欠陥を再発させないことを目的とする。

- 月間アクティブユーザーの誤称（30日ローリング末値を暦月として提示）
- 減少している指標を「最も改善した指標」として緑色表示
- 上位20ページ表が当月PV降順のため、当月0PVのページが不可視になる
- 総括がデータにない原因（検索順位・LINE/CRM・フォーム項目数）を断定
- 広告経由CVRの循環推計（推定CVR ≒ 全体CVR）
- 新規vsリピーターが日別合計の重複計上
- 長い注記が右端で切れる

フィクスチャは実CSVを匿名化したもの（tests/_anonymize_fixtures.py）。
数値は実データのまま残しているため、パーサの回帰を検知できる。
"""
import os
import sys
from pathlib import Path

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')

from csv_parser import parse_ga4_csv  # noqa: E402
from input_validation import validate_csv_inputs, validate_report_data  # noqa: E402
from compare_utils import (  # noqa: E402
    compare_kpis, compare_dataframes, compare_key_events,
    compute_highlights, select_top_pages, format_delta,
)

CUR = os.path.join(FIX, 'sample_cur.csv')            # 31日ある月
PREV = os.path.join(FIX, 'sample_prev.csv')          # 30日ある月
PAID_CUR = os.path.join(FIX, 'sample_paid_cur.csv')  # 有料検索が主体
PAID_PREV = os.path.join(FIX, 'sample_paid_prev.csv')


@pytest.fixture(scope='module')
def cur():
    return parse_ga4_csv(CUR)


@pytest.fixture(scope='module')
def prev():
    return parse_ga4_csv(PREV)


# ─────────────────────────────────────────────
# CSV解析
# ─────────────────────────────────────────────
class TestParser:
    def test_期間を読み取れる(self, cur, prev):
        assert cur['meta']['start'] == '20260701'
        assert cur['meta']['end'] == '20260731'
        assert prev['meta']['start'] == '20260601'
        assert prev['meta']['end'] == '20260630'

    def test_新規ユーザーは日別合計と一致する(self, cur, prev):
        # 新規ユーザーは1人1回しか計上されないため日別合計で正しい
        assert cur['kpis']['new_users'] == 876
        assert prev['kpis']['new_users'] == 971

    def test_セッションはチャネル別合計と一致する(self, cur, prev):
        assert cur['kpis']['sessions'] == 1524
        assert prev['kpis']['sessions'] == 1594

    def test_アクティブユーザーは30日ローリングの期間末値(self, cur, prev):
        # この値は暦月のユニークユーザー数ではない。
        # 31日ある月では初日が集計窓から外れるため、暦月とは一致しない。
        assert cur['kpis']['active_users'] == 950
        assert prev['kpis']['active_users'] == 994

    def test_アクティブユーザーに日別合計を使っていない(self, cur):
        # 日別合計は同一ユーザーを重複計上して過大になる（回帰防止）
        daily_sum = int(cur['df_active'].iloc[:, 1].sum())
        assert daily_sum > cur['kpis']['active_users']
        assert cur['kpis']['active_users'] != daily_sum

    def test_エンゲージメント時間はユーザー数で加重平均している(self, cur):
        simple = float(cur['df_engage'].iloc[:, 1].mean())
        weighted = float(cur['kpis']['avg_engage_sec'])
        assert abs(weighted - simple) > 0.1, '単純平均に戻っている'

    def test_国別内訳を読み取れる(self, cur):
        c = cur['meta']['country']
        assert c['available'] is True
        assert c['jp'] + (c['total'] - c['jp']) == c['total']
        assert 0 < c['jp_share'] <= 1

    def test_CSV出力元を識別できる(self, cur, prev):
        assert cur['meta']['source_report'] == 'レポートのスナップショット'
        assert prev['meta']['source_report'] == 'レポートのスナップショット'

    def test_キーイベントの0件と未出力を区別する(self, cur):
        assert cur['kpis']['key_events_status'] == 'configured_zero'
        assert cur['kpis']['key_events_total'] == 0

        missing = parse_ga4_csv(b'# no key event section\n')
        assert missing['kpis']['key_events_status'] == 'missing'
        assert missing['kpis']['key_events_total'] == 0

    def test_キーイベント明細があれば出力済みと判定する(self):
        data = parse_ga4_csv(
            'イベント名,キーイベント\nweb予約,3\n\n'.encode('utf-8')
        )
        assert data['kpis']['key_events_status'] == 'available'
        assert data['kpis']['key_events_total'] == 3


# ─────────────────────────────────────────────
# 入力バリデーション
# ─────────────────────────────────────────────
class TestInputValidation:
    def test_正しいスナップショットCSVは受け付ける(self, cur, prev):
        assert validate_report_data(cur, '当月CSV') == []
        assert validate_report_data(prev, '比較月CSV') == []
        assert validate_csv_inputs(CUR, PREV) == []

    def test_集客サマリーは生成を拒否する(self):
        bad_csv = b"""# ----------------------------------------
# Acquisition summary
# \xe9\x9b\x86\xe5\xae\xa2\xe3\x82\xb5\xe3\x83\x9e\xe3\x83\xaa\xe3\x83\xbc
# Start: 20260801
# End: 20260831
N day,active users
0000,10
"""
        errors = validate_csv_inputs(bad_csv)
        assert any('集客サマリー' in err for err in errors)
        assert any('必要な項目がありません' in err for err in errors)

    def test_ページ表欠落を0件として扱わない(self, cur):
        incomplete = dict(cur)
        incomplete['df_pages'] = None
        errors = validate_report_data(incomplete, '当月CSV')
        assert any('上位ページ' in err for err in errors)
        assert any('PDFは生成できません' in err for err in errors)


# ─────────────────────────────────────────────
# 上位ページの選定
# ─────────────────────────────────────────────
class TestSelectTopPages:
    def test_当月0PVになったページが表から消えない(self):
        """本番で 218PV→0PV のページが上位20表から消えていた不具合の回帰テスト。"""
        p_cur = parse_ga4_csv(PAID_CUR)
        p_prev = parse_ga4_csv(PAID_PREV)
        sel = select_top_pages(p_cur['df_pages'], p_prev['df_pages'], 20)
        lost = sel[sel['status'] == 'lost']
        assert not lost.empty, '消失ページが検出されていない'
        assert lost['prev'].max() == 218
        assert (lost['cur'] == 0).all()

    def test_行数は上限を超えない(self, cur, prev):
        sel = select_top_pages(cur['df_pages'], prev['df_pages'], 20)
        assert len(sel) <= 20

    def test_選定基準は当月と前月の大きい方(self):
        import pandas as pd
        c = pd.DataFrame({'p': ['A', 'B'], 'v': [10, 0]})
        p = pd.DataFrame({'p': ['A', 'C'], 'v': [5, 999]})
        sel = select_top_pages(c, p, 5)
        assert sel.iloc[0]['page'] == 'C', '前月のみ大きいページが先頭に来ていない'

    def test_新規ページにnewフラグが付く(self):
        import pandas as pd
        c = pd.DataFrame({'p': ['NEW'], 'v': [50]})
        p = pd.DataFrame({'p': ['OLD'], 'v': [50]})
        sel = select_top_pages(c, p, 5)
        assert set(sel['status']) == {'new', 'lost'}

    def test_両月0のページは除外される(self):
        import pandas as pd
        c = pd.DataFrame({'p': ['A', 'Z'], 'v': [10, 0]})
        p = pd.DataFrame({'p': ['A', 'Z'], 'v': [10, 0]})
        sel = select_top_pages(c, p, 5)
        assert 'Z' not in set(sel['page'])


# ─────────────────────────────────────────────
# 総括ハイライト
# ─────────────────────────────────────────────
def _highlights(cur_path, prev_path):
    c = parse_ga4_csv(cur_path)
    p = parse_ga4_csv(prev_path)
    ck = compare_kpis(c['kpis'], p['kpis'])
    ch = compare_dataframes(
        c['df_ch_ses'], p['df_ch_ses'],
        c['df_ch_ses'].columns[0], c['df_ch_ses'].columns[1],
    )
    kev = compare_key_events(c['kpis'].get('key_events', {}), p['kpis'].get('key_events', {}))
    return compute_highlights(ck, ch, kev,
                              cur_pages=c['df_pages'], prev_pages=p['df_pages'])


@pytest.fixture(scope='module')
def hl():
    return _highlights(CUR, PREV)


@pytest.fixture(scope='module')
def hl_paid():
    return _highlights(PAID_CUR, PAID_PREV)


class TestHighlights:
    def test_全指標が下落した月にbestを増加扱いしない(self, hl, hl_paid):
        """減少値を『最も改善した指標』として緑色表示していた不具合の回帰テスト。"""
        for h in (hl, hl_paid):
            assert h['best_kpi']['is_gain'] is False
            assert h['best_kpi']['is_flat'] is False

    def test_増加がない月はその旨を明示する(self, hl):
        assert any('増加した主要指標はありません' in t for t in hl['good_points'])

    def test_大きな悪化は見られずという定型文を出さない(self, hl, hl_paid):
        for h in (hl, hl_paid):
            joined = ''.join(h['good_points'] + h['improve_points'])
            assert '大きな悪化は見られず' not in joined
            assert '基盤は維持できています' not in joined

    @pytest.mark.parametrize('banned', [
        '検索順位に変動があった',
        'メルマガ',
        'LINE',
        'CRM施策',
        '入力項目数',
        '離脱率が高い',
        '予算を維持または増額',
        '広告経由の集客を強化',
    ])
    def test_データにない原因や施策を断定しない(self, hl, hl_paid, banned):
        """GA4のこのCSVには検索順位・広告費・フォーム離脱が含まれないため、
        それらを原因や打ち手として断定してはならない。"""
        for h in (hl, hl_paid):
            text = ''.join(
                h['action_items'] + h['good_points'] + h['improve_points']
                + h['executive_comments'] + h['headline_lines']
            )
            assert banned not in text, f'断定表現が復活している: {banned}'

    def test_確認事項に根拠が併記される(self, hl, hl_paid):
        for h in (hl, hl_paid):
            assert h['action_items'], '確認事項が空'
            assert any('根拠' in a for a in h['action_items'])

    def test_CV未計測なら計測整備が最優先(self, hl, hl_paid):
        for h in (hl, hl_paid):
            assert 'キーイベント' in h['action_items'][0]

    def test_消失ページが確認事項に現れる(self, hl_paid):
        assert hl_paid['lost_pages'], '消失ページが highlights に入っていない'
        assert any('0PV' in a or '0件' in a for a in hl_paid['action_items'])

    def test_キーイベント未出力は前月比を計算しない(self):
        compared = compare_kpis(
            {'key_events_total': 0, 'key_events_status': 'missing'},
            {'key_events_total': 8, 'key_events_status': 'available'},
        )
        key_events = compared['key_events_total']
        assert key_events['comparable'] is False
        assert key_events['pct'] is None
        assert format_delta(key_events) == '比較不能（未出力）'


# ─────────────────────────────────────────────
# PDF生成（描画の回帰）
# ─────────────────────────────────────────────
class TestPdf:
    @pytest.fixture(scope='class')
    def pdf_text(self, tmp_path_factory):
        pytest.importorskip('reportlab')
        pdfplumber = pytest.importorskip('pdfplumber')
        from report_logic_compare import generate_compare_report
        out = tmp_path_factory.mktemp('pdf') / 'report.pdf'
        generate_compare_report(CUR, PREV, str(out),
                                company_name='テスト医院', detailed=True)
        assert out.exists() and out.stat().st_size > 10000
        with pdfplumber.open(str(out)) as doc:
            pages = [p.extract_text() or '' for p in doc.pages]
        return pages

    def test_詳細版は11ページ(self, pdf_text):
        assert len(pdf_text) == 11

    def test_月間アクティブユーザーという誤称を使わない(self, pdf_text):
        all_text = '\n'.join(pdf_text)
        assert '30日アクティブユーザー' in all_text
        # KPIカードのラベルとして「月間アクティブユーザー」を出さない
        assert '月間アクティブユーザー\n' not in all_text.replace(' ', '')

    def test_集計窓の注記が出る(self, pdf_text):
        """暦月と30日窓がずれる月（31日ある月）では必ず注記する。"""
        assert any('期間末時点の直近30日' in t for t in pdf_text)

    def test_最も改善した指標という見出しを出さない(self, pdf_text):
        assert '最も改善した指標' not in '\n'.join(pdf_text)

    def test_リテンション率という誤った呼称を使わない(self, pdf_text):
        all_text = '\n'.join(pdf_text)
        assert 'リテンション率' not in all_text
        assert 'スティッキネス' in all_text

    def test_スティッキネスの読み方を説明している(self, pdf_text):
        assert any('この数値はどうなれば良いのか' in t for t in pdf_text)

    def test_広告の推定CVRを表示しない(self, pdf_text):
        all_text = '\n'.join(pdf_text)
        assert '推定CVR' not in all_text
        assert '推定CV数' not in all_text

    def test_キーイベントを予約完了やCVRと誤称しない(self, pdf_text):
        all_text = '\n'.join(pdf_text)
        assert 'キーイベント発生回数比較' in all_text
        assert '予約完了' not in all_text
        assert '当月CVR' not in all_text
        assert '前月CVR' not in all_text

    def test_注記が右端で切れていない(self, pdf_text):
        """1行描画で文が途切れていた不具合の回帰テスト。"""
        all_text = '\n'.join(pdf_text)
        for broken in ('再エクスポートしてくださ\n', 'をキーイ\n', '集計方法が異な\n'):
            assert broken not in all_text, f'文字切れ: {broken!r}'

    def test_国別比率の母数が明示される(self, pdf_text):
        assert any('/975人' in t or '/ 975人' in t for t in pdf_text)

    def test_生成バージョンがPDFに刻まれる(self, pdf_text):
        """出力物だけでデプロイ反映の有無を判定できるようにする。"""
        from report_logic_compare import REPORT_VERSION
        assert any(REPORT_VERSION in t for t in pdf_text)


class TestPdfPaid:
    @pytest.fixture(scope='class')
    def paid_pdf(self, tmp_path_factory):
        pytest.importorskip('reportlab')
        pytest.importorskip('pdfplumber')
        from report_logic_compare import generate_compare_report
        out = tmp_path_factory.mktemp('paid') / 'paid.pdf'
        generate_compare_report(PAID_CUR, PAID_PREV, str(out),
                                company_name='テスト医院B', detailed=True)
        assert out.exists() and out.stat().st_size > 10000
        return str(out)

    def test_広告主体の医院でも生成できる(self, paid_pdf):
        import pdfplumber
        with pdfplumber.open(paid_pdf) as doc:
            assert len(doc.pages) == 11

    def test_広告CVは取得不能と明示する(self, paid_pdf):
        import pdfplumber
        with pdfplumber.open(paid_pdf) as doc:
            text = '\n'.join(p.extract_text() or '' for p in doc.pages)
        assert '取得不能' in text
        assert '推定CVR' not in text

    def test_本文がフッターと重ならない(self, paid_pdf):
        """注記や表がフッター（10mm）に食い込んでいないことを機械的に検証する。

        p9で注記が表と重なり、フッターにも食い込む不具合が発生したため追加。
        """
        import pdfplumber
        with pdfplumber.open(paid_pdf) as doc:
            for i, page in enumerate(doc.pages, start=1):
                words = page.extract_words()
                if not words:
                    continue
                # フッター行（会社名・ページ番号）は 7mm 付近。それ以外の本文を見る
                body = [w for w in words
                        if (page.height - w['bottom']) / 72 * 25.4 > 9.5]
                if not body:
                    continue
                lowest = min((page.height - w['bottom']) / 72 * 25.4 for w in body)
                assert lowest >= 10.0, (
                    f'p{i}: 本文がフッター線に食い込んでいる（下端 {lowest:.1f}mm）'
                )

    def test_生成バージョン注記がフッターより上にある(self, paid_pdf):
        import pdfplumber
        from report_logic_compare import REPORT_VERSION

        with pdfplumber.open(paid_pdf) as doc:
            page = doc.pages[-1]
            words = page.extract_words()
        version_words = [w for w in words if REPORT_VERSION in w['text']]
        assert version_words, '生成バージョン注記が見つからない'
        bottom_mm = min((page.height - w['bottom']) / 72 * 25.4 for w in version_words)
        assert bottom_mm >= 10.0, (
            f'生成バージョン注記がフッターに近すぎる（下端 {bottom_mm:.1f}mm）'
        )


class TestRobustness:
    def test_同じCSVを当月前月に与えても落ちない(self, tmp_path):
        pytest.importorskip('reportlab')
        from report_logic_compare import generate_compare_report
        out = tmp_path / 'same.pdf'
        generate_compare_report(CUR, CUR, str(out), company_name='同一', detailed=True)
        assert out.exists()

    def test_標準版も生成できる(self, tmp_path):
        pytest.importorskip('reportlab')
        from report_logic_compare import generate_compare_report
        out = tmp_path / 'std.pdf'
        generate_compare_report(CUR, PREV, str(out), company_name='標準', detailed=False)
        assert out.exists()

    def test_キーイベント明細があるPDFは参考比率と明記する(self, tmp_path):
        pytest.importorskip('reportlab')
        pdfplumber = pytest.importorskip('pdfplumber')
        from report_logic_compare import generate_compare_report

        cur_text = Path(CUR).read_text(encoding='utf-8').replace(
            'イベント名,キーイベント\n\n',
            'イベント名,キーイベント\nweb予約,5\n\n',
            1,
        )
        prev_text = Path(PREV).read_text(encoding='utf-8').replace(
            'イベント名,キーイベント\n\n',
            'イベント名,キーイベント\nweb予約,4\n\n',
            1,
        )
        cur_path = tmp_path / 'cur-with-key-events.csv'
        prev_path = tmp_path / 'prev-with-key-events.csv'
        cur_path.write_text(cur_text, encoding='utf-8')
        prev_path.write_text(prev_text, encoding='utf-8')
        out = tmp_path / 'with-key-events.pdf'

        generate_compare_report(
            str(cur_path), str(prev_path), str(out),
            company_name='参考比率テスト', detailed=False,
        )
        with pdfplumber.open(str(out)) as doc:
            text = '\n'.join(p.extract_text() or '' for p in doc.pages)
        assert '当月参考比率' in text
        assert '前月参考比率' in text
        assert 'GA4のセッションCVRではなく' in text
        assert '予約・電話アクション 小計' in text

    def test_キーイベント未出力のPDFは0件減少にしない(self, tmp_path):
        pytest.importorskip('reportlab')
        pdfplumber = pytest.importorskip('pdfplumber')
        from report_logic_compare import generate_compare_report

        cur_text = Path(CUR).read_text(encoding='utf-8')
        cur_text = cur_text.replace('イベント名,キーイベント', 'イベント名,重要イベント')
        cur_text = cur_text.replace('プラットフォーム,キーイベント', 'プラットフォーム,重要イベント')
        prev_text = Path(PREV).read_text(encoding='utf-8').replace(
            'イベント名,キーイベント\n\n',
            'イベント名,キーイベント\nweb予約,8\n\n',
            1,
        )
        cur_path = tmp_path / 'cur-without-key-events.csv'
        prev_path = tmp_path / 'prev-with-key-events.csv'
        cur_path.write_text(cur_text, encoding='utf-8')
        prev_path.write_text(prev_text, encoding='utf-8')
        out = tmp_path / 'missing-key-events.pdf'

        generate_compare_report(
            str(cur_path), str(prev_path), str(out),
            company_name='未出力テスト', detailed=False,
        )
        with pdfplumber.open(str(out)) as doc:
            text = '\n'.join(p.extract_text() or '' for p in doc.pages)
        assert '当月 未出力 / 前月 8件（比較不能）' in text
        assert 'キーイベント比較不能' in text
        assert '未出力を0件として扱っていません' in text
        assert 'キーイベント発生回数: 0件（前月8件 -8' not in text
