"""GA4 CSV input validation for the Streamlit app."""

from csv_parser import parse_ga4_csv


REQUIRED_REPORT_SECTIONS = (
    ("df_active", "日別アクティブユーザー"),
    ("df_new", "日別新規ユーザー"),
    ("df_engage", "平均エンゲージメント時間"),
    ("df_ch_new", "チャネル別新規ユーザー"),
    ("df_ch_ses", "チャネル別セッション"),
    ("df_pages", "上位ページ"),
    ("df_trend", "30日・7日・1日アクティブユーザー"),
)


def validate_report_data(data: dict, label: str) -> list[str]:
    """Return blocking errors when a parsed CSV cannot produce a valid report."""
    errors = []
    source_report = data.get("meta", {}).get("source_report", "")

    if source_report == "集客サマリー":
        errors.append(
            f"{label}はGA4の「集客サマリー」から出力されています。"
            "「レポートのスナップショット」からCSVを再出力してください。"
        )

    missing = [
        display_name
        for key, display_name in REQUIRED_REPORT_SECTIONS
        if data.get(key) is None or data[key].empty
    ]
    if missing:
        errors.append(
            f"{label}にレポート生成に必要な項目がありません: {', '.join(missing)}。"
            "不足項目を0件として扱うと誤った比較になるため、PDFは生成できません。"
        )

    return errors


def validate_csv_inputs(cur_input, prev_input=None) -> list[str]:
    """Parse and validate the current CSV and the optional comparison CSV."""
    errors = []
    parsed = {}

    for label, source in (("当月CSV", cur_input), ("比較月CSV", prev_input)):
        if source is None:
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
