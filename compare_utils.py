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
    if diff_dict.get('comparable') is False:
        return '比較不能（未出力）'

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
        diff = calc_diff(cur_kpis.get(k, 0), prev_kpis.get(k, 0))
        if k == 'key_events_total':
            cur_status = cur_kpis.get('key_events_status', 'missing')
            prev_status = prev_kpis.get('key_events_status', 'missing')
            comparable = cur_status != 'missing' and prev_status != 'missing'
            diff.update({
                'comparable': comparable,
                'current_status': cur_status,
                'previous_status': prev_status,
            })
            if not comparable:
                diff['diff'] = 0.0
                diff['pct'] = None
                diff['direction'] = 'flat'
        result[k] = diff
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
                       compared_kevents: list,
                       cur_pages=None, prev_pages=None) -> dict:
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
        'key_events_total': 'キーイベント発生回数',
    }

    # KPIで最も改善/悪化した指標
    best = None
    worst = None
    for k, label in kpi_labels.items():
        d = compared_kpis.get(k, {})
        if d.get('comparable') is False:
            continue
        pct = d.get('pct')
        if pct is None:
            continue
        if best is None or pct > best[1]:
            best = (label, pct, d)
        if worst is None or pct < worst[1]:
            worst = (label, pct, d)

    # worst が実際には「増加」の場合、「注意が必要」ではなく「伸びが最も小さい指標」
    # として扱う（全KPIが増加している月に、増加した指標を注意指標と誤表示しないため）
    worst_is_decline = bool(worst and worst[2].get('diff', 0) < 0)
    # best が実際には「減少」の場合、「最も改善した指標」と呼ぶのは誤り。
    # 全KPIが下落した月に、減少している指標を改善として緑色表示しないための判定。
    best_is_gain = bool(best and best[2].get('diff', 0) > 0)
    best_is_flat = bool(best and best[2].get('diff', 0) == 0)

    highlights = {
        'best_kpi': {
            'label': best[0] if best else '-',
            'delta': format_delta(best[2]) if best else '-',
            'is_gain': best_is_gain,
            'is_flat': best_is_flat,
        },
        'worst_kpi': {
            'label': worst[0] if worst else '-',
            'delta': format_delta(worst[2]) if worst else '-',
            'is_decline': worst_is_decline,
        },
    }

    # 当月0PVになったページ（前月まで閲覧されていたページの消失）
    # 上位20表と同じ基準で拾い、総括の確認事項に使う。
    lost = []
    if cur_pages is not None or prev_pages is not None:
        sel = select_top_pages(cur_pages, prev_pages, 20)
        if not sel.empty and 'status' in sel.columns:
            lost = (sel[sel['status'] == 'lost']
                    .sort_values('prev', ascending=False)
                    .to_dict('records'))
    highlights['lost_pages'] = lost

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
        if d.get('comparable') is False:
            continue
        if d.get('direction') != 'flat':
            bullets.append(f"{label}: {format_delta(d)}")
    highlights['summary_bullets'] = bullets

    # ─────────────────────────────────────────────────
    # 総括（P10）の生成
    #
    # 設計方針: このCSVから観測できないことを原因として断定しない。
    #   旧実装は Organic の増減から「検索順位に変動があった」、Direct の増減から
    #   「メルマガやLINE等のCRM施策」、CV減から「フォームの入力項目数」を推測し、
    #   広告費データが無いまま「広告予算を増額」を推奨していた。いずれもこのCSVには
    #   根拠となるデータが存在しない。
    #   本実装では出力を「観測事実」「次月の確認事項（根拠つき）」に限定し、
    #   原因が特定できないものは断定せず「どこで確認すべきか」を示す。
    # ─────────────────────────────────────────────────
    comments = []
    facts_up = []       # 増加した指標（観測事実のみ）
    facts_down = []     # 減少した指標（観測事実のみ）
    headline_lines = []
    ses = compared_kpis.get('sessions', {})
    kev = compared_kpis.get('key_events_total', {})
    eng = compared_kpis.get('avg_engage_sec', {})
    new_u = compared_kpis.get('new_users', {})

    # セクション未出力と、出力済み0件を区別する。
    # 未出力を0件として比較すると、存在しない-100%減少を作ってしまう。
    cv_missing = kev.get('comparable') is False
    cv_zero = (
        not cv_missing
        and int(kev.get('current', 0)) == 0
        and int(kev.get('previous', 0)) == 0
    )

    def _pct(d):
        return abs(d.get('pct', 0) or 0)

    # ---- 観測事実（数値の増減のみ。原因は書かない） ----
    if ses.get('pct') is not None and ses.get('diff', 0) != 0:
        if ses.get('diff', 0) > 0:
            headline_lines.append(f"アクセス（セッション）は前月より {_pct(ses):.1f}% 増加")
            facts_up.append(f"セッション数が前月より {_pct(ses):.1f}% 増えました。")
        else:
            headline_lines.append(f"アクセス（セッション）は前月より {_pct(ses):.1f}% 減少")
            facts_down.append(f"セッション数が前月より {_pct(ses):.1f}% 減りました。")

    if new_u.get('pct') is not None and new_u.get('diff', 0) != 0:
        if new_u.get('diff', 0) > 0:
            facts_up.append(f"新規ユーザーが前月より {_pct(new_u):.1f}% 増えました。")
        else:
            facts_down.append(f"新規ユーザーが前月より {_pct(new_u):.1f}% 減りました。")

    if not cv_missing and not cv_zero and kev.get('pct') is not None and kev.get('diff', 0) != 0:
        if kev.get('diff', 0) > 0:
            headline_lines.append(f"キーイベント発生回数は前月比 {_pct(kev):.1f}% 増加")
            facts_up.append(f"キーイベント発生回数が前月比 {_pct(kev):.1f}% 増えました。")
        else:
            headline_lines.append(f"キーイベント発生回数は前月比 {_pct(kev):.1f}% 減少")
            facts_down.append(f"キーイベント発生回数が前月比 {_pct(kev):.1f}% 減りました。")

    if eng.get('pct') is not None and eng.get('diff', 0) != 0:
        if eng.get('diff', 0) > 0:
            facts_up.append(f"平均エンゲージメント時間が前月比 {_pct(eng):.1f}% 伸びました。")
        else:
            facts_down.append(f"平均エンゲージメント時間が前月比 {_pct(eng):.1f}% 短くなりました。")

    if cv_missing:
        headline_lines.append('キーイベントデータが未出力のため、前月比較はできません')
    elif cv_zero:
        headline_lines.append('キーイベントは両月とも0件のため、成果の増減は判断できません')

    # ---- 次月の確認事項（根拠つき／原因は断定しない） ----
    # (priority, text) 。priority が大きいほど優先。
    scored_actions = []

    if cv_missing:
        scored_actions.append((210,
            'GA4のレポートにキーイベント明細を追加して再出力する。CSVに明細が無いため、0件なのか未出力なのかを判定できず、前月比較は行っていない。'
            '（根拠: キーイベントセクション未出力）'))
    elif cv_zero:
        scored_actions.append((200,
            'GA4で予約ボタン・電話タップ・問い合わせ送信をキーイベントに設定し、成果を計測できる状態にする。'
            'これが無い限り、集客施策も広告も効果を評価できない。'
            '（根拠: 当月・前月ともキーイベント発生回数0件）'))

    # チャネルの変動: GA4のこのCSVでは「なぜ変わったか」は分からない。確認先を示す。
    top_ch = highlights.get('top_channel_change', {})
    ch_name = top_ch.get('name')
    if ch_name and ch_name != '-':
        ch_impact = 0.0
        try:
            key_col = compared_channels.columns[0]
            row = compared_channels.loc[compared_channels[key_col] == ch_name]
            if not row.empty:
                ch_impact = abs(calc_diff(row['cur_val'].values[0],
                                          row['prev_val'].values[0]).get('pct', 0) or 0)
        except (KeyError, IndexError, AttributeError):
            ch_impact = 0.0
        ch_delta = top_ch.get('delta', '')
        ch_basis = f'（根拠: {ch_name} セッション {ch_delta}）'
        name_l = str(ch_name)

        if 'Organic' in name_l and 'Social' not in name_l:
            scored_actions.append((80 + ch_impact,
                f'自然検索の変動が最も大きい。GA4には検索語・掲載順位が含まれないため、'
                f'Search Consoleで表示回数・クリック数・平均掲載順位・クエリの変化を確認する。{ch_basis}'))
        elif 'Paid' in name_l or 'Cross-network' in name_l:
            scored_actions.append((85 + ch_impact,
                f'広告流入の変動が最も大きい。GA4には広告費が含まれないため、'
                f'広告管理画面で費用・CPA・コンバージョンを確認したうえで配信量の判断を行う。{ch_basis}'))
        elif 'Direct' in name_l:
            scored_actions.append((60 + ch_impact,
                f'Direct（参照元不明の流入）の変動が最も大きい。GA4では要因を特定できないため、'
                f'チラシ・看板・紹介・院内掲示など、オフライン接点の実施状況を院内で確認する。{ch_basis}'))
        else:
            scored_actions.append((60 + ch_impact,
                f'「{ch_name}」の変動が最も大きい。GA4の「参照元/メディア」で内訳を確認し、'
                f'どのサイトからの流入が動いたかを特定する。{ch_basis}'))
        comments.append(
            f'流入元では「{ch_name}」の増減が最も大きく {ch_delta} でした。'
            'この数字は流入量の変化を示すもので、原因はGA4のこのデータからは特定できません。'
        )

    # セッションとキーイベントの組み合わせ（両月で発生回数を比較できる場合のみ）
    if not cv_missing and not cv_zero:
        ses_up = ses.get('diff', 0) > 0
        ses_down = ses.get('diff', 0) < 0
        kev_up = kev.get('diff', 0) > 0
        kev_down = kev.get('diff', 0) < 0
        if ses_up and kev_down:
            scored_actions.append((150 + _pct(kev),
                'アクセスは増えたがキーイベントは減少。流入が増えたページと、キーイベントが'
                '発生しているページを突き合わせ、導線が途切れている箇所を特定する。'
                f'（根拠: セッション +{_pct(ses):.1f}% / キーイベント -{_pct(kev):.1f}%）'))
        elif ses_down and kev_down:
            scored_actions.append((160 + _pct(ses) + _pct(kev),
                'アクセス・キーイベントとも減少。まず減少した流入チャネルを特定し、'
                '自然検索ならSearch Console、広告なら広告管理画面で個別に要因を確認する。'
                f'（根拠: セッション -{_pct(ses):.1f}% / キーイベント -{_pct(kev):.1f}%）'))
        elif ses_down and kev_up:
            scored_actions.append((120 + _pct(ses),
                'アクセスは減ったがキーイベントは増加。成果につながっている流入元を特定し、'
                'その流入量を戻せるかを確認する。'
                f'（根拠: セッション -{_pct(ses):.1f}% / キーイベント +{_pct(kev):.1f}%）'))
        elif ses_up and kev_up:
            scored_actions.append((70,
                'アクセス・キーイベントとも増加。どの流入元とページが伸びたかを特定し、'
                '再現できる要因かを確認する。'
                f'（根拠: セッション +{_pct(ses):.1f}% / キーイベント +{_pct(kev):.1f}%）'))
    elif ses.get('diff', 0) != 0:
        direction = '増加' if ses.get('diff', 0) > 0 else '減少'
        scored_actions.append((90 + _pct(ses),
            f'アクセスが{direction}した要因を、チャネル別・ページ別の増減から切り分ける。'
            f'（根拠: セッション {format_delta(ses)}）'))

    # 上位ページの消失（select_top_pages と同じ観点。ページデータがある場合のみ）
    lost_pages = highlights.get('lost_pages') or []
    if lost_pages:
        top_lost = lost_pages[0]
        scored_actions.append((140,
            f'前月まで閲覧されていた「{top_lost["page"]}」が当月0件になっている。'
            'ページの削除・URL変更・タイトル変更のいずれかが起きた可能性があるため、'
            f'サイト側の変更履歴を確認する。（根拠: 前月 {top_lost["prev"]:,}PV → 当月 0PV）'))

    # エンゲージメント時間（原因は書かず、確認対象を示す）
    if eng.get('diff', 0) < 0 and _pct(eng) >= 5:
        scored_actions.append((50 + _pct(eng),
            '平均エンゲージメント時間が低下。上位ページのどれで滞在が短くなったかは'
            'このCSVでは分からないため、GA4のページ別レポートで確認する。'
            f'（根拠: 平均エンゲージメント時間 {format_delta(eng)}）'))

    # 優先度順に上位2件
    scored_actions.sort(key=lambda x: x[0], reverse=True)
    action_items = [text for _, text in scored_actions[:2]]
    if not action_items:
        action_items = ['主要指標に大きな変動はなし。次月も同じ指標で推移を確認する。']

    comments.append(
        '本レポートはGA4のスナップショットCSVのみを入力としています。'
        '検索順位・広告費・フォームの離脱箇所は含まれないため、原因の特定には'
        'Search Console・広告管理画面・サイト側の変更履歴の確認が必要です。'
    )

    if not headline_lines:
        headline_lines = ['主要指標は前月と大きな差がなく、横ばいで推移']
    # 「大きな悪化は見られず基盤は維持」という定型文は、全指標が下落した月にも
    # 出てしまい実態と矛盾していたため廃止。増加が無ければ「なし」と明示する。
    good_points = facts_up if facts_up else ['増加した主要指標はありませんでした。']
    improve_points = facts_down if facts_down else ['減少した主要指標はありませんでした。']

    highlights['headline_lines'] = headline_lines[:3]
    highlights['good_points'] = good_points[:3]
    highlights['improve_points'] = improve_points[:3]
    highlights['action_items'] = action_items[:2]
    highlights['executive_comments'] = comments[:8]

    return highlights


def select_top_pages(cur_pages, prev_pages, top_n: int = 20) -> pd.DataFrame:
    """上位ページの選定。当月PVと前月PVの「大きい方」で順位付けする。

    従来は当月PVの降順で上位N件を選んでいたため、当月0PVになったページ
    （例: 前月218PV → 当月0PV）が表から構造的に消え、消失を検知できなかった。
    max(当月, 前月) を基準にすると、当月伸びたページと前月まで主力だったページの
    双方が対称に選ばれ、行数もN件のまま増えない。

    戻り値: columns = ['page', 'cur', 'prev', 'diff', 'status']
        status は 'new'（前月0→当月あり）/ 'lost'（前月あり→当月0）/ ''。
    """
    def _to_map(df):
        if df is None or df.empty:
            return {}
        col_p, col_v = df.columns[0], df.columns[1]
        out = {}
        for _, r in df.iterrows():
            key = str(r[col_p])
            try:
                val = int(r[col_v])
            except (TypeError, ValueError):
                continue
            # 同名タイトルが複数行ある場合は合算（GA4はページタイトル単位のため起こり得る）
            out[key] = out.get(key, 0) + val
        return out

    cur_map = _to_map(cur_pages)
    prev_map = _to_map(prev_pages)
    if not cur_map and not prev_map:
        return pd.DataFrame(columns=['page', 'cur', 'prev', 'diff', 'status'])

    rows = []
    for page in set(cur_map) | set(prev_map):
        cur_v = cur_map.get(page, 0)
        prev_v = prev_map.get(page, 0)
        if cur_v == 0 and prev_v == 0:
            continue
        status = ''
        if prev_v == 0 and cur_v > 0:
            status = 'new'
        elif cur_v == 0 and prev_v > 0:
            status = 'lost'
        rows.append({
            'page': page,
            'cur': cur_v,
            'prev': prev_v,
            'diff': cur_v - prev_v,
            'status': status,
            '_rank_key': max(cur_v, prev_v),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=['page', 'cur', 'prev', 'diff', 'status'])
    # 同順位は当月PVの大きい方を上に（当月の実態を優先して読ませる）
    df = df.sort_values(['_rank_key', 'cur'], ascending=[False, False])
    df = df.head(top_n).drop(columns=['_rank_key']).reset_index(drop=True)
    return df
