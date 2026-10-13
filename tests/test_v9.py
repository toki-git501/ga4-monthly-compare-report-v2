#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v9 の回帰テスト（二重計上の防止・入力の検証）。

- 完了到達（末尾が「完了」等）のイベントは、対応するクリックがあれば合計に入れない
- 件数・総ユーザー数とも同じ2つのイベント（同じ操作の重複計測の疑い）は1つ分のみ合計に入れる
- イベント名から判定する場合、予約・電話等と判定できないイベントは「未分類」として合計に入れない
- 数値の欠損・複数ファイル間の表の不一致は停止する（黙って0や先勝ちにしない）

イベント名・医院名は合成した匿名の名前のみを使う（リポジトリは Public）。
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

import event_classify as ec  # noqa: E402
from event_classify import (  # noqa: E402
    aggregate_event_rows, classify_event_counts, completion_base_name,
    find_same_count_groups, is_patient_action_name, is_recruit_event, is_system_event,
)
from csv_merge import CsvMergeError, merge_ga4_csvs, merge_ga4_csvs_with_notes  # noqa: E402
from csv_parser import (  # noqa: E402
    KEY_EVENTS_FALLBACK, KEY_EVENTS_INFERRED, parse_ga4_csv, period_labels, weighted_engagement,
)
from compare_utils import (  # noqa: E402
    compare_kpis, format_delta, key_events_comparison, shorten_labels_unique,
)
from input_validation import input_signature, validate_csv_inputs  # noqa: E402

SNAP = os.path.join(MULTI, 'snapshot.csv')
ACQ = os.path.join(MULTI, 'acquisition.csv')
ENG = os.path.join(MULTI, 'engagement.csv')
EVT = os.path.join(MULTI, 'events.csv')
OLD_CUR = os.path.join(FIX, 'sample_cur.csv')
OLD_PREV = os.path.join(FIX, 'sample_prev.csv')

# 合成イベント（クリック2種が同数・同ユーザー数、完了到達2種、電話1種）
DUP_ROWS = [
    ('page_view', 9000, 3000), ('user_engagement', 7000, 2000), ('scroll', 1200, 900),
    ('電話_ボタン', 292, 148),
    ('予約_外部_A', 246, 169),
    ('予約_外部_B', 246, 169),
    ('click', 198, 142),
    ('予約_外部_A_完了', 26, 13),
    ('予約_外部_B_完了', 4, 2),
]
DUP_ROWS_PREV = [
    ('page_view', 9100, 3100), ('scroll', 1300, 950),
    ('予約_外部_A', 277, 193),
    ('予約_外部_B', 277, 193),
    ('電話_ボタン', 265, 153),
]


def _read(path):
    return Path(path).read_bytes()


def _shift_period(blob: bytes, start='20260818', end='20260914') -> bytes:
    text = blob.decode('utf-8')
    return (text.replace('20260915', start).replace('20261012', end)).encode('utf-8')


def _replace_table(blob: bytes, header_prefix: str, new_lines: list) -> bytes:
    """header_prefix で始まる表のデータ行を new_lines に置き換える（改行コードは維持）。"""
    text = blob.decode('utf-8')
    lines = text.split('\n')
    i = next(k for k, l in enumerate(lines) if l.startswith(header_prefix))
    cr = '\r' if lines[i].endswith('\r') else ''
    j = i + 1
    while j < len(lines) and lines[j].strip() and not lines[j].startswith('#'):
        j += 1
    lines[i + 1:j] = [l + cr for l in new_lines]
    return '\n'.join(lines).encode('utf-8')


def _with_events(rows, with_users=True, prev=False):
    """エンゲージメントの概要と「イベント」レポートのイベント表を rows に揃えた4ファイル。"""
    eng = _replace_table(_read(ENG), 'イベント名,イベント数', [f'{n},{c}' for n, c, _ in rows])
    files = [_read(SNAP), _read(ACQ), eng]
    if with_users:
        evt = _replace_table(_read(EVT), 'イベント名,イベント数,総ユーザー数',
                             [f'{n},{c},{u},1.0,0' for n, c, u in rows])
        files.append(evt)
    if prev:
        files = [_shift_period(f) for f in files]
    return files


def _pdf_pages(path):
    import pdfplumber
    with pdfplumber.open(str(path)) as doc:
        return [p.extract_text() or '' for p in doc.pages]


# ─────────────────────────────────────────────
# 完了到達の判定（末尾トークン）
# ─────────────────────────────────────────────
class TestCompletionName:
    @pytest.mark.parametrize('name, base', [
        ('予約_外部_A_完了', '予約_外部_a'),
        ('reserve-complete', 'reserve'),
        ('form_completed', 'form'),
        ('contact thanks', 'contact'),
        ('contact_thank_you', 'contact'),
        ('booking_done', 'booking'),
        ('ページ_到達', 'ページ'),
        ('ＷＥＢ予約＿完了', 'web予約'),   # 全角も正規化して判定
    ])
    def test_末尾トークンで完了到達と判定する(self, name, base):
        assert completion_base_name(name) == base

    @pytest.mark.parametrize('name', [
        'form_incomplete', 'incomplete', 'completion', '予約完了', 'complete_flow', '完了',
    ])
    def test_部分一致や区切りなしは完了到達としない(self, name):
        assert completion_base_name(name) is None

    def test_システム系と求人は先に分類する(self):
        r = classify_event_counts({'video_complete': 50, 'video_start': 60,
                                   '求人_応募_完了': 3, '求人_応募': 9})
        assert r['completion'] == {}
        assert r['recruit'] == {'求人_応募': 9, '求人_応募_完了': 3}
        assert r['patient'] == {}


class TestCompletionClassify:
    def test_基底イベントがあれば合計に入れない(self):
        r = classify_event_counts({'予約_A': 100, '予約_A_完了': 20, '電話': 50})
        assert r['patient'] == {'予約_A': 100, '電話': 50}
        assert r['completion'] == {'予約_A_完了': 20}
        assert r['completion_base'] == {'予約_A_完了': '予約_A'}
        # クリック件数から完了件数を引かない
        assert r['patient']['予約_A'] == 100

    def test_基底イベントが無ければ唯一の計測として合計に入れる(self):
        r = classify_event_counts({'予約_A_完了': 20, '電話': 50})
        assert r['patient'] == {'電話': 50, '予約_A_完了': 20}
        assert r['completion'] == {}
        assert r['completion_counted'] == ['予約_A_完了']

    def test_判定の月は基底なし完了も許可語に合致する場合だけ合計に入れる(self):
        r = classify_event_counts({'route_done': 20, 'form_submit_complete': 7, '予約_X_完了': 5},
                                  mode='inferred')
        assert r['patient'] == {'予約_X_完了': 5}
        assert r['unclassified'] == {'route_done': 20, 'form_submit_complete': 7}
        assert r['completion_counted'] == ['予約_X_完了']
        # GA4のキーイベント明細の月は従来どおり合計に入れる
        r = classify_event_counts({'route_done': 20}, mode='available')
        assert r['patient'] == {'route_done': 20}

    def test_基底イベントは同月の別イベントとして存在する場合のみ(self):
        r = classify_event_counts({'予約_A': 10, '予約_B_完了': 5})
        assert r['completion'] == {}
        assert r['completion_counted'] == ['予約_B_完了']


# ─────────────────────────────────────────────
# 同数イベントの二重計測の疑い
# ─────────────────────────────────────────────
class TestSameCountGroups:
    def test_件数とユーザー数が同じ共通先頭の2イベントを検出する(self):
        groups, unverified = find_same_count_groups(
            {'予約_外部_B': 246, '予約_外部_A': 246, '電話_ボタン': 292},
            {'予約_外部_B': 169, '予約_外部_A': 169, '電話_ボタン': 148})
        assert groups == [{'kept': '予約_外部_A', 'excluded': ['予約_外部_B'],
                           'count': 246, 'users': 169}]
        assert unverified == []

    def test_最小件数未満は対象外(self):
        n = ec.DEDUPE_MIN_COUNT - 1
        groups, unverified = find_same_count_groups({'予約_A': n, '予約_B': n},
                                                    {'予約_A': 30, '予約_B': 30})
        assert groups == [] and unverified == []
        groups, _ = find_same_count_groups({'予約_A': n + 1, '予約_B': n + 1},
                                           {'予約_A': 30, '予約_B': 30})
        assert len(groups) == 1

    def test_閾値は件数20以上かつユーザー数10以上(self):
        assert ec.DEDUPE_MIN_COUNT == 20 and ec.DEDUPE_MIN_USERS == 10
        u = ec.DEDUPE_MIN_USERS - 1
        groups, unverified = find_same_count_groups({'予約_A': 50, '予約_B': 50},
                                                    {'予約_A': u, '予約_B': u})
        assert groups == [] and unverified == []
        groups, _ = find_same_count_groups({'予約_A': 50, '予約_B': 50},
                                           {'予約_A': u + 1, '予約_B': u + 1})
        assert len(groups) == 1

    def test_明細の月はイベント数も一致する場合だけ除外する(self):
        kev = {'予約_A': 50, '予約_B': 50}
        users = {'予約_A': 30, '予約_B': 30}
        # キーイベント数・ユーザー数は同じでもイベント数が違えば別の操作
        r = classify_event_counts(kev, mode='available', users=users,
                                  event_counts={'予約_A': 80, '予約_B': 50})
        assert r['dedup_groups'] == [] and r['dedup_unverified'] == []
        assert sum(r['patient'].values()) == 100
        # イベント数が取れない → 除外せず注記のみ
        r = classify_event_counts(kev, mode='available', users=users)
        assert r['dedup_groups'] == []
        assert r['dedup_unverified'] == [{'names': ['予約_A', '予約_B'], 'count': 50}]
        # イベント数も一致 → 1つ分のみ
        r = classify_event_counts(kev, mode='available', users=users,
                                  event_counts={'予約_A': 50, '予約_B': 50})
        assert sum(r['patient'].values()) == 50

    def test_ユーザー数が無ければ除外せず注記のみ(self):
        groups, unverified = find_same_count_groups({'予約_A': 50, '予約_B': 50},
                                                    {'予約_A': None, '予約_B': None})
        assert groups == []
        assert unverified == [{'names': ['予約_A', '予約_B'], 'count': 50}]

    def test_ユーザー数が違えば同一操作としない(self):
        groups, unverified = find_same_count_groups({'予約_A': 50, '予約_B': 50},
                                                    {'予約_A': 30, '予約_B': 31})
        assert groups == [] and unverified == []

    def test_共通の先頭が無ければ同一操作としない(self):
        groups, _ = find_same_count_groups({'web_reserve': 50, 'line_reserve': 50},
                                           {'web_reserve': 30, 'line_reserve': 30})
        assert groups == []

    def test_先頭3文字が同じなら共通の先頭とみなす(self):
        groups, _ = find_same_count_groups({'予約ボタン上': 50, '予約ボタン下': 50},
                                           {'予約ボタン上': 30, '予約ボタン下': 30})
        assert len(groups) == 1

    def test_電話と電話以外は組み合わせない(self):
        groups, _ = find_same_count_groups({'cta_tel': 50, 'cta_web': 50},
                                           {'cta_tel': 30, 'cta_web': 30})
        assert groups == []

    def test_求人と完了到達は組み合わせない(self):
        r = classify_event_counts(
            {'予約_A': 50, '予約_A_完了': 50, '予約_求人': 50},
            users={'予約_A': 30, '予約_A_完了': 30, '予約_求人': 30})
        assert r['dedup_groups'] == []
        assert r['patient'] == {'予約_A': 50}

    def test_代表1件のみ合計に入れ残りは除外として保持する(self):
        r = classify_event_counts({n: c for n, c, _ in DUP_ROWS}, mode='inferred',
                                  users={n: u for n, _, u in DUP_ROWS})
        assert r['patient'] == {'電話_ボタン': 292, '予約_外部_A': 246}
        assert sum(r['patient'].values()) == 538
        assert r['dedup_excluded'] == {'予約_外部_B': 246}
        assert r['completion'] == {'予約_外部_A_完了': 26, '予約_外部_B_完了': 4}

    def test_無効化フラグで従来どおりすべて合計に入れる(self, monkeypatch):
        counts = {n: c for n, c, _ in DUP_ROWS}
        users = {n: u for n, _, u in DUP_ROWS}
        r = classify_event_counts(counts, mode='inferred', users=users, dedupe=False)
        assert sum(r['patient'].values()) == 784
        assert r['dedup_excluded'] == {}
        monkeypatch.setattr(ec, 'DEDUPE_SAME_COUNT_EVENTS', False)
        r = classify_event_counts(counts, mode='inferred', users=users)
        assert sum(r['patient'].values()) == 784

    def test_入力順で結果が変わらない(self):
        rows = [(n, c, u) for n, c, u in DUP_ROWS]
        baseline = classify_event_counts({n: c for n, c, _ in rows}, mode='inferred',
                                         users={n: u for n, _, u in rows})
        for perm in itertools.islice(itertools.permutations(rows), 0, 200, 7):
            r = classify_event_counts({n: c for n, c, _ in perm}, mode='inferred',
                                      users={n: u for n, _, u in perm})
            assert r == baseline


# ─────────────────────────────────────────────
# 表記ゆれ・未分類（イベント名から判定）
# ─────────────────────────────────────────────
class TestNameMatching:
    def test_全角半角を正規化して照合する(self):
        assert is_recruit_event('ＲＥＣＲＵＩＴ_button')
        assert is_recruit_event('ﾏｲﾅﾋﾞリンク')
        assert is_system_event('ＣＬＩＣＫ')

    @pytest.mark.parametrize('name', [
        'phone_click', 'inquiry_click', 'cta_line', 'web予約', 'TEL予約', 'お問い合わせ送信',
        '問診票_成人', 'reservationButton', 'booking_start', 'カウンセリング申込',
    ])
    def test_予約電話等の語を含めば患者アクション(self, name):
        assert is_patient_action_name(name)

    @pytest.mark.parametrize('name', ['route_click', 'timeline_view', 'hotel_info', 'form_submit',
                                      'facebook_link', 'underline_click'])
    def test_語を含まなければ未分類(self, name):
        assert not is_patient_action_name(name)

    def test_判定では未分類を合計に入れずGA4明細では従来どおり(self):
        counts = {'phone_click': 43, 'inquiry_click': 36, 'route_click': 21}
        inferred = classify_event_counts(counts, mode='inferred')
        assert inferred['patient'] == {'phone_click': 43, 'inquiry_click': 36}
        assert inferred['unclassified'] == {'route_click': 21}
        available = classify_event_counts(counts, mode='available')
        assert sum(available['patient'].values()) == 100
        assert available['unclassified'] == {}

    def test_原名を表示し大文字小文字違いを統合しない(self):
        r = classify_event_counts({'Reserve_Top': 5, 'reserve_top': 3}, mode='available')
        assert r['patient'] == {'Reserve_Top': 5, 'reserve_top': 3}


class TestAggregateRows:
    def test_完全に同じ行は合算せず内容が違う同名行は合算する(self):
        rows = [
            ('予約_A', 10, 5, ('予約_A', '10', '5')),
            ('予約_A', 10, 5, ('予約_A', '10', '5')),     # 二重貼付
            ('予約_A', 20, 8, ('予約_A', '20', '8')),     # 分割行
            ('電話', 7, 4, ('電話', '7', '4')),
        ]
        counts, users = aggregate_event_rows(rows)
        assert counts == {'予約_A': 30, '電話': 7}
        # 複数行の総ユーザー数は合算しない
        assert users == {'予約_A': None, '電話': 4}

    def test_明細の同名行は上書きせず合算する(self):
        k = parse_ga4_csv('イベント名,キーイベント\n予約_A,10\n予約_A,20\n\n'.encode('utf-8'))['kpis']
        assert k['key_events'] == {'予約_A': 30}


# ─────────────────────────────────────────────
# 結合セットでの集計（P2/P4 と同じ単一ソース）
# ─────────────────────────────────────────────
class TestParsedDedupe:
    def test_当月前月とも同じ規則で合計を作る(self):
        cur = parse_ga4_csv(merge_ga4_csvs(_with_events(DUP_ROWS)))['kpis']
        prev = parse_ga4_csv(merge_ga4_csvs(_with_events(DUP_ROWS_PREV, prev=True)))['kpis']
        assert cur['key_events_status'] == KEY_EVENTS_INFERRED
        assert cur['key_events_total'] == 538
        assert prev['key_events_total'] == 542
        assert cur['key_events_dedup_excluded'] == {'予約_外部_B': 246}
        assert prev['key_events_dedup_excluded'] == {'予約_外部_B': 277}
        assert cur['key_events_completion_total'] == 30
        assert prev['key_events_completion_total'] == 0
        d = compare_kpis(cur, prev)['key_events_total']
        assert d['comparable'] is True and d['diff'] == -4

    def test_総ユーザー数が無ければ除外しない(self):
        k = parse_ga4_csv(merge_ga4_csvs(_with_events(DUP_ROWS, with_users=False)))['kpis']
        assert k['key_events_total'] == 784
        assert k['key_events_dedup_excluded'] == {}
        assert k['key_events_dedup_unverified'] == [
            {'names': ['予約_外部_A', '予約_外部_B'], 'count': 246}]

    def test_GA4明細でも総ユーザー数はイベント表から使う(self):
        kev = '\n'.join(f'{n},{c}' for n, c, _ in DUP_ROWS if n not in ('page_view', 'user_engagement', 'scroll', 'click'))
        snap = _read(SNAP).decode('utf-8').replace(
            'プラットフォーム,キーイベント', f'イベント名,キーイベント\r\n{kev}\r\n\r\n# x\r\nプラットフォーム,キーイベント')
        files = _with_events(DUP_ROWS)
        files[0] = snap.encode('utf-8')
        k = parse_ga4_csv(merge_ga4_csvs(files))['kpis']
        assert k['key_events_status'] == 'available'
        assert k['key_events_total'] == 538


class TestPdfDedupe:
    @pytest.fixture(scope='class')
    def pdfs(self, tmp_path_factory):
        pytest.importorskip('reportlab')
        pytest.importorskip('pdfplumber')
        from report_logic_compare import generate_compare_report
        out = tmp_path_factory.mktemp('v9')
        cur = merge_ga4_csvs(_with_events(DUP_ROWS))
        prev = merge_ga4_csvs(_with_events(DUP_ROWS_PREV, prev=True))
        result = {}
        for name, detailed, single in (('cmp_std', False, False), ('cmp_det', True, False),
                                       ('single_std', False, True)):
            path = out / f'{name}.pdf'
            generate_compare_report(cur, cur if single else prev, str(path),
                                    company_name='テスト医院D', detailed=detailed,
                                    single_mode=single)
            result[name] = path
        return result

    SENTENCE = ('同数の「予約_外部_A」と「予約_外部_B」は、同じ操作を2つのイベントで数えている'
                '可能性があるため、合計には1つ分のみ含めています')

    def test_P2_P4_総括に平易な文で書く(self, pdfs):
        for name, path in pdfs.items():
            pages = [p.replace('\n', '') for p in _pdf_pages(path)]
            assert self.SENTENCE in pages[1], name           # P2
            assert self.SENTENCE in pages[3], name           # P4
            assert self.SENTENCE in pages[-2], name          # 総括

    def test_P4は代表1行のみ合計に入れ除外行を残す(self, pdfs):
        p4 = _pdf_pages(pdfs['cmp_std'])[3]
        assert '合計 538 542' in p4
        assert '予約_外部_B (246) (277) 同数のため合計から除外' in p4
        assert '予約・電話アクション 小計 538 542' in p4

    def test_完了到達は別表で予約成立数ではないと書く(self, pdfs):
        p4 = _pdf_pages(pdfs['cmp_std'])[3]
        assert '完了画面への到達' in p4
        assert '完了到達 小計 30 0 +30' in p4
        assert '予約成立数ではありません' in p4.replace('\n', '')

    def test_予約完了やnanを出さない(self, pdfs):
        for path in pdfs.values():
            text = '\n'.join(_pdf_pages(path))
            assert '予約完了' not in text
            assert 'nan' not in text.lower()

    def test_本文がフッターと重ならない(self, pdfs):
        from test_multi_csv import _assert_no_footer_overlap
        for path in pdfs.values():
            _assert_no_footer_overlap(path, 'テスト医院D')


# ─────────────────────────────────────────────
# 数値の欠損（P1-1）
# ─────────────────────────────────────────────
class TestMissingNumbers:
    def test_エンゲージメント日別表のハイフンは停止する(self):
        eng = _read(ENG).decode('utf-8').split('\n')
        i = next(k for k, l in enumerate(eng) if l.startswith('N 日目,アクティブ ユーザーあたりの平均エンゲージメント時間'))
        day = eng[i + 3].split(',')[0]
        eng[i + 3] = f'{day},-\r'
        errors = validate_csv_inputs([SNAP, ACQ, '\n'.join(eng).encode('utf-8')],
                                     cur_names=['s.csv', 'a.csv', 'e.csv'])
        joined = '\n'.join(errors)
        assert '「平均エンゲージメント時間」' in joined
        assert '数値として読めない値' in joined

    def test_キーイベント明細のハイフンは停止する(self):
        text = Path(OLD_CUR).read_text(encoding='utf-8').replace(
            'イベント名,キーイベント\n\n', 'イベント名,キーイベント\n予約_A,-\n\n', 1)
        errors = validate_csv_inputs(text.encode('utf-8'))
        assert any('キーイベント（イベント名別）' in e and '「-」' in e for e in errors)

    def test_空欄のチャネル値は停止する(self):
        text = Path(OLD_CUR).read_text(encoding='utf-8').split('\n')
        i = next(k for k, l in enumerate(text) if l.startswith('セッションのメインのチャネル グループ'))
        name = text[i + 1].split(',')[0]
        text[i + 1] = f'{name},'
        errors = validate_csv_inputs('\n'.join(text).encode('utf-8'))
        assert any('チャネル別セッション' in e for e in errors)

    def test_正常なデータは止めない(self):
        assert validate_csv_inputs(OLD_CUR, OLD_PREV) == []
        assert validate_csv_inputs([SNAP, ACQ, ENG, EVT]) == []


# ─────────────────────────────────────────────
# 同じ表が複数ファイルにある（P1-2）
# ─────────────────────────────────────────────
class TestDuplicateTables:
    def test_値が食い違えばファイル名つきで停止する(self):
        evt = _read(EVT).decode('utf-8').replace('WEB予約,136,', 'WEB予約,999,').encode('utf-8')
        errors = validate_csv_inputs([_read(SNAP), _read(ACQ), _read(ENG), evt],
                                     cur_names=['snap.csv', 'acq.csv', 'eng.csv', 'evt.csv'])
        joined = '\n'.join(errors)
        assert '値が一致しません' in joined
        assert 'eng.csv' in joined and 'evt.csv' in joined

    def test_食い違いは入力順によらず停止する(self):
        evt = _read(EVT).decode('utf-8').replace('WEB予約,136,', 'WEB予約,999,').encode('utf-8')
        files = [_read(SNAP), _read(ACQ), _read(ENG), evt]
        for perm in itertools.permutations(range(4)):
            errors = validate_csv_inputs([files[i] for i in perm])
            assert any('値が一致しません' in e for e in errors), perm

    def test_一致していれば入力順によらず同じ値(self):
        files = [_read(SNAP), _read(ACQ), _read(ENG), _read(EVT)]
        baseline = parse_ga4_csv(merge_ga4_csvs(files))['kpis']
        for perm in itertools.permutations(files):
            assert parse_ga4_csv(merge_ga4_csvs(list(perm)))['kpis'] == baseline

    def test_同じ内容のファイルの二重指定は倍にせず注意を返す(self):
        files = [_read(SNAP), _read(ACQ), _read(ENG), _read(EVT), _read(EVT)]
        names = ['s.csv', 'a.csv', 'e.csv', 'v.csv', 'v (1).csv']
        merged, notes = merge_ga4_csvs_with_notes(files, names)
        assert notes == ['「v (1).csv」は「v.csv」と同じ内容のため、2つ目以降は読み込みませんでした。']
        k = parse_ga4_csv(merged)['kpis']
        assert k['key_events_total'] == 368
        assert validate_csv_inputs(files, cur_names=names) == []


# ─────────────────────────────────────────────
# プラットフォーム合計のみ（P1-3）
# ─────────────────────────────────────────────
def _fallback_set(prev=False):
    eng = _read(ENG).decode('utf-8').replace('イベント名,イベント数', 'イベント名,件数').encode('utf-8')
    files = [_read(SNAP), _read(ACQ), eng]
    return [_shift_period(f) for f in files] if prev else files


class TestFallback:
    def test_内訳なしは予約電話の件数として使わない(self):
        k = parse_ga4_csv(merge_ga4_csvs(_fallback_set()))['kpis']
        assert k['key_events_status'] == KEY_EVENTS_FALLBACK
        assert k['key_events_total'] == 0
        assert k['key_events_platform_total'] > 0

    def test_明細と内訳なしは比較しない(self):
        assert key_events_comparison('available', KEY_EVENTS_FALLBACK) == (False, 'breakdown_unavailable')
        assert key_events_comparison(KEY_EVENTS_FALLBACK, KEY_EVENTS_FALLBACK)[0] is False
        d = compare_kpis({'key_events_status': 'available', 'key_events_total': 5},
                         {'key_events_status': KEY_EVENTS_FALLBACK, 'key_events_total': 0})
        assert format_delta(d['key_events_total']) == '比較不能（内訳取得不能）'

    def test_PDFで0件と表示しない(self, tmp_path):
        pytest.importorskip('reportlab')
        from report_logic_compare import generate_compare_report
        out = tmp_path / 'fallback.pdf'
        generate_compare_report(merge_ga4_csvs(_fallback_set()),
                                merge_ga4_csvs(_fallback_set(prev=True)), str(out),
                                company_name='テスト医院E', detailed=True)
        pages = _pdf_pages(out)
        text = '\n'.join(pages)
        assert '当月・前月とも0件' not in text
        assert '当月・前月とも発生回数は0件' not in text
        assert 'キーイベント発生回数: 0件' not in text
        assert '内訳取得不能' in pages[1]
        assert 'イベント別の内訳が無いため' in pages[3]
        assert '0件ではありません' in pages[8]          # 広告ページ


# ─────────────────────────────────────────────
# エンゲージメント時間（P1-5）
# ─────────────────────────────────────────────
class TestEngagementWeighting:
    def _frames(self):
        import pandas as pd
        eng = pd.DataFrame({'d': [0, 1, 2], 'v': [100.0, 50.0, 10.0]})
        act = pd.DataFrame({'d': [0, 1, 2], 'u': [10, 30, 60]})
        return eng, act

    def test_日付で結合し行順に依存しない(self):
        eng, act = self._frames()
        base, problem = weighted_engagement(eng, act)
        assert problem == '' and base == pytest.approx((1000 + 1500 + 600) / 100)
        rev, _ = weighted_engagement(eng.iloc[::-1].reset_index(drop=True), act)
        assert rev == pytest.approx(base)

    def test_日付の欠けや重複は止める(self):
        eng, act = self._frames()
        avg, problem = weighted_engagement(eng.iloc[:2], act)
        assert avg is None and '揃っていません' in problem
        import pandas as pd
        dup = pd.concat([eng, eng.iloc[[0]]], ignore_index=True)
        avg, problem = weighted_engagement(dup, act)
        assert avg is None and '複数' in problem

    def test_日付がずれたCSVは入力検証で止まる(self):
        eng = _read(ENG).decode('utf-8').split('\n')
        i = next(k for k, l in enumerate(eng) if l.startswith('N 日目,アクティブ ユーザーあたりの平均エンゲージメント時間'))
        del eng[i + 2]
        errors = validate_csv_inputs([SNAP, ACQ, '\n'.join(eng).encode('utf-8')])
        assert any('加重平均できません' in e for e in errors)


# ─────────────────────────────────────────────
# チャネルの代替取得（P1-6）
# ─────────────────────────────────────────────
class TestChannels:
    def test_参照元別アクティブユーザーを新規ユーザーとして使わない(self):
        text = _read(SNAP).decode('utf-8')
        # サマリー行（実測の新規ユーザー）が無い場合も、代替表の値を新規ユーザーとして足さない
        text = text.replace('アクティブ ユーザー,新規ユーザー数,アクティブ ユーザーあたりの平均エンゲージメント時間,イベント数',
                            'サマリー以外')
        data = parse_ga4_csv(text.encode('utf-8'))
        assert data['ch_new_metric'] == 'active_users'
        assert data['df_new'] is None
        active_sum = int(data['df_ch_new'].iloc[:, 1].sum())
        assert active_sum > 0
        assert data['kpis']['new_users'] == 0

    def test_月ごとに分類方法が違っても落ちず比較不能と表示する(self, tmp_path):
        pytest.importorskip('reportlab')
        from report_logic_compare import generate_compare_report
        cur = merge_ga4_csvs([SNAP, ACQ, ENG, EVT])
        acq_no_group = _read(ACQ).decode('utf-8') \
            .replace('セッションのメインのチャネル グループ', 'セッションの別項目') \
            .replace('ユーザーの最初のメインのチャネル グループ', 'ユーザーの別項目').encode('utf-8')
        prev = merge_ga4_csvs([_shift_period(f) for f in (_read(SNAP), acq_no_group, _read(ENG), _read(EVT))])
        assert parse_ga4_csv(prev)['ch_ses_dimension'] == 'source_medium'
        out = tmp_path / 'mixed_channels.pdf'
        generate_compare_report(cur, prev, str(out), company_name='テスト医院F', detailed=True)
        p3 = _pdf_pages(out)[2]
        assert 'チャネル比較不能' in p3


# ─────────────────────────────────────────────
# 求人関連の取得状態（P2-8）
# ─────────────────────────────────────────────
def test_前月未出力でも当月の求人明細を出し0件と書かない(tmp_path):
    pytest.importorskip('reportlab')
    from report_logic_compare import generate_compare_report
    prev_text = Path(OLD_PREV).read_text(encoding='utf-8')
    prev_text = prev_text.replace('イベント名,キーイベント', 'イベント名,重要イベント')
    prev_text = prev_text.replace('プラットフォーム,キーイベント', 'プラットフォーム,重要イベント')
    out = tmp_path / 'recruit_prev_missing.pdf'
    generate_compare_report(merge_ga4_csvs([SNAP, ACQ, ENG, EVT]), prev_text.encode('utf-8'),
                            str(out), company_name='テスト医院G', detailed=False)
    pages = _pdf_pages(out)
    assert '当月 70件 / 前月 未出力（比較不能）' in pages[1].replace('\n', '')
    assert '求人_送信ボタン_ 61 未出力 比較不能' in pages[3]
    assert '求人関連 小計 70 未出力 比較不能' in pages[3]


# ─────────────────────────────────────────────
# 長いイベント名（P2-10）
# ─────────────────────────────────────────────
class TestLabels:
    def test_省略後も区別できる(self):
        names = ['とても長い共通の名前のボタンクリック_上', 'とても長い共通の名前のボタンクリック_下', '電話']
        labels = shorten_labels_unique(names, 14)
        assert len(set(labels)) == 3
        assert all(len(lab) <= 14 for lab in labels)
        assert labels[2] == '電話'

    def test_グラフのラベルも区別できる(self):
        from chart_generator_compare import key_events_chart_items
        cur = {'とても長い共通の名前のボタンクリック_上': 10, 'とても長い共通の名前のボタンクリック_下': 9}
        items = key_events_chart_items(cur, {})
        assert len({i[0] for i in items}) == 2


# ─────────────────────────────────────────────
# 期間・メタ情報（P2-11）
# ─────────────────────────────────────────────
class TestPeriod:
    def test_暦月と暦月でない期間のラベル(self):
        assert period_labels('20260901', '20260930') == {
            'is_calendar_month': True, 'month_label': '2026年9月', 'period_label': '2026/9/1〜2026/9/30'}
        lab = period_labels('20260915', '20261012')
        assert lab['is_calendar_month'] is False
        assert lab['month_label'] == '9/15〜10/12'
        assert lab['period_label'] == '2026/9/15〜2026/10/12'

    def test_単一ファイルでもメタ情報を検証する(self):
        text = Path(OLD_CUR).read_text(encoding='utf-8')
        no_prop = '\n'.join(l for l in text.split('\n') if not l.startswith('# プロパティ'))
        errors = validate_csv_inputs(no_prop.encode('utf-8'))
        assert any('プロパティが読み取れません' in e for e in errors)
        swapped = text.replace('開始日: 20260701', '開始日: 20260815')
        errors = validate_csv_inputs(swapped.encode('utf-8'))
        assert any('より後になっています' in e for e in errors)

    def test_複数ファイルで開始日が終了日より後なら停止する(self):
        bad = [f.replace(b'20260915', b'20261020') for f in (_read(SNAP), _read(ACQ), _read(ENG))]
        with pytest.raises(CsvMergeError) as exc:
            merge_ga4_csvs(bad)
        assert '開始日・終了日が正しくない' in str(exc.value)

    def test_暦月でない期間は表紙と総括で期間を表示する(self, tmp_path):
        pytest.importorskip('reportlab')
        from report_logic_compare import generate_compare_report
        cur = merge_ga4_csvs([SNAP, ACQ, ENG, EVT])
        out = tmp_path / 'period.pdf'
        generate_compare_report(cur, cur, str(out), company_name='テスト医院H', single_mode=True)
        pages = _pdf_pages(out)
        assert '2026/9/15〜2026/10/12' in pages[0]
        assert '対象期間: 2026/9/15〜2026/10/12' in pages[-2]


# ─────────────────────────────────────────────
# 入力署名（P2-12）
# ─────────────────────────────────────────────
def test_入力が変われば署名が変わる():
    base = input_signature('a.csv', b'1', '|prev|', '医院A', '標準版')
    assert base == input_signature('a.csv', b'1', '|prev|', '医院A', '標準版')
    assert base != input_signature('a.csv', b'2', '|prev|', '医院A', '標準版')
    assert base != input_signature('a.csv', b'1', '|prev|', '医院B', '標準版')
    assert base != input_signature('a.csv', b'1', '|prev|', '医院A', '詳細版')


# ─────────────────────────────────────────────
# 片月だけ合計から外したイベント（v9 再レビュー P1-2）
# ─────────────────────────────────────────────
class TestTreatmentMismatch:
    @staticmethod
    def _k(patient, excluded=None, completion=None, status=KEY_EVENTS_INFERRED):
        return {'key_events_status': status, 'key_events': patient,
                'key_events_total': sum(patient.values()),
                'key_events_dedup_excluded': excluded or {},
                'key_events_completion': completion or {}}

    def test_片月だけ同数除外なら合計の前月比を出さない(self):
        from compare_utils import treatment_mismatch_events
        cur = self._k({'予約_A': 50}, excluded={'予約_B': 50})
        prev = self._k({'予約_A': 50, '予約_B': 50})
        assert treatment_mismatch_events(cur, prev) == [
            {'name': '予約_B', 'cur_included': False, 'prev_included': True}]
        d = compare_kpis(cur, prev)['key_events_total']
        assert d['comparable'] is False
        assert d['incomparable_reason'] == 'treatment_mismatch'
        assert d['current'] == 50 and d['previous'] == 100   # 値自体は保持
        assert format_delta(d) == '比較不能（合計の対象が月で異なる）'

    def test_完了到達の採否が月で違えば比較しない(self):
        cur = self._k({'予約_A': 40}, completion={'予約_A_完了': 5})
        prev = self._k({'予約_A_完了': 6})
        assert compare_kpis(cur, prev)['key_events_total']['incomparable_reason'] == 'treatment_mismatch'

    def test_両月とも同じ規則で除外なら従来どおり比較する(self):
        cur = self._k({'予約_A': 50}, excluded={'予約_B': 50})
        prev = self._k({'予約_A': 40}, excluded={'予約_B': 40})
        d = compare_kpis(cur, prev)['key_events_total']
        assert d['comparable'] is True and d['diff'] == 10

    def test_片方の月にしか無いイベントは食い違いとしない(self):
        cur = self._k({'予約_A': 50}, completion={'予約_A_完了': 5})
        prev = self._k({'予約_A': 40})
        assert compare_kpis(cur, prev)['key_events_total']['comparable'] is True

    def test_PDFは値を出し合計と小計の差だけ比較不能にする(self, tmp_path):
        pytest.importorskip('reportlab')
        from report_logic_compare import generate_compare_report
        prev_rows = [('予約_外部_A', 246, 169), ('予約_外部_B', 246, 150), ('電話_ボタン', 200, 120)]
        cur = merge_ga4_csvs(_with_events(DUP_ROWS))
        prev = merge_ga4_csvs(_with_events(prev_rows, prev=True))
        out = tmp_path / 'mismatch.pdf'
        generate_compare_report(cur, prev, str(out), company_name='テスト医院J', detailed=False)
        pages = _pdf_pages(out)
        p4 = pages[3]
        assert '合計 538 692 比較不能 比較不能' in p4
        assert '予約_外部_B 0 246 比較不能 比較不能' in p4     # -100% を作らない
        assert '予約・電話アクション 小計 538 692 比較不能 比較不能' in p4
        assert '「予約_外部_B」は当月は合計に含めず、前月は含めた' in p4.replace('\n', '')
        assert '538件 （前月692件 比較不能）' in pages[1]
        from test_multi_csv import _assert_no_footer_overlap
        _assert_no_footer_overlap(out, 'テスト医院J')


# ─────────────────────────────────────────────
# P4 の別表の高さ割り当て（v9 再レビュー P2-4）
# ─────────────────────────────────────────────
MANY_ROWS = [
    ('page_view', 9000, 3000),
    ('予約_外部_A', 246, 169), ('予約_外部_B', 246, 169),
    ('予約_web_X', 120, 80), ('予約_web_Y', 120, 80),
    ('相談_A', 60, 40), ('相談_B', 60, 40),
    ('問診_A', 33, 20), ('問診_B', 33, 20),
    ('電話_ボタン', 292, 148),
    ('予約_外部_A_完了', 26, 13), ('予約_外部_B_完了', 4, 2), ('予約_web_X_完了', 9, 5),
    ('予約_web_Y_完了', 8, 5), ('相談_A_完了', 3, 2),
    ('求人_応募', 30, 10), ('採用_ボタン', 12, 6),
    ('route_click', 21, 14),
]


class TestP4Layout:
    def test_行の割り当て(self):
        from report_logic_compare import allocate_side_rows, fold_items
        assert allocate_side_rows([5, 2, 1], 8) == [5, 2, 1]
        assert allocate_side_rows([5, 2, 1], 5) == [2, 2, 1]
        assert allocate_side_rows([5, 2, 1], 2) is None
        assert allocate_side_rows([], 0) == []
        shown, folded = fold_items(list(range(5)), 2)
        assert shown == [0] and folded == [1, 2, 3, 4]
        assert fold_items([1, 2], 5) == ([1, 2], [])

    def test_別表と除外行が多くてもフッターに落ちない(self, tmp_path):
        pytest.importorskip('reportlab')
        from report_logic_compare import generate_compare_report
        from test_multi_csv import _assert_no_footer_overlap
        cur = merge_ga4_csvs(_with_events(MANY_ROWS))
        prev = merge_ga4_csvs(_with_events(MANY_ROWS, prev=True))
        k = parse_ga4_csv(cur)['kpis']
        assert len(k['key_events_dedup_excluded']) == 4
        assert len(k['key_events_completion']) == 5
        assert len(k['key_events_recruit']) == 2
        assert len(k['key_events_unclassified']) == 1
        for detailed in (False, True):
            out = tmp_path / f'many_{detailed}.pdf'
            generate_compare_report(cur, prev, str(out), company_name='テスト医院K',
                                    detailed=detailed)
            _assert_no_footer_overlap(out, 'テスト医院K')
            p4 = _pdf_pages(out)[3]
            assert '他2件（合計に含めない）' in p4
            assert '完了到達 小計 50 50 0' in p4
            assert '求人関連 小計 42 42 0' in p4
            assert '未分類 小計 21 21 0' in p4

    def test_狭い場合も前月値を落とさない(self):
        from report_logic_compare import side_table_summary_text, RECRUIT_SIDE_TABLE
        t = dict(RECRUIT_SIDE_TABLE, items=[{'name': 'a', 'current': 3, 'previous': 5}])
        assert '9月 3件 / 8月 5件' in side_table_summary_text(t, '9月', '8月')
        t = dict(RECRUIT_SIDE_TABLE, items=[{'name': 'a', 'current': 3, 'previous': None,
                                             'prev_text': '未出力'}])
        assert '8月 未出力（比較不能）' in side_table_summary_text(t, '9月', '8月')


# ─────────────────────────────────────────────
# 空の表・日付の欠け・空のプロパティ（v9 再レビュー P2-5〜7）
# ─────────────────────────────────────────────
class TestInputGaps:
    def test_空のキーイベント表と行のある表が食い違えば停止する(self):
        text = Path(OLD_CUR).read_text(encoding='utf-8')
        with_rows = text.replace('イベント名,キーイベント\n\n', 'イベント名,キーイベント\n予約_A,50\n\n', 1)
        assert with_rows != text
        errors = validate_csv_inputs([text.encode('utf-8'), with_rows.encode('utf-8')],
                                     cur_names=['empty.csv', 'rows.csv'])
        joined = '\n'.join(errors)
        assert '値が一致しません' in joined and '見出しだけ（0件）' in joined
        # 順序を逆にしても停止する
        errors = validate_csv_inputs([with_rows.encode('utf-8'), text.encode('utf-8')])
        assert any('値が一致しません' in e for e in errors)

    def test_空の表どうしは一致として扱う(self):
        text = Path(OLD_CUR).read_text(encoding='utf-8')
        other = text.replace('# ----------------------------------------', '# ---------------------------------------- ', 1)
        assert validate_csv_inputs([text.encode('utf-8'), other.encode('utf-8')]) == []

    def _drop_day(self, blob: bytes, header: str, day_index: int) -> bytes:
        lines = blob.decode('utf-8').split('\n')
        i = next(k for k, l in enumerate(lines) if l.startswith(header))
        del lines[i + 1 + day_index]
        return '\n'.join(lines).encode('utf-8')

    def test_両方の日別表から同じ日が欠けても停止する(self):
        acq = self._drop_day(_read(ACQ), 'N 日目,アクティブ ユーザー', 5)
        eng = self._drop_day(_read(ENG), 'N 日目,アクティブ ユーザーあたりの平均エンゲージメント時間', 5)
        errors = validate_csv_inputs([_read(SNAP), acq, eng], cur_names=['s.csv', 'a.csv', 'e.csv'])
        joined = '\n'.join(errors)
        assert '「日別アクティブユーザー」（a.csv）の日付（N 日目）が期間' in joined
        assert '欠けている日: 0005' in joined
        assert '「平均エンゲージメント時間」（e.csv）' in joined

    def test_新規ユーザーの日別表の欠けも停止する(self):
        acq = self._drop_day(_read(ACQ), 'N 日目,新規ユーザー数', 0)
        errors = validate_csv_inputs([_read(SNAP), acq, _read(ENG)])
        assert any('「日別新規ユーザー」' in e and '欠けている日: 0000' in e for e in errors)

    def test_空のプロパティ名は次の行を拾わず停止する(self):
        text = Path(OLD_CUR).read_text(encoding='utf-8')
        lines = text.split('\n')
        k = next(i for i, l in enumerate(lines) if l.startswith('# プロパティ:'))
        lines[k] = '# プロパティ:'
        blank = '\n'.join(lines)
        data = parse_ga4_csv(blank.encode('utf-8'))
        assert data['meta']['property'] == ''
        assert data['meta']['property_line'] is True
        errors = validate_csv_inputs(blank.encode('utf-8'))
        assert any('プロパティ名が空' in e for e in errors)
        # 複数ファイルの結合でも停止する
        with pytest.raises(CsvMergeError) as exc:
            merge_ga4_csvs([blank.encode('utf-8'), Path(OLD_PREV).read_bytes()], names=['a.csv', 'b.csv'])
        assert 'a.csv（プロパティ名が空です）' in str(exc.value)


# ─────────────────────────────────────────────
# 同じ表で列数・行数が違う（実データ: スナップショットと概要の上位ページ）
# ─────────────────────────────────────────────
class TestPartialTables:
    @staticmethod
    def _df(rows, cols):
        import pandas as pd
        return pd.DataFrame(rows, columns=cols)

    def test_列数が違っても共通列が一致すれば整合(self):
        from csv_parser import tables_consistent
        a = self._df([['p1', '10', '5', '0.2'], ['p2', '7', '3', '0.5']],
                     ['ページ', '表示回数', 'アクティブ ユーザー', '直帰率'])
        b = self._df([['p1', '10'], ['p2', '7']], ['ページ', '表示回数'])
        assert tables_consistent(a, b) and tables_consistent(b, a)

    def test_行数が違っても共通行が一致すれば整合(self):
        from csv_parser import tables_consistent
        a = self._df([['p1', '10'], ['p2', '7'], ['p3', '2']], ['ページ', '表示回数'])
        b = self._df([['p1', '10'], ['p2', '7']], ['ページ', '表示回数'])
        assert tables_consistent(a, b) and tables_consistent(b, a)

    def test_共通行の値が1つでも違えば食い違い(self):
        from csv_parser import tables_consistent
        a = self._df([['p1', '10', '5'], ['p2', '7', '3']], ['ページ', '表示回数', 'アクティブ ユーザー'])
        b = self._df([['p1', '10'], ['p2', '8'], ['p3', '1']], ['ページ', '表示回数'])
        assert not tables_consistent(a, b)

    def test_共通の行が無ければ食い違い(self):
        from csv_parser import tables_consistent
        a = self._df([['p1', '10']], ['ページ', '表示回数'])
        b = self._df([['p9', '10']], ['ページ', '表示回数'])
        assert not tables_consistent(a, b)
        assert not tables_consistent(a, self._df([], ['ページ', '表示回数']))

    def test_同じキーの行は合算せず含まれるかで見る(self):
        from csv_parser import tables_consistent
        a = self._df([['p1', '10'], ['p1', '4']], ['ページ', '表示回数'])
        assert tables_consistent(a, self._df([['p1', '10']], ['ページ', '表示回数']))
        assert not tables_consistent(a, self._df([['p1', '14']], ['ページ', '表示回数']))

    def test_日別表は行がそろっていることも求める(self):
        from csv_parser import tables_consistent
        a = self._df([['0', '5'], ['1', '6']], ['N 日目', 'アクティブ ユーザー'])
        b = self._df([['0', '5']], ['N 日目', 'アクティブ ユーザー'])
        assert tables_consistent(a, b)
        assert not tables_consistent(a, b, strict_rows=True)

    def _truncated_eng(self, n_rows, change=None):
        lines = _read(ENG).decode('utf-8').split('\n')
        i = next(k for k, l in enumerate(lines) if l.startswith('ページ タイトルとスクリーン クラス,表示回数'))
        j = i + 1
        while j < len(lines) and lines[j].strip() and not lines[j].startswith('#'):
            j += 1
        body = lines[i + 1:i + 1 + n_rows]
        if change is not None:
            name, val = body[change].rstrip('\r').rsplit(',', 1)
            body[change] = f'{name},{int(val) + 1}\r'
        lines[i + 1:j] = body
        return '\n'.join(lines).encode('utf-8')

    def test_上位N件の打ち切りと列数の違いでは止めず情報量の多い表を使う(self):
        eng = self._truncated_eng(10)
        files = [_read(SNAP), _read(ACQ), eng, _read(EVT)]
        assert validate_csv_inputs(files) == []
        baseline = parse_ga4_csv(merge_ga4_csvs(files))
        assert len(baseline['df_pages']) == 82                   # 行の多いスナップショット側
        assert len(baseline['df_pages'].columns) == 5
        for perm in itertools.permutations(files):
            data = parse_ga4_csv(merge_ga4_csvs(list(perm)))
            assert data['issues'] == []
            assert data['df_pages'].equals(baseline['df_pages']), perm
            assert data['kpis'] == baseline['kpis']

    def test_共通行の表示回数が1つ違えばファイル名つきで止める(self):
        eng = self._truncated_eng(10, change=3)
        files = [_read(SNAP), _read(ACQ), eng, _read(EVT)]
        for perm in itertools.permutations(range(4)):
            names = ['s.csv', 'a.csv', 'e.csv', 'v.csv']
            errors = validate_csv_inputs([files[i] for i in perm], cur_names=[names[i] for i in perm])
            joined = '\n'.join(errors)
            assert '「上位ページ」の表が複数のファイルにあり、値が一致しません' in joined, perm
            assert 's.csv' in joined and 'e.csv' in joined

    def test_日別表の行の欠けは他ファイルに全日あっても止める(self):
        # スナップショット（旧形式）と集客サマリーの両方に日別アクティブユーザーがある場合
        text = Path(OLD_CUR).read_text(encoding='utf-8')
        lines = text.split('\n')
        i = next(k for k, l in enumerate(lines) if l.startswith('N 日目,アクティブ ユーザー'))
        short = lines[:i + 1] + lines[i + 2:]
        other = '\n'.join(short).replace('# ----------------------------------------',
                                          '# ---------------------------------------- ', 1)
        errors = validate_csv_inputs([text.encode('utf-8'), other.encode('utf-8')])
        assert any('「日別アクティブユーザー」の表が複数のファイルにあり' in e for e in errors)


# ─────────────────────────────────────────────
# 集計方法が違う月どうしでも各月の件数は出す（差分だけ比較不能）
# ─────────────────────────────────────────────
class TestMethodMismatchValues:
    @pytest.fixture(scope='class')
    def pages(self, tmp_path_factory):
        pytest.importorskip('reportlab')
        from report_logic_compare import generate_compare_report
        prev_text = Path(OLD_PREV).read_text(encoding='utf-8').replace(
            'イベント名,キーイベント\n\n',
            'イベント名,キーイベント\nweb予約,40\n成人電話,12\n求人_送信ボタン_,9\nroute_click,5\n\n', 1)
        out = tmp_path_factory.mktemp('mm') / 'method.pdf'
        generate_compare_report(merge_ga4_csvs([SNAP, ACQ, ENG, EVT]), prev_text.encode('utf-8'),
                                str(out), company_name='テスト医院L', detailed=True)
        from test_multi_csv import _assert_no_footer_overlap
        _assert_no_footer_overlap(out, 'テスト医院L')
        return _pdf_pages(out)

    def test_左表に両月の件数を出し増減は比較不能(self, pages):
        p4 = pages[3]
        assert 'WEB予約 136 40 比較不能 比較不能' in p4
        assert '電話（成人） 64 12 比較不能 比較不能' in p4
        # 前月は明細ありのため全キーイベントが対象（route_click も前月の合計に入る）
        assert '合計（求人関連を除く） 368 57 比較不能 比較不能' in p4
        flat = p4.replace('\n', '')
        assert '集計方法が異なるため増減は比較していません' in flat
        assert '（GA4明細）' in p4 and '（名前から判定）' in p4
        assert '対象範囲が異なります' in flat

    def test_求人別表は前月の値も出す(self, pages):
        p4 = pages[3]
        assert '求人_送信ボタン_ 61 9 比較不能' in p4
        assert '求人関連 小計 70 9 比較不能' in p4

    def test_P2のバナーは増減を出さない(self, pages):
        assert '当月 368件（イベント名から判定） / 前月 57件（集計方法が異なるため比較不能）' in pages[1]
