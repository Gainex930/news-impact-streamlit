from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
import streamlit as st
from bs4 import BeautifulSoup

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover
    st_autorefresh = None


CN_TZ = timezone(timedelta(hours=8))
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


SECTOR_RULES = [
    ("存储芯片", ["存储", "DRAM", "NAND", "HBM", "SSD", "内存", "闪存", "涨价", "memory", "micron"], 14),
    ("半导体", ["芯片", "半导体", "晶圆", "封测", "光刻", "EDA", "先进封装", "CoWoS", "chip", "wafer"], 12),
    ("AI算力", ["AI", "算力", "数据中心", "服务器", "GPU", "大模型", "Token", "cloud", "compute"], 13),
    ("机器人", ["机器人", "人形机器人", "减速器", "灵巧手", "传感器", "Optimus", "robot"], 11),
    ("新能源车", ["新能源车", "汽车电子", "智能驾驶", "Robotaxi", "电池", "固态电池", "EV", "Tesla"], 10),
    ("光伏储能", ["光伏", "逆变器", "储能", "组件", "硅料", "solar", "energy storage"], 10),
    ("券商金融", ["券商", "证券", "保险", "银行", "资本市场", "broker", "fed", "treasury"], 12),
    ("地产链", ["房地产", "住房", "城中村", "保障房", "家居", "建材", "mortgage", "housing"], 10),
    ("医药医疗", ["医药", "创新药", "医疗", "集采", "临床", "CXO", "drug", "biotech"], 9),
    ("消费零售", ["消费", "零售", "白酒", "食品", "旅游", "免税", "家电", "retail", "consumer"], 8),
    ("低空经济", ["低空", "eVTOL", "无人机", "通航", "drone", "aviation"], 10),
    ("有色资源", ["铜", "铝", "锂", "钴", "稀土", "黄金", "copper", "lithium", "gold"], 11),
    ("电力电网", ["电力", "电网", "特高压", "变压器", "电价", "power", "grid"], 10),
    ("军工航天", ["军工", "卫星", "商业航天", "导弹", "雷达", "space", "defense"], 10),
    ("化工材料", ["化工", "氟化工", "材料", "电子化学品", "chemical", "materials"], 9),
]

POSITIVE_SIGNALS = [
    ("涨价", 16), ("大幅增长", 15), ("同比增长", 11), ("中标", 12), ("订单饱满", 14),
    ("超预期", 14), ("大额订单", 13), ("订单", 7), ("回购", 10), ("增持", 11),
    ("政策支持", 14), ("利好", 14), ("上调", 9), ("突破", 9), ("rally", 12),
    ("surge", 13), ("bullish", 12), ("approval", 9), ("record", 9),
]

NEGATIVE_SIGNALS = [
    ("减持", 16), ("询价转让", 13), ("风险提示", 15), ("处罚", 16), ("立案", 18),
    ("亏损", 14), ("预亏", 15), ("停产", 17), ("暂停供应", 16), ("下滑", 10),
    ("退市", 20), ("跳水", 10), ("tumble", 14), ("sink", 13), ("drop", 10),
    ("tariff", 12), ("ban", 13), ("lawsuit", 12),
]

POLICY_WORDS = ["国务院", "财政部", "央行", "证监会", "发改委", "工信部", "政策", "规划", "fed", "treasury"]
MARKET_WORDS = ["A股", "沪指", "创业板", "指数", "市场", "资金", "北向", "stock", "stocks", "nasdaq", "s&p"]


@dataclass
class Source:
    key: str
    name: str
    region: str
    category: str
    credibility: int


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def limit_text(value: str, size: int = 120) -> str:
    text = clean_text(value)
    return text if len(text) <= size else text[:size].rstrip("，。；、 ") + "..."


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat()


def parse_cn_time(value: str) -> str:
    if not value:
        return ""
    text = clean_text(value)
    patterns = [
        r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?",
        r"(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})(?::(\d{2}))?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        parts = match.groups(default="00")
        if len(parts) == 6 and parts[0].startswith("20"):
            year, month, day, hour, minute, second = parts
        else:
            year = str(datetime.now(CN_TZ).year)
            month, day, hour, minute, second = parts
        dt = datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second), tzinfo=CN_TZ
        )
        return dt.isoformat()
    return ""


def infer_time_from_url(url: str, offset: int = 0) -> str:
    detailed = re.search(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})[^\d]?(\d{2})(\d{2})(\d{2})?", url)
    if detailed:
        y, m, d, hh, mm, ss = detailed.groups(default="00")
        return datetime(int(y), int(m), int(d), int(hh), int(mm), int(ss), tzinfo=CN_TZ).isoformat()
    match = re.search(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", url)
    if match:
        y, m, d = match.groups()
        return datetime(int(y), int(m), int(d), 0, 0, tzinfo=CN_TZ).isoformat()
    return (datetime.now(CN_TZ) - timedelta(hours=2, seconds=offset * 45)).isoformat()


def fetch_text(url: str, *, encoding: str | None = None, headers: dict[str, str] | None = None) -> str:
    response = requests.get(url, headers={**REQUEST_HEADERS, **(headers or {})}, timeout=12)
    response.raise_for_status()
    if encoding:
        response.encoding = encoding
    elif not response.encoding or response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding
    return response.text


def make_item(source: Source, title: str, summary: str, url: str = "", item_time: str = "", stocks: list[str] | None = None) -> dict[str, Any]:
    item = {
        "id": f"{source.key}:{url or title}",
        "source": source.name,
        "source_key": source.key,
        "region": source.region,
        "category": source.category,
        "credibility": source.credibility,
        "title": clean_text(title),
        "summary": clean_text(summary or title),
        "url": url,
        "time": item_time or now_iso(),
        "stocks": stocks or [],
    }
    item["analysis"] = analyze_item(item)
    return item


def includes(text: str, keyword: str) -> bool:
    return keyword.lower() in text.lower()


def analyze_item(item: dict[str, Any]) -> dict[str, Any]:
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    positive = 0
    negative = 0
    impact = 22
    reasons: list[str] = []

    for word, weight in POSITIVE_SIGNALS:
        if includes(text, word):
            positive += weight
            impact += round(weight * 0.65)
            reasons.append(f"命中正向信号：{word}")
    for word, weight in NEGATIVE_SIGNALS:
        if includes(text, word):
            negative += weight
            impact += round(weight * 0.7)
            reasons.append(f"命中风险信号：{word}")

    sectors = []
    for name, keywords, weight in SECTOR_RULES:
        hits = [keyword for keyword in keywords if includes(text, keyword)]
        if hits:
            sectors.append({"name": name, "score": weight + len(hits) * 3, "hits": hits[:3]})
    sectors.sort(key=lambda row: row["score"], reverse=True)
    if sectors:
        impact += sectors[0]["score"]
        reasons.append(f"关联{sectors[0]['name']}：{', '.join(sectors[0]['hits'])}")

    policy_hits = [word for word in POLICY_WORDS if includes(text, word)]
    market_hits = [word for word in MARKET_WORDS if includes(text, word)]
    if policy_hits:
        impact += 14
        reasons.append("涉及政策/监管口径")
    if market_hits:
        impact += 8
        reasons.append("可能影响市场风险偏好")
    if re.search(r"\d+(?:\.\d+)?\s*%", text):
        impact += 4
        reasons.append("包含百分比变化，便于快速定价")
    if item.get("stocks"):
        impact += min(12, max(3, len(item["stocks"]) * 3))
        reasons.append(f"关联{len(item['stocks'])}个标的/标签")
    if item.get("category") == "rumor":
        impact += 12
        reasons.append("小作文/社区信息：需二次核验")
    if item.get("category") == "article":
        impact += 4
        reasons.append("长文信息：更适合提炼中期叙事")

    if positive > negative + 5:
        direction = "利好"
    elif negative > positive + 5:
        direction = "利空"
    elif positive and negative:
        direction = "多空混合"
    else:
        direction = "中性"

    score = max(18, min(96, round(impact)))
    level = "高" if score >= 76 else "中" if score >= 56 else "低"
    confidence = max(18, min(94, 26 + round(item["credibility"] * 0.45) + len(reasons) * 5 - (20 if item.get("category") == "rumor" else 0)))
    return {
        "score": score,
        "level": level,
        "direction": direction,
        "confidence": confidence,
        "sectors": [sector["name"] for sector in sectors[:4]],
        "reasons": list(dict.fromkeys(reasons))[:7],
    }


def fetch_eastmoney_flash(limit: int = 80) -> list[dict[str, Any]]:
    source = Source("eastmoney_flash", "东方财富快讯", "国内", "快讯", 82)
    endpoint = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList"
    params = {
        "client": "web",
        "biz": "web_724",
        "fastColumn": "102",
        "sortEnd": "",
        "pageSize": min(limit, 80),
        "req_trace": str(int(time.time() * 1000)),
    }
    data = requests.get(endpoint, params=params, headers={**REQUEST_HEADERS, "Referer": "https://kuaixun.eastmoney.com/"}, timeout=12).json()
    rows = data.get("data", {}).get("fastNewsList", [])
    return [
        make_item(
            source,
            row.get("title", ""),
            row.get("summary") or row.get("title", ""),
            f"https://finance.eastmoney.com/a/{row.get('code')}.html" if row.get("code") else "",
            parse_cn_time(row.get("showTime", "").replace("-", "/")) or now_iso(),
            [stock for stock in row.get("stockList", []) if isinstance(stock, str)],
        )
        for row in rows
        if row.get("title")
    ]


def fetch_eastmoney_articles(limit: int = 24) -> list[dict[str, Any]]:
    source = Source("eastmoney_article", "东方财富财经长文", "国内", "大作文", 76)
    soup = BeautifulSoup(fetch_text("https://finance.eastmoney.com/a/czqyw.html", headers={"Referer": "https://finance.eastmoney.com/"}), "html.parser")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        url = link["href"]
        title = clean_text(link.get_text(" "))
        if not re.match(r"https://finance\.eastmoney\.com/a/\d+\.html", url) or len(title) < 8 or url in seen:
            continue
        seen.add(url)
        items.append(make_item(source, title, title, url, infer_time_from_url(url, len(items))))
        if len(items) >= limit:
            break
    return items


def fetch_sina_roll(limit: int = 40) -> list[dict[str, Any]]:
    source = Source("sina_finance_roll", "新浪财经滚动", "国内", "快讯", 74)
    html_text = fetch_text("https://finance.sina.com.cn/roll/c/51894.shtml", headers={"Referer": "https://finance.sina.com.cn/"})
    soup = BeautifulSoup(html_text, "html.parser")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for li in soup.find_all("li"):
        link = li.find("a", href=True)
        if not link:
            continue
        url = urljoin("https://finance.sina.com.cn", link["href"])
        title = clean_text(link.get_text(" "))
        if "sina.com.cn" not in url or len(title) < 8 or url in seen:
            continue
        span = li.find("span")
        item_time = parse_cn_time(span.get_text(" ") if span else "") or infer_time_from_url(url, len(items))
        seen.add(url)
        items.append(make_item(source, limit_text(title, 120), title, url, item_time))
        if len(items) >= limit:
            break
    return items


def fetch_html_list(source: Source, url: str, base_url: str, include: str, limit: int = 24, encoding: str | None = None) -> list[dict[str, Any]]:
    soup = BeautifulSoup(fetch_text(url, encoding=encoding, headers={"Referer": base_url}), "html.parser")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(include, re.I)
    for link in soup.find_all("a", href=True):
        href = urljoin(base_url, link["href"])
        title = clean_text(link.get_text(" "))
        if not pattern.search(href) or len(title) < 8 or len(title) > 140 or href in seen:
            continue
        seen.add(href)
        items.append(make_item(source, title, title, href, infer_time_from_url(href, len(items))))
        if len(items) >= limit:
            break
    return items


def fetch_stcn_flash(limit: int = 40) -> list[dict[str, Any]]:
    source = Source("stcn_flash", "证券时报快讯", "国内", "快讯", 80)
    text = fetch_text(
        "https://www.stcn.com/article/list.html?type=kx",
        headers={"Referer": "https://www.stcn.com/article/list/kx.html", "X-Requested-With": "XMLHttpRequest"},
    )
    data = json.loads(text)
    rows = data.get("data") if isinstance(data.get("data"), list) else []
    return [
        make_item(
            source,
            row.get("title") or clean_text(row.get("content", ""))[:80],
            row.get("content") or row.get("title", ""),
            urljoin("https://www.stcn.com", row.get("web_url") or row.get("url") or ""),
            datetime.fromtimestamp((row.get("time") or int(row.get("show_time", 0)) * 1000) / 1000, CN_TZ).isoformat() if (row.get("time") or row.get("show_time")) else now_iso(),
        )
        for row in rows[:limit]
        if row.get("title") or row.get("content")
    ]


def stringify_cls_param(key: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return f"{key}={value}"
    if isinstance(value, list):
        return "&".join(stringify_cls_param(f"{key}[{idx}]", item) for idx, item in enumerate(value)) if value else f"{key}[]"
    if isinstance(value, dict):
        return "&".join(
            stringify_cls_param(f"{key}[{child}]", value[child])
            for child in sorted(value, key=lambda x: str(x).upper())
        )
    return ""


def sign_cls(params: dict[str, Any]) -> str:
    raw = "&".join(
        part for part in (
            stringify_cls_param(key, params[key])
            for key in sorted(params, key=lambda x: str(x).upper())
        ) if part
    )
    sha1 = hashlib.sha1(raw.encode()).hexdigest()
    return hashlib.md5(sha1.encode()).hexdigest()


def fetch_cls_telegraph(limit: int = 50) -> list[dict[str, Any]]:
    source = Source("cls_telegraph", "财联社电报", "国内", "快讯", 86)
    params = {"app": "CailianpressWeb", "os": "web", "refresh_type": 1, "rn": min(limit, 50), "sv": "8.7.9"}
    params["sign"] = sign_cls(params)
    data = requests.get(
        "https://www.cls.cn/v1/roll/get_roll_list",
        params=params,
        headers={**REQUEST_HEADERS, "Referer": "https://www.cls.cn/telegraph", "Accept": "application/json,text/plain,*/*"},
        timeout=12,
    ).json()
    rows = data.get("data", {}).get("roll_data", [])
    return [
        make_item(
            source,
            row.get("title") or limit_text(row.get("content", ""), 80),
            row.get("content") or row.get("brief") or row.get("title", ""),
            f"https://www.cls.cn/detail/{row.get('id')}" if row.get("id") else "",
            datetime.fromtimestamp(int(row.get("ctime", time.time())), CN_TZ).isoformat(),
            [stock.get("name") or stock.get("code") for stock in row.get("stock_list", []) if isinstance(stock, dict)],
        )
        for row in rows[:limit]
        if row.get("title") or row.get("content")
    ]


def fetch_jiuyangongshe(limit: int = 35) -> list[dict[str, Any]]:
    source = Source("jiuyangongshe", "韭研公社", "国内", "小作文", 56)
    soup = BeautifulSoup(fetch_text("https://www.jiuyangongshe.com/study_publish", headers={"Referer": "https://www.jiuyangongshe.com/"}), "html.parser")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for li in soup.find_all("li"):
        link = li.find("a", href=re.compile(r"^/a/"))
        time_node = li.find(class_="fs13-ash")
        if not link or not time_node:
            continue
        article_id = link["href"].split("/")[-1]
        if article_id in seen:
            continue
        title_node = li.find(class_=re.compile(r"book-title"))
        summary = clean_text(link.get_text(" "))
        title = clean_text(title_node.get_text(" ") if title_node else summary)
        stocks = [clean_text(node.get_text(" ")) for node in li.select(".source-box a.text")][:12]
        if len(summary) < 8:
            continue
        seen.add(article_id)
        items.append(
            make_item(
                source,
                limit_text(title, 96),
                limit_text(summary, 280),
                f"https://www.jiuyangongshe.com/a/{article_id}",
                parse_cn_time(time_node.get_text(" ")),
                stocks,
            )
        )
        if len(items) >= limit:
            break
    return items


def fetch_rss(source: Source, url: str, limit: int = 20) -> list[dict[str, Any]]:
    xml = fetch_text(url)
    rows = re.findall(r"<item\b[\s\S]*?</item>", xml, flags=re.I)
    items = []
    for idx, block in enumerate(rows[:limit]):
        def tag(name: str) -> str:
            match = re.search(fr"<{name}(?:\s[^>]*)?>([\s\S]*?)</{name}>", block, re.I)
            return clean_text(match.group(1)) if match else ""
        title = tag("title")
        link = tag("link")
        summary = tag("description")
        pub = tag("pubDate")
        try:
            item_time = parsedate_to_datetime(pub).astimezone(CN_TZ).isoformat() if pub else infer_time_from_url(link, idx)
        except Exception:
            item_time = infer_time_from_url(link, idx)
        if title:
            items.append(make_item(source, title, summary or title, link, item_time))
    return items


SOURCE_FETCHERS = [
    ("东方财富快讯", lambda: fetch_eastmoney_flash(80)),
    ("东方财富财经长文", lambda: fetch_eastmoney_articles(24)),
    ("新浪财经滚动", lambda: fetch_sina_roll(40)),
    ("同花顺股票", lambda: fetch_html_list(Source("ths_stock", "同花顺股票", "国内", "大作文", 73), "https://stock.10jqka.com.cn/", "https://stock.10jqka.com.cn", r"10jqka\.com\.cn", 30, "gbk")),
    ("证券时报快讯", lambda: fetch_stcn_flash(40)),
    ("证券时报要闻", lambda: fetch_html_list(Source("stcn_yw", "证券时报要闻", "国内", "大作文", 80), "https://www.stcn.com/article/list/yw.html", "https://www.stcn.com", r"stcn\.com/article/detail", 24)),
    ("财联社电报", lambda: fetch_cls_telegraph(50)),
    ("韭研公社", lambda: fetch_jiuyangongshe(35)),
    ("中国证券网", lambda: fetch_html_list(Source("cnstock", "中国证券网", "国内", "大作文", 80), "https://www.cnstock.com/", "https://www.cnstock.com", r"cnstock\.com", 24)),
    ("21财经", lambda: fetch_html_list(Source("twentyone_finance", "21财经", "国内", "大作文", 77), "https://www.21jingji.com/", "https://www.21jingji.com", r"21jingji\.com/article", 24)),
    ("CNBC", lambda: fetch_rss(Source("cnbc", "CNBC", "海外", "快讯", 78), "https://www.cnbc.com/id/100003114/device/rss/rss.html", 20)),
    ("MarketWatch", lambda: fetch_rss(Source("marketwatch", "MarketWatch", "海外", "大作文", 76), "https://feeds.content.dowjones.io/public/rss/mw_topstories", 10)),
    ("TechCrunch", lambda: fetch_rss(Source("techcrunch", "TechCrunch", "海外", "大作文", 70), "https://techcrunch.com/feed/", 20)),
    ("Yahoo Finance", lambda: fetch_rss(Source("yahoo_finance", "Yahoo Finance", "海外", "快讯", 72), "https://finance.yahoo.com/news/rssindex", 20)),
]


def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        key = re.sub(r"\W+", "", item["title"].lower())[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def diversify(items: list[dict[str, Any]], max_consecutive: int = 2) -> list[dict[str, Any]]:
    queue = list(items)
    result = []
    while queue:
        blocked = ""
        tail = result[-max_consecutive:]
        if len(tail) == max_consecutive and all(row["source_key"] == tail[0]["source_key"] for row in tail):
            blocked = tail[0]["source_key"]
        idx = next((i for i, row in enumerate(queue) if row["source_key"] != blocked), 0)
        result.append(queue.pop(idx))
    return result


@st.cache_data(ttl=15, show_spinner=False)
def load_news(limit: int = 260) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    items: list[dict[str, Any]] = []
    health = []
    for name, fetcher in SOURCE_FETCHERS:
        try:
            rows = fetcher()
            health.append({"name": name, "ok": True, "count": len(rows), "error": ""})
            items.extend(rows)
        except Exception as exc:
            health.append({"name": name, "ok": False, "count": 0, "error": str(exc)[:120]})
    items = dedupe(items)
    items.sort(key=lambda row: row["time"], reverse=True)
    return diversify(items)[:limit], health, now_iso()


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.2rem; max-width: 1400px; }
        .metric-card { padding: 14px 16px; border: 1px solid #d9e2ef; border-radius: 8px; background: #fff; }
        .news-card { display: grid; grid-template-columns: 58px 1fr; gap: 14px; padding: 14px; border: 1px solid #d9e2ef; border-radius: 8px; background: #fff; margin-bottom: 10px; }
        .score { width: 50px; height: 50px; border-radius: 50%; display: grid; place-items: center; font-weight: 800; color: #0f2d5c; background: conic-gradient(#2563eb calc(var(--s) * 1%), #e5edf8 0); }
        .title { font-size: 1.02rem; font-weight: 800; color: #082044; margin-bottom: 4px; }
        .meta { color: #6b7890; font-size: 0.82rem; margin-bottom: 4px; }
        .summary { color: #40516e; font-size: 0.92rem; margin-bottom: 8px; }
        .tag { display: inline-block; padding: 2px 8px; border: 1px solid #cfe0ff; border-radius: 999px; margin-right: 6px; margin-bottom: 4px; font-size: 0.78rem; color: #164fa3; background: #f5f8ff; }
        .tag.warn { border-color: #f0d3a5; color: #9a5b00; background: #fff8ed; }
        .tag.bad { border-color: #bdebdc; color: #047857; background: #eefcf7; }
        .health-ok { color: #047857; font-weight: 700; }
        .health-bad { color: #b91c1c; font-weight: 700; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def check_access() -> bool:
    code = st.secrets.get("ACCESS_CODE", "")
    if not code:
        return True
    if st.session_state.get("authed"):
        return True
    st.title("资讯影响监控台")
    password = st.text_input("访问码", type="password")
    if st.button("进入", type="primary") and password == code:
        st.session_state.authed = True
        st.rerun()
    return False


def format_time(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value).astimezone(CN_TZ)
        return dt.strftime("%m/%d %H:%M:%S")
    except Exception:
        return "--"


def render_item(item: dict[str, Any]) -> None:
    analysis = item["analysis"]
    tags = [
        f'<span class="tag">{item["region"]}</span>',
        f'<span class="tag">{item["category"]}</span>',
        f'<span class="tag {"bad" if analysis["direction"] == "利空" else ""}">{analysis["direction"]}</span>',
        f'<span class="tag {"warn" if item["category"] == "小作文" else ""}">{analysis["level"]}影响</span>',
    ]
    tags += [f'<span class="tag">{sector}</span>' for sector in analysis["sectors"][:3]]
    stock_text = " ".join(f'<span class="tag">{stock}</span>' for stock in item.get("stocks", [])[:6])
    url = item.get("url")
    link = f' · <a href="{url}" target="_blank">原文</a>' if url else ""
    st.markdown(
        f"""
        <div class="news-card">
          <div class="score" style="--s:{analysis["score"]}">{analysis["score"]}</div>
          <div>
            <div class="meta">{item["source"]} · {format_time(item["time"])} · 可信 {item["credibility"]}% · 置信 {analysis["confidence"]}%{link}</div>
            <div class="title">{html.escape(item["title"])}</div>
            <div class="summary">{html.escape(item["summary"])}</div>
            <div>{''.join(tags)} {stock_text}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="资讯影响监控台", page_icon="📈", layout="wide")
    inject_css()
    if not check_access():
        return

    st.title("资讯影响监控台")
    st.caption("国内外多源聚合，快讯、大作文、小作文分层分析。仅作盯盘辅助，不构成投资建议。")

    with st.sidebar:
        st.subheader("刷新")
        auto_refresh = st.toggle("自动刷新", value=True)
        interval = st.selectbox("间隔", [15, 30, 60, 120], index=0, format_func=lambda x: f"{x} 秒" if x < 120 else "2 分钟")
        if auto_refresh and st_autorefresh:
            st_autorefresh(interval=interval * 1000, key="news_refresh")
        if st.button("立即刷新", type="primary"):
            st.cache_data.clear()
            st.rerun()

        st.subheader("筛选")
        query = st.text_input("关键词/板块/股票")
        region_filter = st.selectbox("地区", ["全部", "国内", "海外"])
        category_filter = st.selectbox("类型", ["全部", "快讯", "大作文", "小作文"])
        level_filter = st.selectbox("影响", ["全部", "高", "中", "低"])
        direction_filter = st.selectbox("方向", ["全部", "利好", "利空", "多空混合", "中性"])

    items, health, fetched_at = load_news(260)
    source_options = ["全部"] + sorted({item["source"] for item in items})
    with st.sidebar:
        source_filter = st.selectbox("来源", source_options)

    filtered = []
    for item in items:
        haystack = " ".join([
            item["title"], item["summary"], item["source"], item["region"], item["category"],
            " ".join(item.get("stocks", [])), " ".join(item["analysis"]["sectors"]),
        ]).lower()
        if query and query.lower() not in haystack:
            continue
        if region_filter != "全部" and item["region"] != region_filter:
            continue
        if category_filter != "全部" and item["category"] != category_filter:
            continue
        if source_filter != "全部" and item["source"] != source_filter:
            continue
        if level_filter != "全部" and item["analysis"]["level"] != level_filter:
            continue
        if direction_filter != "全部" and item["analysis"]["direction"] != direction_filter:
            continue
        filtered.append(item)

    ok_sources = sum(1 for item in health if item["ok"])
    high_count = sum(1 for item in items if item["analysis"]["level"] == "高")
    rumor_count = sum(1 for item in items if item["category"] == "小作文")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("来源", f"{ok_sources}/{len(health)}")
    c2.metric("资讯", len(items))
    c3.metric("高影响", high_count)
    c4.metric("小作文", rumor_count)
    st.caption(f"最后更新：{format_time(fetched_at)}")

    tab_feed, tab_watch, tab_sources, tab_rumor = st.tabs(["多源资讯流", "重点盯盘", "来源状态", "手动小作文"])

    with tab_feed:
        st.write(f"当前筛选：{len(filtered)} 条")
        for item in filtered[:120]:
            render_item(item)

    with tab_watch:
        for item in sorted(items, key=lambda row: row["analysis"]["score"], reverse=True)[:30]:
            render_item(item)

    with tab_sources:
        for row in health:
            status = "正常" if row["ok"] else "失败"
            cls = "health-ok" if row["ok"] else "health-bad"
            st.markdown(
                f'<div class="metric-card"><span class="{cls}">{status}</span> · {row["name"]} · {row["count"]} 条'
                + (f'<br><small>{html.escape(row["error"])}</small>' if row["error"] else "")
                + "</div>",
                unsafe_allow_html=True,
            )

    with tab_rumor:
        text = st.text_area("粘贴群聊、路演、朋友圈、传闻文字", height=150)
        source_name = st.text_input("来源备注", value="手动小作文")
        if st.button("加入分析"):
            if text.strip():
                source = Source("manual_rumor", source_name or "手动小作文", "国内", "小作文", 30)
                st.session_state.setdefault("manual_items", [])
                st.session_state.manual_items.insert(0, make_item(source, limit_text(text, 80), text, "", now_iso()))
        for item in st.session_state.get("manual_items", []):
            render_item(item)


if __name__ == "__main__":
    main()
