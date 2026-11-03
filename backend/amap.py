"""高德 Web 服务 API 的薄封装：行政区划树 + POI 搜索，带进程内 TTL 缓存。

缓存的目的：免费配额有限，同样的区域/条件短时间内不重复计费。
"""

import time

import requests

import config

_CACHE = {}  # key -> (expire_ts, value)
REGIONS_TTL = 24 * 3600  # 行政区划变动极少，缓存一天
STORES_TTL = 3600  # 店铺数据缓存一小时

# 高德风控会误伤 python-requests 这类脚本 UA（报 CUQPS_HAS_EXCEEDED_THE_LIMIT），
# 带浏览器 UA 可避开；免费共享 QPS 池偶发瞬时限流，用重试兜底。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}
QPS_RETRY_CODES = {"10021"}  # CUQPS_HAS_EXCEEDED_THE_LIMIT


class AmapError(Exception):
    """高德接口调用失败（网络错误或业务错误）。"""


def _cached(key, ttl, builder):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = builder()
    _CACHE[key] = (now + ttl, value)
    return value


def _get(path, params):
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(
                f"{config.REST_BASE}{path}",
                params={**params, "key": config.AMAP_WEB_KEY},
                headers=HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise AmapError(f"请求高德接口失败：{exc}") from exc
        if data.get("status") == "1":
            return data
        last_error = AmapError(
            f"高德接口返回错误：{data.get('info')}（infocode={data.get('infocode')}）"
        )
        if data.get("infocode") in QPS_RETRY_CODES and attempt < 4:
            time.sleep((1, 2, 4, 6)[attempt])
            continue
        raise last_error
    raise last_error


# ---------- 行政区划 ----------

def get_regions():
    """全国省->市->区 三级树，节点：{name, adcode, children}。"""
    return _cached("regions", REGIONS_TTL, _build_regions)


def _build_regions():
    data = _get(
        "/v3/config/district",
        {"keywords": "中国", "subdistrict": 3, "extensions": "base"},
    )
    country = data["districts"][0]
    return [_node(p) for p in country.get("districts", [])]


def _node(district):
    return {
        "name": district["name"],
        "adcode": district["adcode"],
        "children": [_node(c) for c in district.get("districts", [])],
    }


# ---------- POI 搜索 ----------

def get_pet_types():
    return config.PET_TYPES


# ---------- 市场分析 ----------

def get_district_children(adcode):
    """返回 (区域名, 下一级子区列表)；区县无子区时子区为它自身。"""
    def find(nodes):
        for node in nodes:
            if node["adcode"] == adcode:
                return node
            found = find(node["children"])
            if found:
                return found
        return None

    node = find(get_regions())
    if node is None:
        return None, None
    children = [{"name": c["name"], "adcode": c["adcode"]} for c in node["children"]]
    if children:
        return node["name"], children
    return node["name"], [{"name": node["name"], "adcode": node["adcode"]}]


def search_stores(adcode, keywords="", types=None, max_pages=10):
    """按区域搜宠物店铺。有关键词时按关键词搜，否则按类型码搜。

    高德规则：keyword 与 types 同时传入时 keyword 优先，所以二者互斥。
    返回 {"total": 符合条件的POI总数(高德count字段，不受翻页上限影响), "stores": [...]}
    """
    keywords = (keywords or "").strip()
    types = list(types) if types else list(config.PET_TYPES)
    return _cached(
        ("stores", adcode, keywords, "|".join(types)),
        STORES_TTL,
        lambda: _fetch_stores(adcode, keywords, types, max_pages),
    )


def _fetch_stores(adcode, keywords, types, max_pages):
    base = {
        "region": adcode,
        "city_limit": "true",
        "page_size": 25,  # 接口单页上限
        "show_fields": "business",  # 电话在 business 里，需显式请求
    }
    if keywords:
        base["keywords"] = keywords
    else:
        base["types"] = "|".join(types)

    pois = {}
    data = {}
    for page_num in range(1, max_pages + 1):
        data = _get("/v5/place/text", {**base, "page_num": page_num})
        page_pois = data.get("pois", [])
        for poi in page_pois:
            pois[poi["id"]] = poi
        if len(page_pois) < 25:
            break
    stores = [_normalize(p) for p in pois.values()]
    # count 是高德返回的符合条件的POI总数；但关键词搜索时该字段偏小不可靠，
    # 所以与实际拉取数取大者（翻页自然结束时拉取数即完整数量）
    try:
        total = int(data.get("count") or 0)
    except (TypeError, ValueError):
        total = 0
    return {"total": max(total, len(stores)), "stores": stores}


def _normalize(poi):
    lng, lat = poi["location"].split(",")
    business = poi.get("business") or {}
    return {
        "id": poi["id"],
        "name": poi["name"],
        "address": poi.get("address") or "",
        "lng": float(lng),
        "lat": float(lat),
        "tel": business.get("tel") or "",
        "rating": _to_float(business.get("rating")),
        "cost": _to_float(business.get("cost")),
        "opentime_today": business.get("opentime_today") or "",
        "typecode": poi.get("typecode") or "",
        "type_label": _type_label(poi.get("typecode") or ""),
    }


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _type_label(typecode):
    if typecode == "061211":
        return "宠物店"
    if typecode == "060502":
        return "宠物市场"
    if typecode.startswith("0907"):  # 090701 宠物诊所 / 090702 兽医站
        return "宠物医疗"
    return "其他"
