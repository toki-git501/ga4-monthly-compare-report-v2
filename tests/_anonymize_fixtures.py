#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""テスト用フィクスチャの匿名化。

このリポジトリはPublicのため、クライアントを特定できる文字列
（医院名・ページタイトル）をフィクスチャに含めない。
数値は回帰検知の価値があるためそのまま残す（数値だけでは医院を特定できない）。

使い方: 実CSVを tests/fixtures/ に置いてから本スクリプトを実行し、
上書きされた匿名版のみをコミットする。
"""
import os
import re
import sys

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')

# アカウント名・プロパティ名
NAME_MAP = {
    'クララスデンタル長久手': 'サンプル歯科A',
    'クララスデンタル本山': 'サンプル歯科B',
    'クララスデンタル': 'サンプル歯科',
}

# ページタイトルは1件ずつ順番に置き換える（同一タイトルは同一のダミーに写す）
PAGE_SECTION = 'ページ タイトルとスクリーン クラス,表示回数'


def anonymize(path, page_map):
    """page_map は全ファイルで共有する。

    ファイルごとに独立した写像を作ると、当月と前月で同じページが別名になり、
    ページの新規/消失判定が壊れる（実際にテストが落ちて発覚した）。
    """
    with open(path, encoding='utf-8') as f:
        text = f.read()

    # ページタイトル行を汎用名に置換（順序と値は保持）
    out_lines = []
    in_pages = False
    for line in text.split('\n'):
        stripped = line.rstrip('\r')
        if stripped.startswith(PAGE_SECTION):
            in_pages = True
            out_lines.append(line)
            continue
        if in_pages:
            if stripped == '' or stripped.startswith('#'):
                in_pages = False
                out_lines.append(line)
                continue
            if ',' in stripped:
                title, val = stripped.rsplit(',', 1)
                if title not in page_map:
                    page_map[title] = f'サンプルページ{len(page_map) + 1:02d}'
                cr = '\r' if line.endswith('\r') else ''
                out_lines.append(f'{page_map[title]},{val}{cr}')
                continue
        out_lines.append(line)
    text = '\n'.join(out_lines)

    for src, dst in NAME_MAP.items():
        text = text.replace(src, dst)

    # 念のため、残存する固有名詞を検出
    leftovers = re.findall(r'クララス|長久手|本山|藤が丘|千種区|近藤|佐々木', text)
    if leftovers:
        print(f'  !! 匿名化しきれない文字列: {sorted(set(leftovers))}', file=sys.stderr)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f'  匿名化: {os.path.basename(path)}（累計ページ {len(page_map)} 件）')


if __name__ == '__main__':
    shared_page_map = {}
    for name in sorted(os.listdir(FIX)):
        if name.endswith('.csv'):
            anonymize(os.path.join(FIX, name), shared_page_map)
    print('done')
