# -*- coding: utf-8 -*-
"""
MOMO 商品價格查詢與視覺化 (Streamlit)
執行：streamlit run momo_app.py
"""
import re
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from curl_cffi import requests

API_URL = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
HOME_URL = "https://www.momoshop.com.tw/"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}


# ------------------------------------------------------------------
# 爬蟲 (CODE01)
# ------------------------------------------------------------------
def build_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
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
        },
    }


def parse_price(value) -> int | None:
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


@st.cache_data(show_spinner=False, ttl=600)
def fetch_momo(keyword: str, pages: int) -> pd.DataFrame:
    session = requests.Session(impersonate="chrome")  # 模擬 Chrome TLS 指紋
    session.get(HOME_URL, headers=HEADERS)  # 先取得 cookie

    rows = []
    for page in range(1, pages + 1):
        resp = session.post(API_URL, headers=HEADERS, json=build_payload(keyword, page))
        if resp.status_code != 200:
            raise RuntimeError(f"第 {page} 頁請求失敗：HTTP {resp.status_code}\n{resp.text[:300]}")
        goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
        if not goods:
            break  # 沒有更多資料
        for p in goods:
            rows.append(
                {
                    "頁數": page,
                    "品名": p.get("goodsName", ""),
                    "價格": parse_price(p.get("goodsPrice", "")),
                }
            )
        time.sleep(0.8)  # 禮貌性延遲，避免被擋

    df = pd.DataFrame(rows, columns=["頁數", "品名", "價格"])
    df = df.dropna(subset=["價格"]).reset_index(drop=True)
    df["價格"] = df["價格"].astype(int)
    return df


# ------------------------------------------------------------------
# 資料處理
# ------------------------------------------------------------------
def extract_brand(name: str) -> str:
    """momo 品名常以【品牌】開頭，抓不到則歸為『其他』。"""
    m = re.search(r"[【\[](.+?)[】\]]", name)
    return m.group(1).strip()[:12] if m else "其他"


def add_price_bins(df: pd.DataFrame, n_bins: int = 5) -> pd.DataFrame:
    df = df.copy()
    if df["價格"].nunique() <= 1:
        df["價格區間"] = f"{df['價格'].iloc[0]}"
        return df
    try:
        binned = pd.qcut(df["價格"], q=n_bins, duplicates="drop")
    except ValueError:
        binned = pd.cut(df["價格"], bins=n_bins)
    df["價格區間"] = binned.apply(lambda iv: f"{int(iv.left)}~{int(iv.right)}")
    # 依區間下界排序，圖例才會由低到高
    order = sorted(df["價格區間"].unique(), key=lambda s: int(s.split("~")[0]))
    df["價格區間"] = pd.Categorical(df["價格區間"], categories=order, ordered=True)
    return df


# ------------------------------------------------------------------
# 圖表 (CODE02) —— 使用 Plotly，字型由瀏覽器渲染，不需安裝中文字型
# ------------------------------------------------------------------
def pie_chart(df: pd.DataFrame, keyword: str):
    count = df.groupby("價格區間", observed=True).size().reset_index(name="商品數")
    fig = px.pie(count, names="價格區間", values="商品數", hole=0.35,
                 title=f"「{keyword}」價格區間分布")
    fig.update_traces(textinfo="percent+label")
    return fig


def line_chart(df: pd.DataFrame, keyword: str):
    mean_price = df["價格"].mean()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df["價格"], mode="lines+markers",
                             name="價格", text=df["品名"],
                             hovertemplate="%{text}<br>$%{y:,}<extra></extra>"))
    fig.add_hline(y=mean_price, line_dash="dash", line_color="red",
                  annotation_text=f"平均 {mean_price:,.0f}")
    fig.update_layout(title=f"MOMO「{keyword}」售價", xaxis_title="商品序號",
                      yaxis_title="價格 (元)")
    return fig


def sunburst_chart(df: pd.DataFrame, keyword: str):
    d = df.copy()
    d["品牌"] = d["品名"].apply(extract_brand)
    # 只保留商品數前 12 名品牌，其餘合併，避免圖太雜
    top = d["品牌"].value_counts().head(12).index
    d["品牌"] = d["品牌"].where(d["品牌"].isin(top), "其他")
    d["價格區間"] = d["價格區間"].astype(str)
    grp = d.groupby(["價格區間", "品牌"]).size().reset_index(name="商品數")
    fig = px.sunburst(grp, path=["價格區間", "品牌"], values="商品數",
                      title=f"「{keyword}」價格區間 → 品牌")
    return fig


# ------------------------------------------------------------------
# Streamlit 介面
# ------------------------------------------------------------------
st.set_page_config(page_title="MOMO 價格查詢", page_icon="🛒", layout="wide")
st.title("🛒 MOMO 商品價格查詢與視覺化")

with st.sidebar:
    st.header("查詢條件")
    keyword = st.text_input("商品關鍵字", value="行動電源")
    pages = st.number_input("爬取頁數", min_value=1, max_value=20, value=2, step=1)
    chart_type = st.radio("圖表類型", ["圓餅圖", "折線圖", "旭日圖"])
    n_bins = st.slider("價格區間數 (圓餅圖/旭日圖)", 3, 8, 5)
    run = st.button("開始查詢", type="primary", use_container_width=True)

if run:
    if not keyword.strip():
        st.warning("請輸入商品關鍵字")
        st.stop()
    try:
        with st.spinner(f"正在爬取「{keyword}」前 {pages} 頁…"):
            st.session_state["df"] = fetch_momo(keyword.strip(), int(pages))
            st.session_state["keyword"] = keyword.strip()
    except Exception as e:
        st.error(f"爬取失敗：{e}")
        st.stop()

df = st.session_state.get("df")
if df is None:
    st.info("請在左側輸入關鍵字與頁數，然後按「開始查詢」。")
    st.stop()
if df.empty:
    st.warning("查無資料，請換個關鍵字試試。")
    st.stop()

kw = st.session_state["keyword"]

c1, c2, c3, c4 = st.columns(4)
c1.metric("商品數", f"{len(df):,}")
c2.metric("平均價格", f"${df['價格'].mean():,.0f}")
c3.metric("最高價格", f"${df['價格'].max():,}")
c4.metric("最低價格", f"${df['價格'].min():,}")

df_binned = add_price_bins(df, n_bins)

if chart_type == "圓餅圖":
    st.plotly_chart(pie_chart(df_binned, kw), use_container_width=True)
elif chart_type == "折線圖":
    st.plotly_chart(line_chart(df, kw), use_container_width=True)
else:
    st.plotly_chart(sunburst_chart(df_binned, kw), use_container_width=True)

st.subheader("商品明細")
st.dataframe(df, use_container_width=True, hide_index=True)

csv = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
st.download_button("下載 CSV", csv,
                   file_name=f"MOMO_{kw}_{time.strftime('%Y%m%d')}.csv",
                   mime="text/csv")
