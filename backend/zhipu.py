"""城市养宠市场数据提取（正则方案：确定性、零幻觉）。

搜索交给 search.py；这里用正则从搜索结果里抓"XX万只 / X亿只 / XX万户"
格式的数字，按规则取最大值：
- 优先取上下文带城市名的数字
- 排除"全国/中国/全球"口径的数字（全国数套在城市头上会离谱）
- 超过合理性上限的数字不采用
材料里实在没有合适数字就返回空，界面显示"暂无公开数据 + 重试"。
"""

import re

import search

POP_CAP = 2000_0000  # 单个城市宠物保有量合理性上限：2000万只
HH_CAP = 1500_0000  # 养宠家庭数合理性上限：1500万户
NATIONAL_WORDS = ("全国", "中国", "我国", "全球")
NUM_FACTOR = {"亿": 1e8, "万": 1e4, "": 1}

POP_PATTERNS = [
    re.compile(r"(?:宠物|犬|猫|狗|猫狗)[^。\n；;]{0,40}?(\d+(?:\.\d+)?)(亿|万)?只"),
    re.compile(r"(\d+(?:\.\d+)?)(亿|万)?只"),
]
HH_PATTERNS = [
    re.compile(r"(?:养宠|养犬|养猫|养狗|宠物)[^。\n；;]{0,40}?(\d+(?:\.\d+)?)(亿|万)?户"),
    re.compile(r"(\d+(?:\.\d+)?)\s*(亿|万)?户"),
]


class ZhipuError(Exception):
    """兼容旧接口名的提取错误。"""


def extract_city_market(city_name):
    """搜索该市养宠数据并用正则取最大参考值，返回含来源的结构化结果。"""
    results = search.web_search(f"{city_name} 宠物保有量 养宠家庭 数量")
    if not results:
        raise ZhipuError("联网搜索没有返回任何结果")

    hint = city_name.rstrip("市省")  # "杭州市" -> "杭州"
    pop = _regex_max(results, POP_PATTERNS, "只", hint, POP_CAP)
    hh = _regex_max(results, HH_PATTERNS, "户", hint, HH_CAP)
    if not pop and not hh:
        # 没有合适数字：返回空（不入库），界面显示"暂无公开数据 + 重试"
        return {
            "pet_population": None,
            "pet_population_text": "",
            "pet_population_unit": "",
            "households": None,
            "households_text": "",
            "households_unit": "",
            "year": None,
            "tier": "none",
            "conflict": "公开材料中没有该市口径的数字",
            "sources": results[:3],
        }

    src = pop or hh
    city_anchored = bool((pop and pop["city_hit"]) or (hh and hh["city_hit"]))
    tier = "official" if src["gov"] else "media"
    conflict = "政府公开数据" if src["gov"] else ("该市相关公开数据" if city_anchored else "非该市专项数据")
    conflict += "，仅作参考"
    conflict += f"（来源：{src['title'][:36]}）"
    return {
        "pet_population": pop["num"] if pop else None,
        "pet_population_text": pop["text"] if pop else "",
        "pet_population_unit": pop["unit"] if pop else "",
        "households": hh["num"] if hh else None,
        "households_text": hh["text"] if hh else "",
        "households_unit": hh["unit"] if hh else "",
        "year": None,
        "tier": tier,
        "conflict": conflict,
        "sources": results[:3],
    }


def _regex_max(results, patterns, unit_char, hint, cap):
    """抓取相关数字，按优先级取最大：城市相关 > 其他；全国口径和超上限的排除。"""
    best = None
    for r in results:
        pool = f"{r['title']}。{r['content']}"
        for pat in patterns:
            for m in pat.finditer(pool):
                ctx = pool[max(0, m.start() - 40): m.end() + 20]
                if any(word in ctx for word in NATIONAL_WORDS):
                    continue  # 全国口径的数字，排除
                num = float(m.group(1))
                unit = m.group(2) or ""
                value = num * NUM_FACTOR[unit]
                if value > cap:
                    continue  # 超出城市合理性上限
                cand = {
                    "score": 2 if hint in ctx else 1,
                    "value": value,
                    "num": num,
                    "unit": f"{unit}{unit_char}",
                    "text": f"约{m.group(1)}{unit}{unit_char}",
                    "title": r["title"],
                    "url": r["url"],
                    "gov": ".gov.cn" in r["url"],
                    "city_hit": hint in ctx,
                }
                if (
                    best is None
                    or cand["score"] > best["score"]
                    or (cand["score"] == best["score"] and value > best["value"])
                ):
                    best = cand
    return best
