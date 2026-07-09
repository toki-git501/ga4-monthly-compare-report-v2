import streamlit as st
import os
import tempfile
import base64
import traceback
from datetime import datetime
from report_logic_compare import generate_compare_report
from csv_parser import parse_ga4_csv

APP_VERSION = "2026-07-09-v2"

# ─────────────────────────────────────────────────────
# アイコン画像パス
# ─────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_TITLE = os.path.join(SCRIPT_DIR, "image1.png")
ICON_SETTING = os.path.join(SCRIPT_DIR, "image2.png")
ICON_PDF = os.path.join(SCRIPT_DIR, "iamge3.png")

# ─────────────────────────────────────────────────────
# ユーティリティ
# ─────────────────────────────────────────────────────
def icon_b64(path):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    return None

def icon_img_tag(path, size=28):
    b64 = icon_b64(path)
    if b64:
        return f'<img src="data:image/png;base64,{b64}" width="{size}" style="vertical-align: middle; margin-right: 8px;">'
    return ""

def display_pdf(pdf_bytes):
    try:
        from streamlit_pdf_viewer import pdf_viewer
        pdf_viewer(pdf_bytes, width=700)
    except Exception as e:
        st.info("プレビュー表示に失敗したため、PDFはダウンロードしてご確認ください。")
        st.caption(f"preview error: {e}")

def validate_csvs(cur_bytes, prev_bytes):
    """2つのCSVのバリデーション。エラーがあればメッセージを返す。"""
    errors = []
    try:
        cur_data = parse_ga4_csv(cur_bytes)
        prev_data = parse_ga4_csv(prev_bytes)
    except Exception as e:
        return [f"CSVの解析に失敗しました: {e}"]

    # 同一プロパティ確認
    cur_prop = cur_data['meta'].get('property', '')
    prev_prop = prev_data['meta'].get('property', '')
    if cur_prop and prev_prop and cur_prop != prev_prop:
        errors.append(f"プロパティが一致しません。当月: {cur_prop} / 比較月: {prev_prop}")

    # 期間の前後確認
    cur_end = cur_data['meta'].get('end', '')
    prev_end = prev_data['meta'].get('end', '')
    if cur_end and prev_end and cur_end <= prev_end:
        errors.append(f"当月の終了日({cur_end})が比較月の終了日({prev_end})以前です。アップロード先を確認してください。")

    return errors

# ─────────────────────────────────────────────────────
# Streamlit UI 設定
# ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="GA4 月次比較レポート",
    page_icon="📊",
    layout="wide"
)

st.markdown("""
    <style>
    .main {
        background-color: #F5F8FC;
    }
    footer {visibility: hidden;}
    #MainMenu {visibility: hidden;}
    header[data-testid="stHeader"] {visibility: hidden;}
    .viewerBadge_container__r5tak {display: none !important;}
    .stActionButton {display: none !important;}
    [data-testid="manage-app-button"] {display: none !important;}
    .styles_viewerBadge__CvC9N {display: none !important;}
    ._profileContainer_gzau3_53 {display: none !important;}
    ._profilePreview_gzau3_63 {display: none !important;}
    [data-testid="stStatusWidget"] {display: none !important; visibility: hidden !important;}
    div[data-testid="stStatusWidget"] {display: none !important; visibility: hidden !important;}
    [data-testid="stDecoration"] {display: none !important; visibility: hidden !important;}
    div[class*="stDeployButton"] {display: none !important;}
    div[class*="StatusWidget"] {display: none !important;}
    button[kind="manage"] {display: none !important;}
    [class*="viewerBadge"] {display: none !important;}
    [class*="statusWidget"] {display: none !important;}
    [class*="StatusWidget"] {display: none !important;}
    input::placeholder {color: #C8D0D8 !important; opacity: 1 !important;}
    .stButton>button {
        background-color: #1A6FAB;
        color: white;
        border-radius: 5px;
        width: 100%;
        height: 3em;
        font-weight: bold;
    }
    .stDownloadButton>button {
        background-color: #4DA3D4;
        color: white;
        border-radius: 5px;
        width: 100%;
        height: 3em;
        font-weight: bold;
    }
    [data-testid="stFileUploaderDropzone"] div:has(> small) div:first-child {
        visibility: hidden;
        position: relative;
        height: 1.2em;
    }
    [data-testid="stFileUploaderDropzone"] div:has(> small) div:first-child::after {
        content: "ここにファイルをドラッグ＆ドロップ";
        visibility: visible;
        position: absolute;
        top: 0;
        left: 0;
    }
    [data-testid="stFileUploaderDropzone"] small {
        visibility: hidden;
        position: relative;
        height: 1.2em;
        display: inline-block;
    }
    [data-testid="stFileUploaderDropzone"] small::after {
        content: "ファイルサイズ上限: 200MB";
        visibility: visible;
        position: absolute;
        top: 0;
        left: 0;
        white-space: nowrap;
    }
    [data-testid="stFileUploaderDropzone"] button {
        visibility: hidden;
        position: relative;
    }
    [data-testid="stFileUploaderDropzone"] button::after {
        content: "ファイルを選択";
        visibility: visible;
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        white-space: nowrap;
    }
    </style>
    """, unsafe_allow_html=True)

# ヘッダー
_title_icon = icon_img_tag(ICON_TITLE, size=40)
st.markdown(
    f'<h1 style="display:flex; align-items:center;">{_title_icon}GA4 月次比較レポート生成ツール</h1>',
    unsafe_allow_html=True
)
st.write("GA4からエクスポートしたCSVをアップロードしてレポートを自動生成します。**比較したい月** のCSVが未指定の場合は、**単月モード** として分析します。")

st.divider()

# サイドバー設定
_setting_icon = icon_img_tag(ICON_SETTING, size=28)
st.sidebar.markdown(
    f'<h2 style="display:flex; align-items:center;">{_setting_icon}レポート設定</h2>',
    unsafe_allow_html=True
)
company_name = st.sidebar.text_input("会社名", value="", placeholder="入力例）ABC株式会社")
staff_name = st.sidebar.text_input("担当者名", value="", placeholder="入力例）サンプル 太郎")
site_url = st.sidebar.text_input("対象サイトURL", value="", placeholder="入力例）https://example.com")

st.sidebar.divider()
report_type = st.sidebar.radio(
    "レポートの種類",
    ["標準版 (9ページ)", "詳細版 (11ページ)"],
    index=1,
    help="【標準版】主要KPI比較・チャネル・CV・上位ページ・ユーザー行動・総括・用語解説の構成。\n\n【詳細版】標準版に加えて、人気ページエンゲージ分析・コホート分析・広告分析を追加した構成。"
)

# メインコンテンツ
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. データのインポート")

    st.markdown("**当月のCSV**")
    uploaded_cur = st.file_uploader("当月のGA4エクスポートCSV", type=["csv"], key="cur_csv")

    st.markdown("**比較したい月のCSV**")
    uploaded_prev = st.file_uploader("比較したい月のGA4エクスポートCSV", type=["csv"], key="prev_csv")

    uploaded_logo = st.file_uploader("ロゴを入れたい場合（任意）", type=["png", "jpg", "jpeg"])

with col2:
    st.subheader("2. レポートの生成")

    if uploaded_cur is not None:
        single_mode = uploaded_prev is None
        if single_mode:
            st.success("当月CSVが読み込めました（単月モードで生成できます）")
            st.info("比較したい月のCSVが未指定のため、当月データを基準に単月分析レポートを生成します。")
        else:
            st.success("2つのCSVファイルが読み込めました！")

        button_label = "🚀 単月分析PDFレポートを生成する" if single_mode else "🚀 比較PDFレポートを生成する"
        if st.button(button_label):
            cur_bytes = uploaded_cur.getvalue()
            prev_bytes = cur_bytes if single_mode else uploaded_prev.getvalue()

            # バリデーション
            if not single_mode:
                validation_errors = validate_csvs(cur_bytes, prev_bytes)
                if validation_errors:
                    for err in validation_errors:
                        st.warning(err)

            spinner_text = "単月分析レポートを生成中です。しばらくお待ちください..." if single_mode else "比較レポートを生成中です。しばらくお待ちください..."
            with st.spinner(spinner_text):
                try:
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        cur_path = os.path.join(tmp_dir, "current.csv")
                        prev_path = os.path.join(tmp_dir, "previous.csv")

                        with open(cur_path, "wb") as f:
                            f.write(cur_bytes)
                        with open(prev_path, "wb") as f:
                            f.write(prev_bytes)

                        logo_path = None
                        if uploaded_logo:
                            logo_path = os.path.join(tmp_dir, "logo.png")
                            with open(logo_path, "wb") as f:
                                f.write(uploaded_logo.getbuffer())

                        output_pdf_path = os.path.join(tmp_dir, "compare_report.pdf")

                        generate_compare_report(
                            cur_path, prev_path, output_pdf_path,
                            company_name=company_name,
                            department=staff_name,
                            logo_path=logo_path,
                            detailed=("詳細版" in report_type),
                            single_mode=single_mode,
                            site_url=site_url,
                        )

                        with open(output_pdf_path, "rb") as pdf_file:
                            st.session_state['pdf_data'] = pdf_file.read()

                    st.session_state['pdf_ready'] = True
                    st.rerun()
                except Exception as e:
                    st.error(f"エラーが発生しました (v{APP_VERSION}): {e}")
                    st.code(traceback.format_exc())

        # 生成済みPDFがあれば表示
        if st.session_state.get('pdf_ready'):
            pdf_data = st.session_state['pdf_data']
            done_text = "単月分析レポートの生成が完了しました！" if single_mode else "比較レポートの生成が完了しました！"
            st.success(done_text)

            st.download_button(
                label="📥 PDFをダウンロードする",
                data=pdf_data,
                file_name=f"GA4月次比較レポート_{company_name}_{datetime.now().strftime('%Y%m%d')}.pdf",
                mime="application/pdf"
            )
    else:
        st.info("当月のCSVをアップロードしてください（比較したい月のCSVは任意）。")
        if 'pdf_ready' in st.session_state:
            del st.session_state['pdf_ready']
            del st.session_state['pdf_data']

# プレビュー表示
if st.session_state.get('pdf_ready'):
    st.divider()
    st.subheader("レポートプレビュー")
    display_pdf(st.session_state['pdf_data'])

st.divider()
st.caption(f"© 2026 MOCAL株式会社 | GA4 月次比較レポート | v{APP_VERSION}")
