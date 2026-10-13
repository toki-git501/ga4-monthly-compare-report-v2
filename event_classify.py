#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""イベント名の分類（v9〜）。すべて純粋関数。

予約・電話アクションの合計（key_events_total）に何を入れ、何を入れないかを決める。
当月・前月とも同じ関数で分類するため、前月比の整合が保たれる。

分類の順序（先に当たったものが優先）:
  1. システム系（tap / click / page_view 等の自動計測）  → どこにも入れない
  2. 求人関連（求人・採用・recruit・マイナビ等）          → key_events_recruit
  3. 完了到達（末尾トークンが 完了 / complete / thanks 等）
       - 末尾を除いた基底名のイベントが同月にある       → key_events_completion（合計に入れない）
       - 基底名のイベントが無い                           → 唯一の計測のため合計に入れる（注記）
  4. イベント名から判定（event_name_inferred）の場合のみ:
       予約・電話等の語を含む → 患者アクション / 含まない → 未分類（合計に入れない）
     GA4のキーイベント明細（available）の場合は、残りをすべて患者アクションとする
  5. 同数イベントの二重計測の疑い（find_same_count_groups）
       → 代表1件だけを合計に入れ、残りは key_events_dedup_excluded に移す

照合には NFKC 正規化＋casefold した名前を使う（全角英字・半角カナの表記ゆれ対策）。
表示・集計のキーは元の名前（既知の表示名への置換を除く）のまま。
"""

import re
import unicodedata

# 同数イベントを「同じ操作を2つのイベントで数えている疑い」として合計から1つ分に
# まとめるか。False にすると従来どおりすべて合計に入れる（注記も出さない）。
DEDUPE_SAME_COUNT_EVENTS = True
# 同数判定の対象とする最小イベント数・最小総ユーザー数（少数どうしの偶然の一致を拾わないため）
DEDUPE_MIN_COUNT = 20
DEDUPE_MIN_USERS = 10
# 共通の先頭文字として扱う最小文字数
DEDUPE_PREFIX_MIN_CHARS = 3

# GA4の自動収集・システム系イベント（患者の成果行動ではないため集計から除外）
SYSTEM_EVENTS = {
    'tap', 'click', 'scroll', 'page_view', 'session_start', 'first_visit',
    'user_engagement', 'view_search_results', 'file_download', 'download',
    'video_start', 'video_progress', 'video_complete',
    'form_start', 'view_promotion', 'select_promotion',
}

# 求人関連（照合は正規化後の部分一致）
RECRUIT_PATTERNS = ('求人', '採用', 'recruit', 'saiyo', 'まいなび', 'マイナビ', 'mynavi')

# 完了到達とみなす末尾トークン（区切り `_` `-` 空白の直後の末尾のみ。途中の一致は見ない）
COMPLETION_TAIL_TOKENS = frozenset({
    '完了', 'complete', 'completed', 'completion', 'thanks', 'thankyou', 'done', '到達',
})
COMPLETION_TAIL_PAIRS = frozenset({('thank', 'you')})

# イベント名から判定する場合の、予約・電話等の患者アクションとみなす語
PATIENT_JA_KEYWORDS = (
    '予約', '電話', '問い合わせ', '問合せ', '問合わせ', '相談', '問診', '来院',
    '申込', '申し込み', 'カウンセリング',
)
# 英字は単語（英数字の連続）単位で一致を見る（timeline の line、hotel の tel を拾わない）
PATIENT_EN_KEYWORDS = frozenset({
    'tel', 'phone', 'telephone', 'call', 'contact', 'reserve', 'reservation',
    'booking', 'inquiry', 'line',
})
PHONE_JA_KEYWORDS = ('電話',)
PHONE_EN_KEYWORDS = frozenset({'tel', 'phone', 'telephone', 'call'})

# 既知イベントの表示名（照合は正規化名。未知のイベントは元の名称のまま表示する）
DISPLAY_NAME_MAP = {
    'web予約':                          'WEB予約',
    'web予約_sp小児ページ':              'WEB予約（小児SP）',
    '小児web予約_sp固定タブ_':            'WEB予約（小児タブ）',
    '成人電話':                          '電話（成人）',
    '小児電話':                          '電話（小児）',
    '小児電話_小児ページからのアクセス_':    '電話（小児ページ）',
    'スマホ_tel':                        '電話（スマホ）',
    '問診票_成人':                       '問診票（成人）',
    '問診票ダウンロード_成人_':            '問診票DL（成人）',
    '問診票_小児':                       '問診票（小児）',
}

_SEP_RE = re.compile(r'[_\-\s]+')


def normalize_event_name(name) -> str:
    """照合用の正規化名（NFKC＋casefold・前後空白除去）。表示には使わない。"""
    return unicodedata.normalize('NFKC', str(name)).strip().casefold()


def _tokens(norm: str) -> list:
    """区切り（_ - 空白）で分けたトークン。"""
    return [t for t in _SEP_RE.split(norm) if t]


def _ascii_words(name) -> list:
    """英数字の単語（camelCase も分ける）。"""
    s = unicodedata.normalize('NFKC', str(name))
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', s)
    return re.findall(r'[a-z0-9]+', s.casefold())


def is_system_event(name) -> bool:
    return normalize_event_name(name) in SYSTEM_EVENTS


def is_recruit_event(name) -> bool:
    """求人関連イベント（求人応募ボタン・求人サイトへのリンク等）か。"""
    norm = normalize_event_name(name)
    return any(p.casefold() in norm for p in RECRUIT_PATTERNS)


def completion_base_name(name):
    """完了到達のイベント名なら、末尾トークンを除いた基底名（正規化名）を返す。違えば None。

    末尾トークンは区切り（_ - 空白）の直後のものだけを見る。
    `incomplete`・`予約完了`（区切りなし）は完了到達として扱わない。
    """
    norm = normalize_event_name(name)
    toks = _tokens(norm)
    if len(toks) >= 2 and toks[-1] in COMPLETION_TAIL_TOKENS:
        k = 1
    elif len(toks) >= 3 and tuple(toks[-2:]) in COMPLETION_TAIL_PAIRS:
        k = 2
    else:
        return None
    base = re.sub(r'(?:[_\-\s]+[^_\-\s]+){%d}[_\-\s]*$' % k, '', norm)
    base = base.rstrip('_- ')
    return base or None


def is_completion_event(name) -> bool:
    return completion_base_name(name) is not None


def is_patient_action_name(name) -> bool:
    """イベント名に予約・電話等の語を含むか（イベント名から判定する場合に使う）。"""
    norm = normalize_event_name(name)
    if any(k in norm for k in PATIENT_JA_KEYWORDS):
        return True
    return any(w in PATIENT_EN_KEYWORDS for w in _ascii_words(name))


def is_phone_event(name) -> bool:
    norm = normalize_event_name(name)
    if any(k in norm for k in PHONE_JA_KEYWORDS):
        return True
    return any(w in PHONE_EN_KEYWORDS for w in _ascii_words(name))


def is_reservation_or_phone(name) -> bool:
    """「予約・電話アクション 小計」に入れるイベントか（予約・電話・tel 等）。"""
    norm = normalize_event_name(name)
    if '予約' in norm or '電話' in norm:
        return True
    return any(w in PHONE_EN_KEYWORDS | {'reserve', 'reservation', 'booking'}
               for w in _ascii_words(name))


def _share_prefix(a: str, b: str) -> bool:
    """共通の先頭（区切りで分けた先頭1トークン以上、または先頭3文字以上）を持つか。"""
    na, nb = normalize_event_name(a), normalize_event_name(b)
    ta, tb = _tokens(na), _tokens(nb)
    if ta and tb and ta[0] == tb[0]:
        return True
    n = DEDUPE_PREFIX_MIN_CHARS
    return len(na) >= n and len(nb) >= n and na[:n] == nb[:n]


def find_same_count_groups(counts: dict, users: dict | None = None,
                           min_count: int | None = None,
                           event_counts: dict | None = None,
                           require_event_counts: bool = False,
                           min_users: int | None = None) -> tuple:
    """同じ月の中で、同じ操作を2つのイベントで数えている疑いのある組を探す。

    条件（すべて満たす組）:
      - 異なるイベント名で、件数が完全に一致し、各 DEDUPE_MIN_COUNT（20）件以上
      - 共通の先頭（先頭1トークン以上、または先頭3文字以上）を持つ
      - 電話のイベントと電話以外のイベントの組ではない
      - 総ユーザー数が両方とも取得でき、完全に一致し、各 DEDUPE_MIN_USERS（10）人以上
      - require_event_counts=True（GA4のキーイベント明細の月）の場合は、「イベント数」も
        両方とも取得でき、完全に一致する（キーイベント数だけの一致では除外しない）

    counts:       {イベント名: 件数}（候補になる患者アクションのみを渡す）
    users:        {イベント名: 総ユーザー数 or None}
    event_counts: {イベント名: イベント数 or None}（「イベント」レポート等の値）

    戻り値: (groups, unverified)
      groups:     [{'kept': 代表名, 'excluded': [名前...], 'count': n, 'users': u}, ...]
                  代表は名前の昇順で最初のもの（件数が同じため）
      unverified: [{'names': [...], 'count': n}, ...]
                  件数は一致するが、総ユーザー数（または明細の月のイベント数）が取得できず
                  確かめられない組（除外せず注記のみ）
    """
    if min_count is None:
        min_count = DEDUPE_MIN_COUNT
    if min_users is None:
        min_users = DEDUPE_MIN_USERS
    users = users or {}
    event_counts = event_counts or {}
    names = sorted(n for n, c in (counts or {}).items() if int(c) >= min_count)
    by_count = {}
    for n in names:
        by_count.setdefault(int(counts[n]), []).append(n)

    groups, unverified = [], []
    for count in sorted(by_count, reverse=True):
        bucket = by_count[count]
        if len(bucket) < 2:
            continue
        # 共通の先頭を持つ組を連結成分にまとめる
        parent = {n: n for n in bucket}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                if is_phone_event(a) != is_phone_event(b):
                    continue
                if _share_prefix(a, b):
                    parent[find(a)] = find(b)
        comps = {}
        for n in bucket:
            comps.setdefault(find(n), []).append(n)
        for comp in comps.values():
            if len(comp) < 2:
                continue
            comp = sorted(comp)
            keyed, unknown = {}, []
            for n in comp:
                u = users.get(n)
                ev = event_counts.get(n)
                if u is None or (require_event_counts and ev is None):
                    unknown.append(n)
                    continue
                if int(u) < min_users:
                    continue   # 人数が少なすぎる一致は判定に使わない
                keyed.setdefault((int(u), ev if require_event_counts else None), []).append(n)
            for (u, _), members in sorted(keyed.items(), key=lambda x: (x[0][0], str(x[0][1]))):
                if len(members) >= 2:
                    members = sorted(members)
                    groups.append({'kept': members[0], 'excluded': members[1:],
                                   'count': count, 'users': u})
            if unknown:
                unverified.append({'names': comp, 'count': count})
    return groups, unverified


def _sorted_counts(d: dict) -> dict:
    return dict(sorted(d.items(), key=lambda x: (-x[1], str(x[0]))))


def classify_event_counts(counts: dict, mode: str = 'available', users: dict | None = None,
                          dedupe: bool | None = None, event_counts: dict | None = None) -> dict:
    """イベント名ごとの件数を分類する（純粋関数）。

    counts: {元のイベント名: 件数}（同名行は aggregate_event_rows で集約済みのもの）
    mode:   'available'（GA4のキーイベント明細）/ 'inferred'（イベント名から判定）
    users:  {元のイベント名: 総ユーザー数 or None}（同数の二重計測の確認に使う）
    dedupe: 同数イベントの除外を行うか（None なら DEDUPE_SAME_COUNT_EVENTS）
    event_counts: {元のイベント名: イベント数 or None}。GA4のキーイベント明細（available）の月は、
            キーイベント数に加えてイベント数も一致する場合だけ同数として除外する

    戻り値（キーはすべて表示名）:
      patient            合計に入れる予約・電話等 {名前: 件数}
      recruit            求人関連
      completion         完了到達（基底イベントあり・合計に入れない）
      completion_base    {完了到達の名前: 対応する基底イベントの名前}
      completion_counted 基底イベントが無いため合計に入れた完了到達の名前リスト
      unclassified       未分類（イベント名から判定の場合のみ・合計に入れない）
      dedup_excluded     同数のため合計から除外した {名前: 件数}
      dedup_groups       [{'kept', 'excluded', 'count', 'users'}]
      dedup_unverified   [{'names', 'count'}]（総ユーザー数が無く確認できない組）
    """
    if dedupe is None:
        dedupe = DEDUPE_SAME_COUNT_EVENTS
    users = users or {}

    valid = {}
    for name, count in (counts or {}).items():
        raw = str(name).strip()
        try:
            n = int(count)
        except (TypeError, ValueError):
            continue
        if n <= 0 or not raw:
            continue
        valid[raw] = valid.get(raw, 0) + n

    # 表示名（既知の別名のみ置換。置換後に別のイベントと重なる場合は元の名前のまま）
    display, used = {}, {}
    for raw in sorted(valid):
        d = DISPLAY_NAME_MAP.get(normalize_event_name(raw), raw)
        if d in used and used[d] != raw:
            d = raw
        used[d] = raw
        display[raw] = d

    norm_to_raw = {}
    for raw in sorted(valid):
        norm_to_raw.setdefault(normalize_event_name(raw), raw)

    patient, recruit, completion, unclassified = {}, {}, {}, {}
    completion_base, completion_counted = {}, []
    click_candidates = {}
    for raw in sorted(valid):
        n = valid[raw]
        if is_system_event(raw):
            continue
        if is_recruit_event(raw):
            recruit[raw] = n
            continue
        base = completion_base_name(raw)
        if base is not None:
            base_raw = norm_to_raw.get(base)
            if base_raw is not None and base_raw != raw:
                completion[raw] = n
                completion_base[raw] = base_raw
            elif mode != 'available' and not is_patient_action_name(raw):
                # イベント名から判定する月は、基底の無い完了到達も予約・電話等の語を
                # 含む場合だけ合計に入れる（route_done 等の未知の完了は未分類）
                unclassified[raw] = n
            else:
                # 対応するクリック等のイベントが無い → 唯一の計測なので合計に入れる
                patient[raw] = n
                completion_counted.append(raw)
            continue
        if mode != 'available' and not is_patient_action_name(raw):
            unclassified[raw] = n
            continue
        patient[raw] = n
        click_candidates[raw] = n

    groups, unverified = ([], [])
    if dedupe:
        groups, unverified = find_same_count_groups(
            click_candidates, {k: users.get(k) for k in click_candidates},
            event_counts={k: (event_counts or {}).get(k) for k in click_candidates},
            require_event_counts=(mode == 'available'))
    dedup_excluded = {}
    for g in groups:
        for ex in g['excluded']:
            dedup_excluded[ex] = patient.pop(ex)

    def disp(d):
        return _sorted_counts({display[k]: v for k, v in d.items()})

    return {
        'patient': disp(patient),
        'recruit': disp(recruit),
        'completion': disp(completion),
        'completion_base': {display[k]: display[v] for k, v in completion_base.items()},
        'completion_counted': [display[k] for k in completion_counted],
        'unclassified': disp(unclassified),
        'dedup_excluded': disp(dedup_excluded),
        'dedup_groups': [
            {'kept': display[g['kept']], 'excluded': [display[e] for e in g['excluded']],
             'count': g['count'], 'users': g['users']}
            for g in groups
        ],
        'dedup_unverified': [
            {'names': [display[n] for n in u['names']], 'count': u['count']}
            for u in unverified
        ],
    }


def aggregate_event_rows(rows: list) -> tuple:
    """イベント表の行（文字列のタプル）を名前ごとに集約する。

    rows: [(イベント名, 件数, 総ユーザー数 or None, 行全体のタプル), ...]
          件数・総ユーザー数は数値（検証済み）。
    - 完全に同一の行の重複（二重貼付）は1行として扱い、合算しない
    - 同名で内容が異なる行は件数を合算する（GA4の分割行）
    - 総ユーザー数は行間で重複がありうるため合算しない。同名が複数行なら None

    戻り値: (counts, users)
    """
    seen = set()
    counts, users, n_rows = {}, {}, {}
    for name, count, user, whole in rows:
        if whole in seen:
            continue
        seen.add(whole)
        counts[name] = counts.get(name, 0) + int(count)
        n_rows[name] = n_rows.get(name, 0) + 1
        users[name] = user if n_rows[name] == 1 else None
    return counts, users
