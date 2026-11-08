import re
import time
import math

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ----------------------------------------------------------------------
# 基本設定
# ----------------------------------------------------------------------
st.set_page_config(page_title="MOMO 商品價格查詢", page_icon="🛒", layout="wide")

API_URL = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    )
}
# 中文字型：Plotly 由瀏覽器渲染，直接指定系統中文字型即可，不需要 !wget 下載字型檔
CJK_FONT = "Microsoft JhengHei, PingFang TC, Noto Sans CJK TC, Noto Sans TC, sans-serif"


def build_payload(keyword: str, page: int) -> dict:
    """組合 MOMO 搜尋 API 的 payload"""
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "specialGoodsType": "",
            "isBrandSeriesPage": "false",
            "authorNo": "",
            "originalCateCode": "",
            "cateType": "",
            "searchValue": keyword,
            "cateCode": "",
            "cateLevel": "-1",
            "cp": "N",
            "NAM": "N",
            "first": "N",
            "freeze": "N",
            "superstore": "N",
            "tvshop": "N",
            "china": "N",
            "tomorrow": "N",
            "stockYN": "N",
            "prefere": "N",
            "threeHours": "N",
            "video": "N",
            "cycle": "N",
            "cod": "N",
            "superstorePay": "N",
            "showType": "chessboardType",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "searchType": "1",
            "reduceKeyword": "",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
            "adSource": "tenmax",
        },
    }


def parse_price(value) -> int | None:
    """把 '$1,299' 之類的字串轉成整數"""
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def extract_brand(name: str) -> str:
    """從品名擷取品牌：優先抓【】或[]內文字，否則取第一個詞"""
    m = re.search(r"[【\[](.+?)[】\]]", name)
    brand = m.group(1) if m else re.split(r"[\s/／]", name.strip())[0]
    brand = brand.strip()
    return brand[:12] if brand else "其他"


def nice_step(max_price: float, parts: int = 6) -> int:
    """依最高價格自動決定『漂亮』的價格區間寬度 (1/2/5 × 10^n)"""
    raw = max(max_price / parts, 1)
    exp = 10 ** int(math.floor(math.log10(raw)))
    for m in (1, 2, 5, 10):
        if raw <= m * exp:
            return int(m * exp)
    return int(10 * exp)


# ----------------------------------------------------------------------
# 爬蟲 (快取 30 分鐘，避免重複請求)
# ----------------------------------------------------------------------
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_page(keyword: str, page: int) -> list[dict]:
    resp = requests.post(
        API_URL, headers=HEADERS, json=build_payload(keyword, page), timeout=15
    )
    resp.raise_for_status()
    goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", []) or []
    rows = []
    for g in goods:
        price = parse_price(g.get("goodsPrice", ""))
        if price is None:
            continue
        code = g.get("goodsCode", "")
        rows.append(
            {
                "品名": g.get("goodsName", ""),
                "價格": price,
                "連結": f"https://www.momoshop.com.tw/goods/GoodsDetail.jsp?i_code={code}"
                if code
                else "",
            }
        )
    return rows


def crawl(keyword: str, start_page: int, pages: int) -> pd.DataFrame:
    all_rows: list[dict] = []
    bar = st.progress(0.0, text="開始爬取…")
    for i in range(pages):
        page = start_page + i
        bar.progress((i + 1) / pages, text=f"爬取第 {page} 頁（{i + 1}/{pages}）")
        try:
            rows = fetch_page(keyword, page)
        except requests.RequestException as e:
            st.warning(f"第 {page} 頁請求失敗：{e}")
            break
        if not rows:
            st.info(f"第 {page} 頁沒有資料，停止爬取。")
            break
        for r in rows:
            r["頁數"] = page
        all_rows.extend(rows)
        time.sleep(0.8)  # 禮貌性延遲，避免被封鎖
    bar.empty()

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["品名", "價格"]).reset_index(drop=True)
        df["品牌"] = df["品名"].map(extract_brand)
    return df


# ----------------------------------------------------------------------
# 圖表
# ----------------------------------------------------------------------
def add_price_range(df: pd.DataFrame, step: int) -> pd.DataFrame:
    df = df.copy()
    top = (int(df["價格"].max() // step) + 1) * step
    bins = list(range(0, top + step, step))
    labels = [f"{bins[i]:,}–{bins[i + 1]:,}" for i in range(len(bins) - 1)]
    df["價格區間"] = pd.cut(
        df["價格"], bins=bins, labels=labels, right=False, include_lowest=True
    ).astype(str)
    return df


def style(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=22)),
        font=dict(family=CJK_FONT, size=14),
        margin=dict(t=70, l=20, r=20, b=40),
        height=600,
    )
    return fig


def pie_chart(df: pd.DataFrame, keyword: str, step: int) -> go.Figure:
    d = add_price_range(df, step)
    cnt = d["價格區間"].value_counts().reset_index()
    cnt.columns = ["價格區間", "商品數"]
    # 依區間下限排序
    cnt["_s"] = cnt["價格區間"].str.split("–").str[0].str.replace(",", "").astype(int)
    cnt = cnt.sort_values("_s")
    fig = px.pie(cnt, names="價格區間", values="商品數", hole=0.35)
    fig.update_traces(sort=False, textinfo="label+percent")
    return style(fig, f"「{keyword}」價格區間分布")


def line_chart(df: pd.DataFrame, keyword: str, max_points: int) -> go.Figure:
    d = df.head(max_points).reset_index(drop=True)
    avg = d["價格"].mean()
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=d.index + 1,
            y=d["價格"],
            mode="lines+markers",
            name="售價",
            text=d["品名"],
            hovertemplate="#%{x}<br>%{text}<br>$%{y:,}<extra></extra>",
        )
    )
    fig.add_hline(
        y=avg,
        line_dash="dash",
        line_color="red",
        annotation_text=f"平均 ${avg:,.0f}",
        annotation_position="top left",
    )
    fig.update_xaxes(title="商品序號")
    fig.update_yaxes(title="價格 (NT$)")
    return style(fig, f"MOMO 上「{keyword}」售價（前 {len(d)} 筆）")


def sunburst_chart(df: pd.DataFrame, keyword: str, step: int, top_brands: int) -> go.Figure:
    d = add_price_range(df, step)
    # 只保留最多商品的品牌，其餘併入「其他」
    top = d["品牌"].value_counts().head(top_brands).index
    d["品牌"] = d["品牌"].where(d["品牌"].isin(top), "其他")
    g = d.groupby(["價格區間", "品牌"]).size().reset_index(name="商品數")
    fig = px.sunburst(g, path=["價格區間", "品牌"], values="商品數")
    fig.update_traces(textinfo="label+percent parent")
    return style(fig, f"「{keyword}」價格區間 × 品牌 旭日圖")


# ----------------------------------------------------------------------
# 介面
# ----------------------------------------------------------------------
st.title("🛒 MOMO 購物網商品價格查詢")
st.caption("輸入商品關鍵字與頁數，即時爬取 MOMO 搜尋結果並視覺化。")

with st.sidebar:
    st.header("查詢條件")
    keyword = st.text_input("商品關鍵字", value="行動電源")
    start_page = st.number_input("起始頁數", min_value=1, max_value=50, value=1, step=1)
    pages = st.number_input("爬取頁數", min_value=1, max_value=20, value=3, step=1)
    st.divider()
    chart_type = st.radio("圖表類型", ["圓餅圖", "折線圖", "旭日圖"])
    auto_step = st.checkbox("自動決定價格區間寬度", value=True)
    manual_step = st.number_input(
        "價格區間寬度 (NT$)", min_value=10, value=500, step=50, disabled=auto_step
    )
    run = st.button("🔍 開始查詢", type="primary", use_container_width=True)

if run:
    if not keyword.strip():
        st.error("請輸入商品關鍵字")
        st.stop()
    with st.spinner("查詢中…"):
        st.session_state["df"] = crawl(keyword.strip(), int(start_page), int(pages))
        st.session_state["keyword"] = keyword.strip()

df: pd.DataFrame | None = st.session_state.get("df")

if df is None:
    st.info("請在左側輸入條件後按下「開始查詢」。")
elif df.empty:
    st.warning("查無資料，請換個關鍵字或頁數再試。")
else:
    kw = st.session_state["keyword"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("商品數", f"{len(df):,}")
    c2.metric("平均價格", f"${df['價格'].mean():,.0f}")
    c3.metric("最高價格", f"${df['價格'].max():,}")
    c4.metric("最低價格", f"${df['價格'].min():,}")

    step = nice_step(df["價格"].max()) if auto_step else int(manual_step)

    if chart_type == "圓餅圖":
        fig = pie_chart(df, kw, step)
    elif chart_type == "折線圖":
        max_points = st.slider(
            "顯示筆數", min_value=5, max_value=len(df), value=min(70, len(df))
        ) if len(df) > 5 else len(df)
        fig = line_chart(df, kw, max_points)
    else:
        top_brands = st.slider("顯示前幾大品牌", 3, 30, 12)
        fig = sunburst_chart(df, kw, step, top_brands)

    st.plotly_chart(fig, use_container_width=True)

    st.subheader("原始資料")
    st.dataframe(
        df[["頁數", "品牌", "品名", "價格", "連結"]],
        use_container_width=True,
        column_config={"連結": st.column_config.LinkColumn("連結", display_text="前往")},
        hide_index=True,
    )
    st.download_button(
        "⬇️ 下載 CSV",
        data=df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
        file_name=f"MOMO_{kw}.csv",
        mime="text/csv",
    )
