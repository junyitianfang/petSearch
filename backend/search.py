"""联网搜索客户端：智谱（已充值联网搜索，首选）> 博查 > Tavily 自动选择。

backend/.env 里配了哪个 Key 就用哪个，按上面顺序取优先级最高的。
返回字段口径统一：[{title, url, content, media, publish_date}]。
"""

import time

import requests

import config

TAVILY_API = "https://api.tavily.com/search"
BOCHA_API = "https://api.bochaai.com/v1/web-search"
ZHIPU_API = "https://open.bigmodel.cn/api/paas/v4/web_search"


class SearchError(Exception):
    pass


def _provider():
    if config.ZHIPU_API_KEY:
        return "zhipu"
    if config.BOCHA_API_KEY:
        return "bocha"
    if config.TAVILY_API_KEY:
        return "tavily"
    raise SearchError(
        "未配置搜索Key：请在 backend/.env 设置 ZHIPU_API_KEY（需开通联网搜索资源包）、"
        "BOCHA_API_KEY 或 TAVILY_API_KEY 后重启服务"
    )


def web_search(query, count=8):
    provider = _provider()
    if provider == "zhipu":
        return _zhipu(query, count)
    if provider == "bocha":
        return _bocha(query, count)
    return _tavily(query, count)


def _zhipu(query, count):
    """智谱联网搜索：先试 search_std（便宜），余额不支持时退 search_pro。

    对限流做退避重试；余额类错误（1113）是确定性的，换引擎重试而不是原地等。
    """
    last_err = ""
    for engine in ("search_std", "search_pro"):
        for attempt in range(3):
            try:
                resp = requests.post(
                    ZHIPU_API,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {config.ZHIPU_API_KEY}",
                    },
                    json={"search_engine": engine, "search_query": query, "count": count},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as exc:
                resp = getattr(exc, "response", None)
                status = getattr(resp, "status_code", None)
                api_msg = ""
                if resp is not None:
                    try:
                        body = resp.json().get("error") or {}
                        api_msg = f"{body.get('code', '')} {body.get('message', '')}".strip()
                    except Exception:
                        api_msg = ""
                if "1113" in api_msg or "余额" in api_msg or "资源包" in api_msg:
                    last_err = f"{engine}: {api_msg or '余额不足或无可用资源包'}"
                    break  # 换下一个引擎
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep((3, 6)[attempt])
                    continue
                last_err = api_msg or str(exc)
                break
            items = data.get("search_result") or data.get("data") or []
            return [
                {
                    "title": it.get("title") or "",
                    "url": it.get("link") or it.get("url") or "",
                    "content": (it.get("content") or "")[:1200],
                    "media": it.get("media") or "",
                    "publish_date": it.get("publish_date") or "",
                }
                for it in items
            ]
    raise SearchError(f"智谱搜索失败：{last_err}（可到 open.bigmodel.cn 检查联网搜索资源包）")


def _tavily(query, count):
    try:
        resp = requests.post(
            TAVILY_API,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.TAVILY_API_KEY}",
            },
            json={"query": query, "search_depth": "basic", "max_results": count},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 401:
            raise SearchError("Tavily Key 无效（401）：请检查 TAVILY_API_KEY 是否填写正确") from exc
        if status in (429, 432, 433):
            raise SearchError(
                "Tavily 额度用尽或限流：免费档每月1000次，可到 app.tavily.com 查看用量"
            ) from exc
        raise SearchError(f"Tavily 搜索失败：{exc}") from exc
    return [
        {
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "content": (item.get("content") or "")[:1200],
            "media": "",
            "publish_date": item.get("published_date") or "",
        }
        for item in (data.get("results") or [])
    ]


def _bocha(query, count):
    try:
        resp = requests.post(
            BOCHA_API,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.BOCHA_API_KEY}",
            },
            json={"query": query, "count": count, "summary": True, "freshness": "noLimit"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        api_msg = ""
        if resp is not None:
            try:
                body = resp.json()
                api_msg = f"{body.get('code', '')} {body.get('message', '')}".strip()
            except Exception:
                api_msg = ""
        if status == 401 or "实名" in api_msg or "Key" in api_msg:
            raise SearchError(f"博查Key无效或未实名：{api_msg or '请到 open.bochaai.com 检查'}") from exc
        if "余额" in api_msg or "余额不足" in api_msg:
            raise SearchError(f"博查余额不足：{api_msg}（充值后即可恢复）") from exc
        raise SearchError(f"博查搜索失败：{api_msg or exc}") from exc
    pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
    return [
        {
            "title": p.get("name") or "",
            "url": p.get("url") or "",
            "content": (p.get("summary") or p.get("snippet") or "")[:1200],
            "media": p.get("siteName") or "",
            "publish_date": p.get("datePublished") or "",
        }
        for p in pages
    ]
