#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同じ月・同じプロパティの GA4 エクスポートCSV（複数ファイル）を1セットに結合する。

背景（2026-10）:
GA4「レポートのスナップショット」の構成が変わり、日別アクティブユーザー・日別新規ユーザー・
平均エンゲージメント時間・30日/7日/1日アクティブユーザーがCSVに入らなくなった。
不足分は「集客サマリー」「エンゲージメントの概要」から出力できるため、
これらを1セットとして受け取り、従来の単一CSVと同じ形に連結して csv_parser に渡す。

結合ルール:
- 文字コードは csv_parser._decode_text で判定する（UTF-8 / UTF-8 BOM / Shift_JIS）。
- 「# レポートのスナップショット」のファイルを先頭に置く（source_report・プロパティ・期間の
  メタ情報は先頭ファイル基準で読まれるため）。無ければ入力順。
- 同じセット内でプロパティ名・開始日・終了日が異なる場合は結合せずエラーにする。
  別の月や別の医院のCSVを混ぜたまま数値を作らないため。
"""

import os
import re

from csv_parser import _decode_text

SNAPSHOT_TITLE = 'レポートのスナップショット'


class CsvMergeError(ValueError):
    """同一セットとして結合できないCSVの組み合わせ。"""


def report_title(text: str) -> str:
    """CSV冒頭の「# ----」直後の行からGA4の出力元レポート名を返す（無ければ空文字）。"""
    lines = text.splitlines()
    for i, line in enumerate(lines[:12]):
        if re.match(r'^#\s*-{5,}\s*$', line.strip()) and i + 1 < len(lines):
            m = re.match(r'^#\s*(.+?)\s*$', lines[i + 1].strip())
            if m and not re.match(r'^(アカウント|プロパティ)\s*:', m.group(1)) \
                    and not re.match(r'^-{5,}$', m.group(1)):
                return m.group(1)
            return ''
    return ''


def _file_meta(text: str) -> dict:
    """csv_parser.parse_ga4_csv と同じ規則（最初の出現）でプロパティと期間を読む。"""
    prop = re.search(r'プロパティ:\s*(.+)', text)
    start_d = re.search(r'開始日:\s*(\d{8})', text)
    end_d = re.search(r'終了日:\s*(\d{8})', text)
    return {
        'title': report_title(text),
        'property': prop.group(1).strip() if prop else '',
        'start': start_d.group(1) if start_d else '',
        'end': end_d.group(1) if end_d else '',
    }


def _fmt_period(start: str, end: str) -> str:
    def f(d):
        return f'{d[:4]}/{d[4:6]}/{d[6:]}' if len(d) == 8 else (d or '不明')
    return f'{f(start)}〜{f(end)}'


def _default_name(source, index: int) -> str:
    if isinstance(source, str):
        return os.path.basename(source)
    name = getattr(source, 'name', None)
    if isinstance(name, str) and name:
        return os.path.basename(name)
    return f'ファイル{index + 1}'


def _odd_ones(entries: list, key) -> list:
    """key の値ごとにまとめ、最大グループ（同数なら先頭ファイルを含む方）以外を返す。"""
    groups = {}
    for e in entries:
        groups.setdefault(key(e), []).append(e)
    if len(groups) <= 1:
        return []
    first_key = key(entries[0])
    majority = max(groups, key=lambda k: (len(groups[k]), k == first_key))
    return [e for e in entries if key(e) != majority]


def merge_ga4_csvs(files, names=None) -> bytes:
    """GA4エクスポートCSVを1セットとして結合し、UTF-8のbytesを返す。

    files: bytes / ファイルパス / file-like のリスト（1件以上）
    names: エラー表示用のファイル名リスト（省略時はパスのファイル名または「ファイルN」）

    1ファイルだけの場合は入力をそのまま返す（従来の単一CSVと完全に同じ挙動）。
    """
    files = list(files or [])
    if not files:
        raise CsvMergeError('CSVファイルが指定されていません。')
    names = list(names) if names is not None else [
        _default_name(f, i) for i, f in enumerate(files)
    ]
    if len(names) != len(files):
        raise ValueError('names の件数が files と一致しません。')

    if len(files) == 1:
        only = files[0]
        if isinstance(only, (bytes, bytearray)):
            return bytes(only)
        return _decode_text(only).encode('utf-8')

    entries = []
    for i, (src, name) in enumerate(zip(files, names)):
        text = _decode_text(src)
        meta = _file_meta(text)
        entries.append({'index': i, 'name': name, 'text': text, **meta})

    unreadable = [e for e in entries if not (e['property'] and e['start'] and e['end'])]
    if unreadable:
        detail = '、'.join(
            f"{e['name']}（"
            + '・'.join(
                label for label, ok in (
                    ('プロパティ', e['property']), ('開始日', e['start']), ('終了日', e['end']),
                ) if not ok
            )
            + 'が読み取れません）'
            for e in unreadable
        )
        raise CsvMergeError(
            f'同じセットとして確認できないCSVがあります: {detail}。'
            'GA4のレポート画面からエクスポートしたCSVか確認してください。'
        )

    # 先頭にスナップショットを置く（複数あれば入力順で最初のもの）。他は入力順。
    snapshots = [e for e in entries if e['title'] == SNAPSHOT_TITLE]
    if snapshots:
        head = snapshots[0]
        ordered = [head] + [e for e in entries if e is not head]
    else:
        ordered = entries

    prop_odd = _odd_ones(ordered, lambda e: e['property'])
    if prop_odd:
        others = [e for e in ordered if e not in prop_odd]
        raise CsvMergeError(
            'プロパティが異なるCSVが混ざっています。'
            f"異なるファイル: {'、'.join(_desc(e, 'property') for e in prop_odd)} / "
            f"他のファイル: {'、'.join(_desc(e, 'property') for e in others)}。"
            '同じ医院（同じプロパティ）から出力したCSVだけを指定してください。'
        )

    period_odd = _odd_ones(ordered, lambda e: (e['start'], e['end']))
    if period_odd:
        others = [e for e in ordered if e not in period_odd]
        raise CsvMergeError(
            '期間が異なるCSVが混ざっています。'
            f"異なるファイル: {'、'.join(_desc(e, 'period') for e in period_odd)} / "
            f"他のファイル: {'、'.join(_desc(e, 'period') for e in others)}。"
            'GA4で同じ開始日・終了日を指定して出力し直してください。'
        )

    # 各ファイルの最終セクションが次ファイルのヘッダと連続しないよう、空行を挟んで連結する。
    merged = '\n\n'.join(e['text'].rstrip('\r\n') for e in ordered) + '\n'
    return merged.encode('utf-8')


def _desc(entry: dict, kind: str) -> str:
    if kind == 'property':
        return f"{entry['name']}（{entry['property']}）"
    return f"{entry['name']}（{_fmt_period(entry['start'], entry['end'])}）"
