#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""複数ファイル入力（2026-10 以降のGA4出力形式）のテスト用フィクスチャを作る。

GA4「レポートのスナップショット」の構成変更で、日別アクティブユーザー等が
スナップショットに入らなくなったため、同じ期間の次の4ファイルを1セットとして使う。

  レポートのスナップショット.csv / 集客サマリー.csv /
  エンゲージメントの概要.csv / イベント_イベント名.csv

このリポジトリはPublicのため、医院を特定できる文字列を除く:
- アカウント名・プロパティ名 → 固定のダミー名
- ページタイトル → サンプルページNN（全ファイル共通の写像。ファイル間で同じページは同じ名前）
- 市区町村 → サンプル市区町村NNN
数値は回帰検知のためそのまま残す。

スナップショットは他3本と期間が異なる状態で取得されていたため、--snapshot-period で
開始日・終了日だけを他3本に揃える（数値はそのまま。テスト用の複製であることに注意）。

使い方:
  python tests/_make_multi_fixtures.py SRC_DIR --forbid 医院名,地名,...
  （--forbid の文字列が出力に残っていたら失敗する。医院名はスクリプトに書かない）
"""
import argparse
import csv
import io
import os
import re
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'multi')

FILES = [
    ('レポートのスナップショット.csv', 'snapshot.csv'),
    ('集客サマリー.csv', 'acquisition.csv'),
    ('エンゲージメントの概要.csv', 'engagement.csv'),
    ('イベント_イベント名.csv', 'events.csv'),
]

PAGE_HEADER = 'ページ タイトルとスクリーン クラス,'
CITY_HEADER = '市区町村,'


def _rewrite_first_col(line: str, mapping: dict, prefix: str, width: int) -> str:
    row = next(csv.reader(io.StringIO(line)))
    key = row[0]
    if key not in mapping:
        mapping[key] = f'{prefix}{len(mapping) + 1:0{width}d}'
    return ','.join([mapping[key]] + row[1:])


def anonymize(text: str, page_map: dict, city_map: dict) -> str:
    out = []
    mode = None
    for raw in text.split('\n'):
        cr = '\r' if raw.endswith('\r') else ''
        line = raw.rstrip('\r')
        if line.startswith('# アカウント:'):
            out.append('# アカウント: サンプル歯科' + cr)
            continue
        if line.startswith('# プロパティ:'):
            out.append('# プロパティ: サンプル歯科C - GA4' + cr)
            continue
        if line.startswith(PAGE_HEADER):
            mode = 'page'
            out.append(raw)
            continue
        if line.startswith(CITY_HEADER):
            mode = 'city'
            out.append(raw)
            continue
        if not line.strip() or line.startswith('#'):
            mode = None
            out.append(raw)
            continue
        if mode == 'page':
            out.append(_rewrite_first_col(line, page_map, 'サンプルページ', 2) + cr)
        elif mode == 'city':
            out.append(_rewrite_first_col(line, city_map, 'サンプル市区町村', 3) + cr)
        else:
            out.append(raw)
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('--forbid', default='', help='出力に残ってはいけない文字列（カンマ区切り）')
    ap.add_argument('--snapshot-period', default='',
                    help='スナップショットの期間置換 例: 20260901-20260930:20260915-20261012')
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    page_map, city_map = {}, {}
    forbid = [w for w in args.forbid.split(',') if w]
    failed = False
    for src_name, dst_name in FILES:
        with open(os.path.join(args.src, src_name), encoding='utf-8-sig', newline='') as f:
            text = f.read()
        if dst_name == 'snapshot.csv' and args.snapshot_period:
            old, new = args.snapshot_period.split(':')
            (os_, oe), (ns, ne) = old.split('-'), new.split('-')
            text = re.sub(rf'(開始日:\s*){os_}', rf'\g<1>{ns}', text)
            text = re.sub(rf'(終了日:\s*){oe}', rf'\g<1>{ne}', text)
        text = anonymize(text, page_map, city_map)
        left = sorted({w for w in forbid if w in text})
        if left:
            print(f'  !! {dst_name} に残存: {left}', file=sys.stderr)
            failed = True
        with open(os.path.join(OUT, dst_name), 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        print(f'  {src_name} -> multi/{dst_name}')
    print(f'pages={len(page_map)} cities={len(city_map)}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
