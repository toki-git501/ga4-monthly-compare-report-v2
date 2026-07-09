#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
比較計算ユーティリティ — 当月 vs 前月の差分計算・フォーマット
"""

import pandas as pd
import numpy as np


def calc_diff(current, previous) -> dict:
    """増減計算。current/previous は数値。

    Returns:
        {
            'current': float,
            'previous': float,
            'diff': float,        # current - previous
            'pct': float | None,  # 変化率(%) previous=0ならNone
            'direction': 'up' | 'down' | 'flat',
        }
    """
    cur = float(current) if current is not None else 0.0
    prev = float(previous) if previous is not None else 0.0
    diff = cur - prev

    if prev != 0:
        pct = (diff / abs(prev)) * 100
    else:
        pct = None  # 前月0の場合は割合算出不可

    if diff > 0:
        direction = 'up'
    elif diff < 0:
        direction = 'down'
    else:
        direction = 'flat'

    return {
        'current': cur,
        'previous': prev,
        'diff': diff,
        'pct': pct,
        'direction': direction,
    }


def format_delta(diff_dict: dict) -> str:
    """"+1,234 (+12.3%) ▲" 形式の文字列を返す"""
    diff = diff_dict['diff']
    pct = diff_dict['pct']
    direction = diff_dict['direction']

    if direction == 'flat':
        return "±0 (0.0%) ─"

    sign = '+' if diff >= 0 else ''
    arrow = '▲' if direction == 'up' else '▼'

    if pct is not None:
        return f"{sign}{diff:,.0f} ({sign}{pct:.1f}%) {arrow}"
    else:
        return f"{sign}{diff:,.0f} (N/A) {arrow}"


def delta_color(direction: str) -> str:
    """方向に応じた色コードを返す"""
    if direction == 'up':
        return '#2E8B57'   # 増加: 緑
    elif direction == 'down':
        return '#D9534F'   # 減少: 赤
    else:
        return '#8A9BB0'   # flat: グレー


def compare_kpis(cur_kpis: dict, prev_kpis: dict) -> dict:
    """4指標一括比較。

    Returns:
        {
            'active_users': diff_dict,
            'new_users': diff_dict,
            'sessions': diff_dict,
            'avg_engage_sec': diff_dict,
            'key_events_total': diff_dict,
        }
    """
    keys = ['active_users', 'new_users', 'sessions', 'avg_engage_sec', 'key_events_total']
    result = {}
    for k in keys:
        result[k] = calc_diff(cur_kpis.get(k, 0), prev_kpis.get(k, 0))
    return result


def compare_dataframes(cur_df, prev_df, key_col: str, val_col: str) -> pd.DataFrame:
    """2つのDataFrameを外部結合し、差分列を追加。

    Returns:
        DataFrame with columns: [key_col, 'cur_val', 'prev_val', 'diff', 'pct']
    """
    if cur_df is None and prev_df is None:
        return pd.DataFrame()

    def prep(df, suffix):
        if df is None:
            return pd.DataFrame(columns=[key_col, f'{suffix}_val'])
        d = df[[key_col, val_col]].copy()
        d.columns = [key_col, f'{suffix}_val']
        return d

    cur = prep(cur_df, 'cur')
    prev = prep(prev_df, 'prev')

    merged = pd.merge(cur, prev, on=key_col, how='outer').fillna(0)
    merged['diff'] = merged['cur_val'] - merged['prev_val']
    merged['pct'] = merged.apply(
        lambda r: (r['diff'] / abs(r['prev_val']) * 100) if r['prev_val'] != 0 else None,
        axis=1
    )
    merged = merged.sort_values('cur_val', ascending=False).reset_index(drop=True)
    return merged


def compare_key_events(cur_kev: dict, prev_kev: dict) -> list:
    """キーイベントを比較してリスト形式で返す。

    Returns:
        [{'name': str, 'current': int, 'previous': int, 'diff_dict': dict}, ...]
    """
    all_keys = set(list(cur_kev.keys()) + list(prev_kev.keys()))
    result = []
    for k in sorted(all_keys):
        cur_val = cur_kev.get(k, 0)
        prev_val = prev_kev.get(k, 0)
        result.append({
            'name': k,
            'current': cur_val,
            'previous': prev_val,
            'diff_dict': calc_diff(cur_val, prev_val),
        })
    result.sort(key=lambda x: x['current'], reverse=True)
    return result


def compute_highlights(compared_kpis: dict, compared_channels: pd.DataFrame,
                       compared_kevents: list) -> dict:
    """P7総括ページ用のハイライト情報を抽出。

    Returns:
        {
            'best_kpi': {'label': str, 'delta': str},
            'worst_kpi': {'label': str, 'delta': str},
            'top_channel_change': {'name': str, 'delta': str},
            'top_kevent_change': {'name': str, 'delta': str},
            'summary_bullets': [str, ...],
        }
    """
    kpi_labels = {
        'active_users': 'アクティブユーザー',
        'new_users': '新規ユーザー',
        'sessions': 'セッション数',
        'key_events_total': 'キーイベント合計',
    }

    # KPIで最も改善/悪化した指標
    best = None
    worst = None
    for k, label in kpi_labels.items():
        d = compared_kpis.get(k, {})
        pct = d.get('pct')
        if pct is None:
            continue
        if best is None or pct > best[1]:
            best = (label, pct, d)
        if worst is None or pct < worst[1]:
            worst = (label, pct, d)

    highlights = {
        'best_kpi': {
            'label': best[0] if best else '-',
            'delta': format_delta(best[2]) if best else '-',
        },
        'worst_kpi': {
            'label': worst[0] if worst else '-',
            'delta': format_delta(worst[2]) if worst else '-',
        },
    }

    # チャネルで最も変動が大きいもの
    if not compared_channels.empty and 'diff' in compared_channels.columns:
        key_col = compared_channels.columns[0]
        idx_max = compared_channels['diff'].abs().idxmax()
        ch_row = compared_channels.loc[idx_max]
        ch_diff = calc_diff(ch_row['cur_val'], ch_row['prev_val'])
        highlights['top_channel_change'] = {
            'name': ch_row[key_col],
            'delta': format_delta(ch_diff),
        }
    else:
        highlights['top_channel_change'] = {'name': '-', 'delta': '-'}

    # キーイベントで最も変動が大きいもの
    if compared_kevents:
        top_kev = max(compared_kevents, key=lambda x: abs(x['diff_dict']['diff']))
        highlights['top_kevent_change'] = {
            'name': top_kev['name'],
            'delta': format_delta(top_kev['diff_dict']),
        }
    else:
        highlights['top_kevent_change'] = {'name': '-', 'delta': '-'}

    # サマリーバレット
    bullets = []
    for k, label in kpi_labels.items():
        d = compared_kpis.get(k, {})
        if d.get('direction') != 'flat':
            bullets.append(f"{label}: {format_delta(d)}")
    highlights['summary_bullets'] = bullets

    # 総括コメント（P7用） - なるべく平易な文体
    comments = []
    good_points = []
    improve_points = []
    action_items = []
    headline_lines = []
    ses = compared_kpis.get('sessions', {})
    kev = compared_kpis.get('key_events_total', {})
    eng = compared_kpis.get('avg_engage_sec', {})
    new_u = compared_kpis.get('new_users', {})

    if ses.get('pct') is not None:
        if ses.get('diff', 0) > 0:
            headline_lines.append(f"アクセスは前月より {ses.get('pct', 0):.1f}% 増加")
            good_points.append(f"アクセス数が前月より {ses.get('pct', 0):.1f}% 増えています。")
            comments.append(
                f"サイト全体のアクセスは前月より {ses.get('pct', 0):.1f}% 増えました。伸びている流入元の施策は維持し、同じ訴求を他ページにも横展開するのが有効です。"
            )
        elif ses.get('diff', 0) < 0:
            headline_lines.append(f"アクセスは前月より {abs(ses.get('pct', 0)):.1f}% 減少")
            improve_points.append(f"アクセス数が前月より {abs(ses.get('pct', 0)):.1f}% 減っています。")
            comments.append(
                f"サイト全体のアクセスは前月より {abs(ses.get('pct', 0)):.1f}% 減っています。まずは減少した流入元を特定し、広告配信面と検索流入面を分けて対策することを推奨します。"
            )

    if new_u.get('pct') is not None:
        if new_u.get('diff', 0) > 0:
            good_points.append(f"新しく来たユーザーが前月比 {new_u.get('pct', 0):.1f}% 増えました。")
            comments.append(
                f"初めて来たユーザーは前月より {new_u.get('pct', 0):.1f}% 増加しました。入口ページの訴求を維持し、予約導線まで一貫したメッセージにそろえると成果につながりやすくなります。"
            )
        elif new_u.get('diff', 0) < 0:
            improve_points.append(f"新しく来たユーザーが前月比 {abs(new_u.get('pct', 0)):.1f}% 減りました。")
            comments.append(
                f"初めて来たユーザーは前月より {abs(new_u.get('pct', 0)):.1f}% 減少しました。新規獲得用の広告文や検索流入の入口ページを優先して見直すのが効果的です。"
            )

    if kev.get('pct') is not None:
        if kev.get('diff', 0) > 0:
            headline_lines.append(f"予約・電話の成果は前月比 {kev.get('pct', 0):.1f}% 増加")
            good_points.append(f"予約や電話などの成果数が前月比 {kev.get('pct', 0):.1f}% 増えています。")
            comments.append(
                f"予約や電話などの成果アクションは前月比 {kev.get('pct', 0):.1f}% 増えています。成果が出た導線は残しつつ、同じ構成を他の上位ページへ展開するのがおすすめです。"
            )
        elif kev.get('diff', 0) < 0:
            headline_lines.append(f"予約・電話の成果は前月比 {abs(kev.get('pct', 0)):.1f}% 減少")
            improve_points.append(f"予約や電話などの成果数が前月比 {abs(kev.get('pct', 0)):.1f}% 減っています。")
            comments.append(
                f"予約や電話などの成果アクションは前月比 {abs(kev.get('pct', 0)):.1f}% 減っています。予約ボタンの位置・文言・電話導線の見やすさを優先して改善したい状況です。"
            )

    if eng.get('pct') is not None:
        if eng.get('diff', 0) > 0:
            good_points.append(f"ユーザーのページ滞在は前月比 {eng.get('pct', 0):.1f}% 改善しています。")
            comments.append(
                f"1ユーザーあたりの利用時間は前月より {eng.get('pct', 0):.1f}% 伸びています。読まれているページの構成をテンプレート化すると全体改善につながります。"
            )
        elif eng.get('diff', 0) < 0:
            improve_points.append(f"ユーザーのページ滞在は前月比 {abs(eng.get('pct', 0)):.1f}% 低下しています。")
            comments.append(
                f"1ユーザーあたりの利用時間は前月より {abs(eng.get('pct', 0)):.1f}% 短くなりました。離脱しやすい上位ページから先に改善対象として着手するのが効率的です。"
            )

    # --- 優先施策ハイライト（優先度スコア付き、上位2件を選定） ---
    # (priority, action_text) のリスト。priorityが大きいほど優先
    scored_actions = []

    ses_up = ses.get('diff', 0) > 0
    ses_down = ses.get('diff', 0) < 0
    kev_up = kev.get('diff', 0) > 0
    kev_down = kev.get('diff', 0) < 0
    eng_up = eng.get('diff', 0) > 0
    eng_down = eng.get('diff', 0) < 0
    new_up = new_u.get('diff', 0) > 0
    new_down = new_u.get('diff', 0) < 0

    ses_pct = abs(ses.get('pct', 0) or 0)
    kev_pct = abs(kev.get('pct', 0) or 0)
    eng_pct = abs(eng.get('pct', 0) or 0)
    new_pct = abs(new_u.get('pct', 0) or 0)

    # ---- セッション × CV の組み合わせ（最も影響が大きい） ----
    if ses_up and kev_up:
        scored_actions.append((50,
            '好調な流入元の広告予算を維持または増額し、同じ訴求を他のページにも横展開して成果拡大を狙う。'))
        scored_actions.append((45,
            '成果が出ているページの構成（見出し・CTA配置・安心材料の順序）をテンプレート化し、他ページにも適用する。'))
    elif ses_up and kev_down:
        # CVが下がっている → CVR改善が最優先
        scored_actions.append((100 + kev_pct,
            'アクセスは増えているのにCVが減少 → 予約ボタンの位置・文言・フォーム入力ステップを優先的に見直す。'))
        scored_actions.append((90 + kev_pct,
            '流入が増えたページのファーストビューに「予約の流れ」や「初診の方へ」など安心材料を追加してCV率を改善する。'))
    elif ses_down and kev_up:
        # アクセス減 → 集客回復が最優先
        scored_actions.append((100 + ses_pct,
            'CV率は改善傾向 → アクセス回復のため、検索上位ページのタイトル・メタディスクリプションを見直し流入を増やす。'))
        scored_actions.append((85 + ses_pct,
            '広告経由の集客を強化。成果が出ているCV導線はそのまま維持し、入口のアクセス数を増やすことで成果総数を伸ばす。'))
    elif ses_down and kev_down:
        # 両方減少 → 最も緊急度が高い
        scored_actions.append((120 + ses_pct + kev_pct,
            'アクセス・CV共に減少 → まず減少した流入チャネルを特定し、広告配信面と検索流入面を分けて原因を調査する。'))
        scored_actions.append((110 + ses_pct + kev_pct,
            '上位ページの離脱率が高い可能性。ファーストビューの見出しをユーザーの検索意図に合わせ、主要ボタンを1つに絞る。'))
    else:
        scored_actions.append((30,
            '主要指標は横ばい → 現状維持しつつ、上位ページで小規模なA/Bテスト（見出し変更・CTA色変更等）を実施して改善の余地を探る。'))

    # ---- エンゲージメント系 ----
    if eng_down:
        scored_actions.append((70 + eng_pct,
            'エンゲージ時間低下 → 上位ページの冒頭に要点を簡潔にまとめ、スクロールを促す目次やアンカーリンクを追加する。'))
    elif eng_up and kev_down:
        scored_actions.append((80 + eng_pct,
            '滞在時間は伸びているがCVに至っていない → ページ内にCTAボタンを複数箇所（冒頭・中盤・末尾）に配置して導線を強化する。'))

    # ---- 新規ユーザー系 ----
    if new_down and ses_up:
        scored_actions.append((65 + new_pct,
            'リピーター中心の構成 → 新規向けのランディングページを整備し、初回訪問者向けの訴求（実績・口コミ・アクセス方法）を強化する。'))
    elif new_up and kev_down:
        scored_actions.append((75 + new_pct,
            '新規ユーザーは増えているが成果未達 → 初回訪問者向けにわかりやすい予約フローと初診案内を目立つ位置に配置する。'))

    # ---- チャネル系（変動幅に応じた優先度） ----
    top_ch = highlights.get('top_channel_change', {})
    if top_ch.get('name') and top_ch.get('name') != '-':
        ch_name = top_ch.get('name')
        ch_diff_d = calc_diff(
            compared_channels.loc[compared_channels[compared_channels.columns[0]] == ch_name, 'cur_val'].values[0],
            compared_channels.loc[compared_channels[compared_channels.columns[0]] == ch_name, 'prev_val'].values[0],
        ) if not compared_channels.empty and ch_name in compared_channels[compared_channels.columns[0]].values else {}
        ch_impact = abs(ch_diff_d.get('pct', 0) or 0)

        if 'Organic' in str(ch_name):
            scored_actions.append((55 + ch_impact,
                f'「{ch_name}」の変動が大きい → 検索順位に変動があった可能性。上位表示ページのコンテンツ鮮度を確認し、最新情報に更新する。'))
        elif 'Paid' in str(ch_name):
            scored_actions.append((60 + ch_impact,
                f'「{ch_name}」の変動が大きい → 広告のクリック率・品質スコアを確認し、広告文とLP内容の一貫性を見直す。'))
        elif 'Direct' in str(ch_name):
            scored_actions.append((40 + ch_impact,
                f'「{ch_name}」の変動が大きい → ブックマーク・リピート訪問に変化あり。メルマガやLINE等のCRM施策の効果を確認する。'))
        else:
            scored_actions.append((50 + ch_impact,
                f'「{ch_name}」向けの入口ページを先に改善。ファーストビューの見出しを検索意図に合わせ、主要ボタンを1つに絞る。'))
        comments.append(
            f"流入元では「{ch_name}」の変動が最も大きく、今月の結果に強く影響した可能性があります。"
        )

    # ---- キーイベント系 ----
    top_kev = highlights.get('top_kevent_change', {})
    if top_kev.get('name') and top_kev.get('name') != '-':
        kev_name = top_kev.get('name')
        kev_impact = abs(top_kev.get('delta', '') and kev_pct)

        if '電話' in str(kev_name).lower() or 'tel' in str(kev_name).lower():
            scored_actions.append((60 + kev_impact,
                f'「{kev_name}」の変動が目立つ → 電話ボタンの視認性（色・サイズ・スクロール追従）を確認し、営業時間の明記を徹底する。'))
        elif '予約' in str(kev_name).lower():
            scored_actions.append((65 + kev_impact,
                f'「{kev_name}」の変動が目立つ → 予約フォームの入力項目数を見直し、必須項目を最小限にしてCV率を改善する。'))
        else:
            scored_actions.append((55 + kev_impact,
                f'「{kev_name}」につながる導線を強化。上位3ページのCTAボタン文言を統一し、スクロール追従ボタンの追加を検討する。'))
        comments.append(
            f"成果アクションでは「{kev_name}」の変化が目立ちました。次月はこの導線の継続改善が有効です。"
        )

    # 優先度順にソートし、上位2件を選定
    scored_actions.sort(key=lambda x: x[0], reverse=True)
    action_items = [text for _, text in scored_actions[:2]]
    if not action_items:
        action_items = ['上位ページの見出しと導線を見直し、反応率の改善を優先。']

    comments.append(
        "次月は、アクセスが伸びた流入元を維持しつつ、成果につながりやすいページを優先して改善する進め方が現実的です。"
    )

    if not headline_lines:
        headline_lines = ['主要指標は前月と大きな差がなく、横ばいで推移']
    if not good_points:
        good_points = ['大きな悪化は見られず、基盤は維持できています。']
    if not improve_points:
        improve_points = ['次月は成果につながるページの改善余地を優先確認。']

    highlights['headline_lines'] = headline_lines[:3]
    highlights['good_points'] = good_points[:3]
    highlights['improve_points'] = improve_points[:3]
    highlights['action_items'] = action_items[:2]
    highlights['executive_comments'] = comments[:8]

    return highlights
