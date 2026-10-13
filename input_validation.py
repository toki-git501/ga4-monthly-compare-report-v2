"""GA4 CSV input validation for the Streamlit app."""

from csv_merge import CsvMergeError, merge_ga4_csvs
from csv_parser import parse_ga4_csv


# GA4の画面名（不足項目ごとに「どこから出力すれば揃うか」を示す）
SCREEN_ACQUISITION = '「レポート > 集客 > 集客サマリー」'
SCREEN_ENGAGEMENT = '「レポート > エンゲージメント > 概要」'
SCREEN_SNAPSHOT = '「レポートのスナップショット」'

REQUIRED_REPORT_SECTIONS = (
    ("df_active", "日別アクティブユーザー", SCREEN_ACQUISITION),
    ("df_new", "日別新規ユーザー", SCREEN_ACQUISITION),
    ("df_engage", "平均エンゲージメント時間", SCREEN_ENGAGEMENT),
    ("df_ch_new", "チャネル別新規ユーザー", f"{SCREEN_SNAPSHOT}または{SCREEN_ACQUISITION}"),
    ("df_ch_ses", "チャネル別セッション", f"{SCREEN_SNAPSHOT}または{SCREEN_ACQUISITION}"),
    ("df_pages", "上位ページ", f"{SCREEN_SNAPSHOT}または{SCREEN_ENGAGEMENT}"),
    ("df_trend", "30日・7日・1日アクティブユーザー", SCREEN_ENGAGEMENT),
)


def missing_report_sections(data: dict) -> list[tuple[str, str]]:
    """不足している必須項目を (項目名, 出力元のGA4画面) のリストで返す。"""
    return [
        (display_name, screen)
        for key, display_name, screen in REQUIRED_REPORT_SECTIONS
        if data.get(key) is None or data[key].empty
    ]


def validate_report_data(data: dict, label: str) -> list[str]:
    """Return blocking errors when a parsed CSV cannot produce a valid report."""
    errors = []
    source_report = data.get("meta", {}).get("source_report", "")
    missing = missing_report_sections(data)

    # 集客サマリーは他のCSV（エンゲージメントの概要など）と組み合わせれば必要項目が揃う。
    # 結合後も不足がある場合だけ、出力元を案内する。
    if missing and source_report == "集客サマリー":
        errors.append(
            f"{label}はGA4の「集客サマリー」から出力されたCSVで、レポートに必要な項目が揃っていません。"
            "下記の不足項目を出力できる画面のCSVを、同じ期間で追加してアップロードしてください。"
        )

    if missing:
        detail = "、".join(f"{name}（出力元: {screen}）" for name, screen in missing)
        errors.append(
            f"{label}にレポート生成に必要な項目がありません: {detail}。"
            "不足項目を0件として扱うと誤った比較になるため、PDFは生成できません。"
        )

    return errors


def merge_inputs(source, label: str, names=None):
    """複数ファイル指定なら1セットに結合して (bytes, errors) を返す。単一ならそのまま返す。"""
    if isinstance(source, (list, tuple)):
        try:
            return merge_ga4_csvs(source, names=names), []
        except CsvMergeError as exc:
            return None, [f"{label}: {exc}"]
    return source, []


def validate_csv_inputs(cur_input, prev_input=None, cur_names=None, prev_names=None) -> list[str]:
    """Parse and validate the current CSV and the optional comparison CSV.

    cur_input / prev_input: bytes・パス、または同じ月のCSV（複数ファイル）のリスト。
    """
    errors = []
    parsed = {}

    for label, source, names in (
        ("当月CSV", cur_input, cur_names),
        ("比較月CSV", prev_input, prev_names),
    ):
        if source is None:
            continue
        source, merge_errors = merge_inputs(source, label, names)
        if merge_errors:
            errors.extend(merge_errors)
            continue
        try:
            data = parse_ga4_csv(source)
        except Exception as exc:
            errors.append(f"{label}の解析に失敗しました: {exc}")
            continue
        parsed[label] = data
        errors.extend(validate_report_data(data, label))

    cur_data = parsed.get("当月CSV")
    prev_data = parsed.get("比較月CSV")
    if cur_data is not None and prev_data is not None:
        cur_prop = cur_data["meta"].get("property", "")
        prev_prop = prev_data["meta"].get("property", "")
        if cur_prop and prev_prop and cur_prop != prev_prop:
            errors.append(
                f"プロパティが一致しません。当月: {cur_prop} / 比較月: {prev_prop}"
            )

        cur_end = cur_data["meta"].get("end", "")
        prev_end = prev_data["meta"].get("end", "")
        if cur_end and prev_end and cur_end <= prev_end:
            errors.append(
                f"当月の終了日({cur_end})が比較月の終了日({prev_end})以前です。"
                "アップロード先を確認してください。"
            )

    return errors
