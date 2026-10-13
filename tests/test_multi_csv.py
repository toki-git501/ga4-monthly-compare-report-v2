#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""複数CSV（同じ月・同じプロパティ）を1セットとして受け取る入力の回帰テスト。

2026-10、GA4「レポートのスナップショット」の構成変更で日別アクティブユーザー・
日別新規ユーザー・平均エンゲージメント時間・30/7/1日アクティブユーザーが
スナップショットに入らなくなった。不足分を「集客サマリー」「エンゲージメントの概要」
から補えるよう、複数ファイルを結合して従来どおり処理する。

フィクスチャ（tests/fixtures/multi/）は実CSVを匿名化したもの（tests/_make_multi_fixtures.py）。
スナップショットは期間だけ他3本に揃えた複製（数値は別期間のまま）。
"""
import itertools
import os
import sys
from pathlib import Path

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
MULTI = os.path.join(FIX, 'multi')

from csv_merge import CsvMergeError, merge_ga4_csvs  # noqa: E402
from csv_parser import KEY_EVENTS_INFERRED, parse_ga4_csv  # noqa: E402
from input_validation import validate_csv_inputs  # noqa: E402
from compare_utils import compare_kpis, format_delta  # noqa: E402

SNAP = os.path.join(MULTI, 'snapshot.csv')
ACQ = os.path.join(MULTI, 'acquisition.csv')
ENG = os.path.join(MULTI, 'engagement.csv')
EVT = os.path.join(MULTI, 'events.csv')
SET3 = [SNAP, ACQ, ENG]
SET4 = [SNAP, ACQ, ENG, EVT]
OLD_CUR = os.path.join(FIX, 'sample_cur.csv')

SCREEN_ACQ = 'レポート > 集客 > 集客サマリー'
SCREEN_ENG = 'レポート > エンゲージメント > 概要'

REQUIRED = ('df_active', 'df_new', 'df_engage', 'df_trend', 'df_pages', 'df_ch_new', 'df_ch_ses')


def _read(path):
    return Path(path).read_bytes()


def _shift_period(blob: bytes, start='20260818', end='20260914') -> bytes:
    """前月セット用に期間だけを変えた複製を作る。"""
    text = blob.decode('utf-8')
    return (text.replace('20260915', start).replace('20261012', end)).encode('utf-8')


def _prev_set(paths):
    return [_shift_period(_read(p)) for p in paths]


# ─────────────────────────────────────────────
# 結合
# ─────────────────────────────────────────────
class TestMerge:
    def test_1ファイルはそのまま返す(self):
        blob = _read(OLD_CUR)
        assert merge_ga4_csvs([blob]) == blob

    def test_3ファイル結合で必須項目が揃う(self):
        assert validate_csv_inputs(SET3) == []
        data = parse_ga4_csv(merge_ga4_csvs(SET3))
        for key in REQUIRED:
            assert data[key] is not None and not data[key].empty, key
        assert data['meta']['source_report'] == 'レポートのスナップショット'
        assert data['meta']['start'] == '20260915'
        assert data['meta']['end'] == '20261012'

    def test_結合は入力順に依存しない(self):
        baseline = parse_ga4_csv(merge_ga4_csvs(SET4))
        for perm in itertools.permutations(SET4):
            merged = merge_ga4_csvs(list(perm))
            # メタ情報の基準になるスナップショットが常に先頭
            assert merged.decode('utf-8').splitlines()[1] == '# レポートのスナップショット'
            data = parse_ga4_csv(merged)
            assert data['kpis'] == baseline['kpis'], perm
            assert data['meta'] == baseline['meta'], perm
            for key in REQUIRED:
                assert data[key].equals(baseline[key]), (perm, key)

    def test_期間が異なるファイルはファイル名つきでエラー(self):
        odd = _shift_period(_read(ENG), '20260901', '20260930')
        with pytest.raises(CsvMergeError) as exc:
            merge_ga4_csvs([_read(SNAP), _read(ACQ), odd],
                           names=['snapshot.csv', 'acquisition.csv', 'engagement.csv'])
        msg = str(exc.value)
        assert '期間が異なる' in msg
        assert '異なるファイル: engagement.csv（2026/09/01〜2026/09/30）' in msg
        assert 'snapshot.csv（2026/09/15〜2026/10/12）' in msg

    def test_スナップショットだけ期間が違っても検知する(self):
        # 実際に取得されたスナップショットは他3本と期間が違っていた
        odd = _read(SNAP).decode('utf-8').replace('20260915', '20260901') \
            .replace('20261012', '20260930').encode('utf-8')
        with pytest.raises(CsvMergeError) as exc:
            merge_ga4_csvs([odd, _read(ACQ), _read(ENG)],
                           names=['snap.csv', 'acq.csv', 'eng.csv'])
        assert '異なるファイル: snap.csv' in str(exc.value)

    def test_プロパティが異なるファイルはエラー(self):
        other = _read(ACQ).decode('utf-8').replace(
            'サンプル歯科C - GA4', '別の医院 - GA4').encode('utf-8')
        with pytest.raises(CsvMergeError) as exc:
            merge_ga4_csvs([_read(SNAP), other, _read(ENG)],
                           names=['snapshot.csv', 'acquisition.csv', 'engagement.csv'])
        msg = str(exc.value)
        assert 'プロパティが異なる' in msg
        assert 'acquisition.csv（別の医院 - GA4）' in msg

    def test_結合エラーは入力検証のエラーとして返る(self):
        odd = _shift_period(_read(ENG), '20260901', '20260930')
        errors = validate_csv_inputs([_read(SNAP), _read(ACQ), odd])
        assert len(errors) == 1
        assert errors[0].startswith('当月CSV: 期間が異なる')

    def test_BOM付きとShift_JISのファイルも結合できる(self):
        snap_bom = _read(SNAP).decode('utf-8').encode('utf-8-sig')
        acq_sjis = _read(ACQ).decode('utf-8').encode('cp932')
        merged = merge_ga4_csvs([acq_sjis, _read(ENG), snap_bom])
        data = parse_ga4_csv(merged)
        baseline = parse_ga4_csv(merge_ga4_csvs(SET3))
        assert data['kpis'] == baseline['kpis']


# ─────────────────────────────────────────────
# 入力検証（必須項目と出力元の案内）
# ─────────────────────────────────────────────
class TestValidation:
    def test_集客サマリー単体はエラー(self):
        errors = validate_csv_inputs(ACQ)
        assert any('集客サマリー' in e and '揃っていません' in e for e in errors)
        joined = '\n'.join(errors)
        assert f'平均エンゲージメント時間（出力元: 「{SCREEN_ENG}」）' in joined
        assert '30日・7日・1日アクティブユーザー' in joined
        assert 'PDFは生成できません' in joined

    def test_集客サマリーと組み合わせて揃えば集客サマリーエラーを出さない(self):
        errors = validate_csv_inputs([ACQ, ENG])
        assert errors == []

    def test_新スナップショット単体は不足項目つきエラー(self):
        errors = validate_csv_inputs(SNAP)
        joined = '\n'.join(errors)
        assert f'日別アクティブユーザー（出力元: 「{SCREEN_ACQ}」）' in joined
        assert f'日別新規ユーザー（出力元: 「{SCREEN_ACQ}」）' in joined
        assert f'平均エンゲージメント時間（出力元: 「{SCREEN_ENG}」）' in joined
        assert f'30日・7日・1日アクティブユーザー（出力元: 「{SCREEN_ENG}」）' in joined
        # 上位ページ・チャネルはスナップショットに含まれるので不足扱いしない
        assert '上位ページ' not in joined
        assert 'チャネル別' not in joined
        # スナップショットを集客サマリーと誤認しない
        assert '「集客サマリー」から出力されたCSV' not in joined

    def test_2ファイルセットで足りない項目を明示して止める(self):
        errors = validate_csv_inputs([SNAP, ACQ])
        joined = '\n'.join(errors)
        assert '必要な項目がありません' in joined
        assert f'平均エンゲージメント時間（出力元: 「{SCREEN_ENG}」）' in joined
        assert '日別アクティブユーザー' not in joined

        errors = validate_csv_inputs([SNAP, ENG])
        joined = '\n'.join(errors)
        assert f'日別アクティブユーザー（出力元: 「{SCREEN_ACQ}」）' in joined
        assert '平均エンゲージメント時間' not in joined

    def test_当月前月とも結合セットで検証できる(self):
        assert validate_csv_inputs(SET3, _prev_set(SET3)) == []

    def test_上位ページの出力元はスナップショットかエンゲージメント概要(self):
        data = parse_ga4_csv(merge_ga4_csvs([ACQ, ENG]))
        data['df_pages'] = None
        from input_validation import validate_report_data
        joined = '\n'.join(validate_report_data(data, '当月CSV'))
        assert f'上位ページ（出力元: 「レポートのスナップショット」または「{SCREEN_ENG}」）' in joined


# ─────────────────────────────────────────────
# キーイベント（イベント別内訳が無い新形式）
# ─────────────────────────────────────────────
class TestKeyEventInference:
    @pytest.fixture(scope='class')
    def merged(self):
        return parse_ga4_csv(merge_ga4_csvs(SET4))

    def test_イベント名から判定した状態になる(self, merged):
        k = merged['kpis']
        assert k['key_events_status'] == KEY_EVENTS_INFERRED
        # スナップショットのプラットフォーム合計(805)ではなく、イベント名からの判定。
        # 予約・電話等の合計には求人関連（61+6+3=70）を含めない（v8〜）
        assert k['key_events_total'] == 368

    def test_自動計測は除外し求人系は予約電話と混ぜない(self, merged):
        k = merged['kpis']
        names = set(k['key_events'])
        recruit = set(k['key_events_recruit'])
        for excluded in ('tap', 'click', 'page_view', 'scroll', 'download', 'file_download'):
            assert excluded not in names
            assert excluded not in recruit
        for r in ('求人_送信ボタン_', '求人_応募フォームボタン_', 'まいなびリンク_top'):
            assert r not in names
        assert k['key_events']['WEB予約'] == 136
        assert k['key_events']['電話（小児）'] == 69

    def test_求人系は別カテゴリで計測する(self, merged):
        k = merged['kpis']
        assert k['key_events_recruit'] == {
            '求人_送信ボタン_': 61, 'まいなびリンク_top': 6, '求人_応募フォームボタン_': 3,
        }
        assert k['key_events_recruit_total'] == 70
        # 自動計測を除いた全イベント = 予約・電話等 + 求人関連（取りこぼし・二重計上なし）
        from csv_parser import CV_EXCLUDE_EXACT
        expected = sum(v for n, v in k['key_events_raw'].items()
                       if n.lower() not in CV_EXCLUDE_EXACT)
        assert k['key_events_total'] + k['key_events_recruit_total'] == expected

    def test_キーイベント明細でも求人系を別カテゴリにする(self):
        from csv_parser import classify_key_events, extract_cv_events, extract_recruit_events
        raw = {'WEB予約': 10, '求人_送信ボタン_': 4, 'recruit_click': 2, 'tap': 50,
               'まいなびリンク_top': 1, '成人電話': 3}
        patient, recruit = classify_key_events(raw)
        assert patient == {'WEB予約': 10, '電話（成人）': 3}
        assert recruit == {'求人_送信ボタン_': 4, 'recruit_click': 2, 'まいなびリンク_top': 1}
        assert extract_cv_events(raw) == patient
        assert extract_recruit_events(raw) == recruit

    def test_求人系が無い月は空の別カテゴリ(self):
        k = parse_ga4_csv(OLD_CUR)['kpis']
        assert k['key_events_recruit'] == {}
        assert k['key_events_recruit_total'] == 0

    def test_エンゲージメント概要だけでも判定できる(self):
        k = parse_ga4_csv(merge_ga4_csvs(SET3))['kpis']
        assert k['key_events_status'] == KEY_EVENTS_INFERRED
        assert k['key_events_total'] == 368
        assert k['key_events_recruit_total'] == 70

    def test_イベント一覧が無ければ判定しない(self):
        # スナップショット＋集客サマリーには「イベント名,イベント数」が無い
        k = parse_ga4_csv(merge_ga4_csvs([SNAP, ACQ]))['kpis']
        assert k['key_events_status'] == 'fallback'

    def test_従来スナップショットのイベント一覧からは判定しない(self):
        """従来形式の単一スナップショットはキーイベント未出力のまま（9/14方針）。"""
        text = Path(OLD_CUR).read_text(encoding='utf-8')
        text = text.replace('イベント名,キーイベント', 'イベント名,重要イベント')
        text = text.replace('プラットフォーム,キーイベント', 'プラットフォーム,重要イベント')
        assert 'イベント名,イベント数' in text
        k = parse_ga4_csv(text.encode('utf-8'))['kpis']
        assert k['key_events_status'] == 'missing'

    def test_明細と判定は前月比較しない(self):
        compared = compare_kpis(
            {'key_events_total': 374, 'key_events_status': KEY_EVENTS_INFERRED},
            {'key_events_total': 8, 'key_events_status': 'available'},
        )
        d = compared['key_events_total']
        assert d['comparable'] is False
        assert d['pct'] is None
        assert format_delta(d) == '比較不能（集計方法が異なる）'

    def test_判定同士は前月比較する(self):
        compared = compare_kpis(
            {'key_events_total': 374, 'key_events_status': KEY_EVENTS_INFERRED},
            {'key_events_total': 340, 'key_events_status': KEY_EVENTS_INFERRED},
        )
        assert compared['key_events_total']['comparable'] is True
        assert compared['key_events_total']['diff'] == 34


# ─────────────────────────────────────────────
# 国別セクションが無い（市区町村のみ）
# ─────────────────────────────────────────────
def test_国別セクションが無くても注記ロジックが落ちない():
    from report_logic_compare import country_note_text
    data = parse_ga4_csv(merge_ga4_csvs(SET3))
    c = data['meta']['country']
    assert c['available'] is False
    assert c['overseas_present'] is False
    assert '国別データが取得できませんでした' in country_note_text(data['meta'])


# ─────────────────────────────────────────────
# PDF（結合セットで生成）
# ─────────────────────────────────────────────
def _pdf_pages(path):
    import pdfplumber
    with pdfplumber.open(str(path)) as doc:
        return [p.extract_text() or '' for p in doc.pages]


def _assert_no_footer_overlap(path, company_name=''):
    """フッター（会社名・ページ番号）以外の文字がフッター線（10mm）より下に無いこと。

    既存テストは下端9.5mm以下の語をフッター扱いで除外するため、本文が
    フッター行まで落ちた場合（用語解説の生成バージョン行で実際に発生）を検知できない。
    ここではフッターの語そのものだけを除外する。
    """
    import pdfplumber
    with pdfplumber.open(str(path)) as doc:
        for i, page in enumerate(doc.pages, start=1):
            for w in page.extract_words():
                bottom_mm = (page.height - w['bottom']) / 72 * 25.4
                is_footer = bottom_mm < 9.0 and (
                    w['text'] == str(i) or (company_name and w['text'] in company_name)
                )
                if is_footer:
                    continue
                assert bottom_mm >= 10.0, (
                    f'p{i}: 「{w["text"]}」がフッター線に食い込んでいる（下端 {bottom_mm:.1f}mm）'
                )


class TestMultiPdf:
    @pytest.fixture(scope='class')
    def pdfs(self, tmp_path_factory):
        pytest.importorskip('reportlab')
        pytest.importorskip('pdfplumber')
        from report_logic_compare import generate_compare_report
        out = tmp_path_factory.mktemp('multi')
        cur = merge_ga4_csvs(SET4)
        prev = merge_ga4_csvs(_prev_set(SET4))
        result = {}
        for name, prev_blob, detailed, single in (
            ('compare_detailed', prev, True, False),
            ('compare_standard', prev, False, False),
            ('single_detailed', cur, True, True),
            ('single_standard', cur, False, True),
        ):
            path = out / f'{name}.pdf'
            generate_compare_report(cur, prev_blob, str(path), company_name='テスト医院C',
                                    detailed=detailed, single_mode=single)
            result[name] = path
        return result

    def test_ページ数(self, pdfs):
        assert len(_pdf_pages(pdfs['compare_detailed'])) == 11
        assert len(_pdf_pages(pdfs['compare_standard'])) == 9
        assert len(_pdf_pages(pdfs['single_detailed'])) == 11
        assert len(_pdf_pages(pdfs['single_standard'])) == 9

    def test_イベント名から判定と注記する(self, pdfs):
        for path in pdfs.values():
            text = '\n'.join(_pdf_pages(path))
            assert 'GA4のキーイベント設定は出力に含まれないため、イベント名から判定' in text

    def test_予約完了やCVRと呼ばない(self, pdfs):
        for path in pdfs.values():
            text = '\n'.join(_pdf_pages(path))
            assert '予約完了' not in text
            assert '当月CVR' not in text
            assert '前月CVR' not in text
            assert '推定CVR' not in text

    def test_NaNや未出力を出さない(self, pdfs):
        for path in pdfs.values():
            text = '\n'.join(_pdf_pages(path))
            assert 'nan' not in text.lower()
            assert '未出力' not in text

    def test_生成バージョンが入る(self, pdfs):
        from report_logic_compare import REPORT_VERSION
        for path in pdfs.values():
            assert any(REPORT_VERSION in t for t in _pdf_pages(path))

    def test_本文がフッターと重ならない(self, pdfs):
        for path in pdfs.values():
            _assert_no_footer_overlap(path, 'テスト医院C')

    def test_求人系はP4で別表に小計つきで出る(self, pdfs):
        for path in pdfs.values():
            p4 = _pdf_pages(path)[3]
            assert '求人関連イベント' in p4
            assert '求人_送信ボタン_ 61' in p4
            assert 'まいなびリンク_top 6' in p4
            assert '求人関連 小計 70' in p4
            # 左表の合計は予約・電話等の内訳の和（368）で、求人関連を含まない
            assert '合計（求人関連を除く） 368' in p4
            assert '左表の合計・参考比率には含めず別集計' in p4

    def test_求人系はP2で別集計と注記する(self, pdfs):
        for path in pdfs.values():
            p2 = _pdf_pages(path)[1]
            assert '368件' in p2
            assert '求人関連のイベント' in p2 and '上記に含めず別集計' in p2
            assert '当月 70件' in p2

    def test_求人系を除外したとは書かない(self, pdfs):
        for path in pdfs.values():
            text = '\n'.join(_pdf_pages(path))
            assert '求人系のイベントは除外' not in text

    def test_P4の求人別表と左表の注記が重ならない(self, pdfs):
        """求人の別表（右）と左表の注記が同じ領域に描かれていないこと。"""
        import pdfplumber
        with pdfplumber.open(str(pdfs['compare_detailed'])) as doc:
            page = doc.pages[3]
            words = page.extract_words()
        head = next(w for w in words if w['text'].startswith('求人関連イベント'))
        left_edge = head['x0']
        for w in words:
            if w['text'].startswith('※') and w['x0'] < left_edge:
                assert w['x1'] <= left_edge, f'左表の注記「{w["text"][:20]}」が求人の別表に食い込む'

    def test_単月総括はアクティブユーザーあたりと表記する(self, pdfs):
        for name in ('single_detailed', 'single_standard'):
            pages = _pdf_pages(pdfs[name])
            summary = pages[-2]
            flat = summary.replace('\n', '')  # 折り返し位置に依存しないように
            assert '1セッションあたり' not in summary
            assert 'アクティブユーザーあたりの平均エンゲージメント時間' in summary
            assert '求人関連のイベント 70 件は含めず別に集計' in flat

    def test_単月総括が右端まで伸びない(self, pdfs):
        """総括の文が右余白（12mm）を残して折り返されていること。"""
        import pdfplumber
        for name in ('single_detailed', 'single_standard'):
            with pdfplumber.open(str(pdfs[name])) as doc:
                page = doc.pages[len(doc.pages) - 2]
                limit = page.width - 11.5 / 25.4 * 72
                for w in page.extract_words():
                    if w['top'] < 20 / 25.4 * 72:   # タイトルバー（右寄せの期間表記）は対象外
                        continue
                    assert w['x1'] <= limit, f'{name}: 「{w["text"][:20]}」が右端まで伸びている'

    def test_計測不完全の注記が加わってもフッターと重ならない(self, tmp_path):
        """注記が最も多くなる組み合わせ（イベント名判定＋計測不完全）で用語解説が溢れない。"""
        from report_logic_compare import generate_compare_report
        acq = _read(ACQ).decode('utf-8')
        lines = acq.split('\n')
        start = next(i for i, l in enumerate(lines) if l.startswith('N 日目,アクティブ ユーザー'))
        for k in range(1, 8):  # 月初7日を未計測にする
            day, _ = lines[start + k].split(',')
            lines[start + k] = f'{day},0' + ('\r' if lines[start + k].endswith('\r') else '')
        cur = merge_ga4_csvs([_read(SNAP), '\n'.join(lines).encode('utf-8'), _read(ENG)])
        assert parse_ga4_csv(cur)['meta']['measure']['incomplete'] is True
        for detailed in (True, False):
            out = tmp_path / f'incomplete_{detailed}.pdf'
            generate_compare_report(cur, merge_ga4_csvs(_prev_set(SET3)), str(out),
                                    company_name='テスト医院C', detailed=detailed)
            _assert_no_footer_overlap(out, 'テスト医院C')

    def test_集計方法が異なる月とは比較しない(self, tmp_path):
        from report_logic_compare import generate_compare_report
        out = tmp_path / 'mismatch.pdf'
        generate_compare_report(merge_ga4_csvs(SET4), OLD_CUR, str(out),
                                company_name='混在', detailed=True)
        text = '\n'.join(_pdf_pages(out))
        assert '集計方法が異なるため比較不能' in text
        assert 'キーイベント比較不能（当月: イベント名から判定 / 前月: GA4のキーイベント明細）' in text
        _assert_no_footer_overlap(out, '混在')


class TestPagesWithoutExtraColumns:
    """上位ページが「表示回数」列のみ（エンゲージメントの概要由来）の場合。"""

    def test_表示回数のみでも推定接触量を出す(self, tmp_path):
        pytest.importorskip('reportlab')
        from report_logic_compare import generate_compare_report
        cur = merge_ga4_csvs([ACQ, ENG])
        data = parse_ga4_csv(cur)
        assert list(data['df_pages'].columns) == ['ページ タイトルとスクリーン クラス', '表示回数']
        out = tmp_path / 'pv_only.pdf'
        generate_compare_report(cur, merge_ga4_csvs(_prev_set([ACQ, ENG])), str(out),
                                company_name='PVのみ', detailed=True)
        pages = _pdf_pages(out)
        p6 = pages[5]
        assert '推定接触量' in p6
        assert '取得不能' not in p6
        assert 'nan' not in p6.lower()
        assert '1PVあたり平均滞在時間（目安）: 当月 0秒' not in p6
        _assert_no_footer_overlap(out, 'PVのみ')

    def test_ページの表示回数が無い月は取得不能と表示する(self, tmp_path):
        pytest.importorskip('reportlab')
        import pdfplumber
        from report_logic_compare import CompareReportCanvas
        data = parse_ga4_csv(merge_ga4_csvs(SET3))
        out = tmp_path / 'no_pv.pdf'
        rc = CompareReportCanvas(str(out))
        rc.draw_page_engagement_page(data['df_pages'], None, data['kpis'], data['kpis'])
        rc.save()
        with pdfplumber.open(str(out)) as doc:
            text = doc.pages[0].extract_text() or ''
        assert '前月 取得不能' in text
        assert '前月 0秒' not in text


# ─────────────────────────────────────────────
# P4 イベント別グラフ（イベントが多いと横軸ラベルが重なっていた）
# ─────────────────────────────────────────────
class TestKeyEventsChart:
    def _many(self, n=12):
        return ({f'とても長いイベント名_{i:02d}_ボタンクリック': 100 - i for i in range(n)},
                {f'とても長いイベント名_{i:02d}_ボタンクリック': 90 - i for i in range(n)})

    def test_上位N件とその他に集約し合計は保つ(self):
        from chart_generator_compare import KEY_EVENTS_CHART_TOP_N, key_events_chart_items
        cur, prev = self._many(12)
        items = key_events_chart_items(cur, prev)
        assert len(items) == KEY_EVENTS_CHART_TOP_N + 1
        assert items[-1][0] == f'その他（{12 - KEY_EVENTS_CHART_TOP_N}種）'
        assert sum(i[1] for i in items) == sum(cur.values())
        assert sum(i[2] for i in items) == sum(prev.values())

    def test_ラベルは省略して長さを揃える(self):
        from chart_generator_compare import KEY_EVENTS_LABEL_MAX, key_events_chart_items
        cur, prev = self._many(3)
        for label, _, _ in key_events_chart_items(cur, prev):
            assert len(label) <= KEY_EVENTS_LABEL_MAX

    def test_件数が少なければ集約しない(self):
        from chart_generator_compare import key_events_chart_items
        items = key_events_chart_items({'WEB予約': 5, '電話': 3}, {'WEB予約': 4})
        assert [i[0] for i in items] == ['WEB予約', '電話']
        assert items[1] == ('電話', 3, 0)

    def test_縦軸ラベルが重ならない(self):
        """横棒にしたグラフで、イベント名ラベル同士の描画領域が重ならないこと。"""
        import matplotlib.pyplot as plt
        from chart_generator_compare import make_key_events_grouped_bar
        cur, prev = self._many(12)
        captured = {}
        orig = plt.close

        def keep(fig=None):
            captured['fig'] = fig
        plt.close = keep
        try:
            assert make_key_events_grouped_bar(cur, prev, '当月', '前月')
        finally:
            plt.close = orig
        fig = captured['fig']
        renderer = fig.canvas.get_renderer()
        ax = fig.axes[0]
        boxes = [t.get_window_extent(renderer) for t in ax.get_yticklabels() if t.get_text()]
        assert len(boxes) == 8
        for a, b in zip(boxes, boxes[1:]):
            assert not a.overlaps(b)
        plt.close(fig)
