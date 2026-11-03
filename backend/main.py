import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import amap
import config
import search
import zhipu

app = FastAPI(title="petSerch API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本地开发工具，放开跨域
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_key():
    if not config.AMAP_WEB_KEY:
        raise HTTPException(
            status_code=500,
            detail="未配置高德Key：请在 backend/.env 中设置 AMAP_WEB_KEY 后重启服务",
        )


@app.get("/api/health")
def health():
    return {"ok": True, "key_configured": bool(config.AMAP_WEB_KEY)}


@app.get("/api/pet-types")
def pet_types():
    return amap.get_pet_types()


@app.get("/api/regions")
def regions():
    _require_key()
    try:
        return amap.get_regions()
    except amap.AmapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/stores")
def stores(
    adcode: str = Query(..., description="行政区划编码，取省/市/区中最深一级"),
    keywords: str = "",
    types: str = Query("", description="逗号分隔的类型码，留空用默认宠物类型"),
):
    _require_key()
    type_list = [t.strip() for t in types.split(",") if t.strip()] or None
    try:
        return amap.search_stores(adcode, keywords, type_list)
    except amap.AmapError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/partner-stores")
def partner_stores():
    """加盟门店名单（数据来自 import_stores.py 导入，与地图无关）。"""
    conn = _db()
    rows = conn.execute(
        "SELECT id, name, type, address, created_at FROM partner_store ORDER BY id"
    ).fetchall()
    conn.close()
    return {
        "total": len(rows),
        "stores": [
            {"id": r[0], "name": r[1], "type": r[2], "address": r[3], "created_at": r[4]}
            for r in rows
        ],
    }


# ---------- 城市养宠市场数据（智谱联网搜索 + LLM提取） ----------

class MarketBody(BaseModel):
    adcode: str


@app.get("/api/city-market")
def city_market_get(adcode: str = Query(..., description="城市adcode")):
    conn = _db()
    row = conn.execute(
        "SELECT city_name, data_json, fetched_at FROM city_market WHERE adcode=?",
        (adcode,),
    ).fetchone()
    conn.close()
    if not row:
        return {"exists": False}
    return {
        "exists": True,
        "city": row[0],
        "data": json.loads(row[1]),
        "fetched_at": row[2],
    }


@app.post("/api/city-market/fetch")
def city_market_fetch(body: MarketBody):
    # 搜索源在 search.py 里按 ZHIPU > BOCHA > TAVILY 自动选择，缺Key会报友好错误
    city_name, _ = amap.get_district_children(body.adcode)
    if city_name is None:
        raise HTTPException(status_code=400, detail=f"行政区划 {body.adcode} 不存在")
    try:
        data = zhipu.extract_city_market(city_name)
    except (zhipu.ZhipuError, search.SearchError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    saved = bool(data.get("pet_population") or data.get("households"))
    if saved:  # 没提取到数据的market不入库，方便之后重试
        _save_market(body.adcode, city_name, data, fetched_at)
    return {
        "city": city_name,
        "data": data,
        "fetched_at": fetched_at,
        "saved": saved,
    }


def _save_market(adcode, city_name, data, fetched_at):
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO city_market VALUES (?,?,?,?)",
        (adcode, city_name, json.dumps(data, ensure_ascii=False), fetched_at),
    )
    conn.commit()
    conn.close()


# ---------- 市场分析（异步任务，逐区拉取店铺+住宅，聚合榜单与热力点） ----------

_JOBS = {}  # job_id -> job dict
_FETCH_WORKERS = 2  # 并发2个请求，与免费QPS配额匹配，过高会持续触发限流

# 分析结果持久化：启动先查库，命中秒回；「重新分析」强制重拉并更新库
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "petserch.db")


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS analysis_result (
            adcode TEXT PRIMARY KEY,
            city_name TEXT,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    # 加盟门店：平台自己的名单，与高德POI检索无关
    conn.execute(
        """CREATE TABLE IF NOT EXISTS partner_store (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT DEFAULT '',
            address TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    # 城市养宠市场数据：智谱联网搜索提取的结果（宠物保有量/养宠家庭）
    conn.execute(
        """CREATE TABLE IF NOT EXISTS city_market (
            adcode TEXT PRIMARY KEY,
            city_name TEXT,
            data_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )"""
    )
    return conn


def _load_analysis(adcode):
    row = _db().execute(
        "SELECT result_json, created_at FROM analysis_result WHERE adcode=?",
        (adcode,),
    ).fetchone()
    if not row:
        return None
    return json.loads(row[0]), row[1]


def _save_analysis(adcode, city_name, result):
    """入库策略：完整结果直接写；残缺结果只在库里还没有该区域时才写（不让残缺覆盖完整）。"""
    if result.get("failed") and _load_analysis(adcode):
        return
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO analysis_result VALUES (?,?,?,?)",
        (
            adcode,
            city_name,
            json.dumps(result, ensure_ascii=False),
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        ),
    )
    conn.commit()
    conn.close()


class AnalysisBody(BaseModel):
    adcode: str
    force: bool = False  # true=跳过数据库缓存，强制重新拉取


@app.post("/api/analysis")
def start_analysis(body: AnalysisBody):
    _require_key()
    if not body.force:
        hit = _load_analysis(body.adcode)
        if hit:
            return {
                "status": "done",
                "result": hit[0],
                "from_db": True,
                "analyzed_at": hit[1],
            }
    # 同一区域已有任务在跑，直接复用
    for job in _JOBS.values():
        if job["adcode"] == body.adcode and job["status"] == "running":
            return {"status": "running", "job_id": job["id"]}
    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {
        "id": job_id,
        "adcode": body.adcode,
        "status": "running",
        "progress": "准备中…",
        "result": None,
        "error": None,
    }
    threading.Thread(target=_run_analysis, args=(job_id, body.adcode), daemon=True).start()
    return {"status": "running", "job_id": job_id}


@app.get("/api/analysis-cache")
def analysis_cache(adcode: str = Query(..., description="城市adcode")):
    """轻量查库接口：切换城市时前端先调它，命中缓存就直接展示，不触发重新拉取。"""
    hit = _load_analysis(adcode)
    if not hit:
        return {"exists": False}
    return {"exists": True, "result": hit[0], "analyzed_at": hit[1]}


@app.get("/api/analysis/{job_id}")
def analysis_status(job_id: str):
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    payload = {"status": job["status"], "progress": job["progress"], "error": job["error"]}
    if job["status"] == "done":
        payload["result"] = job["result"]
    return payload


def _run_analysis(job_id, adcode):
    job = _JOBS[job_id]
    try:
        city_name, districts = amap.get_district_children(adcode)
        if districts is None:
            raise ValueError(f"行政区划 {adcode} 不存在")
        stats = {
            d["adcode"]: {
                "adcode": d["adcode"],
                "name": d["name"],
                "store_count": 0,
                "rating_sum": 0.0,
                "rating_n": 0,
                "open24": 0,
            }
            for d in districts
        }
        store_points = []

        def fetch(district, kind):
            return amap.search_stores(district["adcode"], max_pages=20)

        tasks = [(d, "stores") for d in districts]
        name_map = {d["adcode"]: d["name"] for d in districts}

        def run_pending(pending):
            """跑一批子任务，返回 {(adcode, kind): 结果或None}，None=失败。"""
            results = {}
            with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
                futures = {
                    pool.submit(fetch, d, k): (d["adcode"], k) for d, k in pending
                }
                done = 0
                for fut in as_completed(futures):
                    ad, kind = futures[fut]
                    done += 1
                    kind_name = "宠物店" if kind == "stores" else "住宅小区"
                    job["progress"] = f"{name_map[ad]}·{kind_name}（{done}/{len(pending)}）"
                    try:
                        results[(ad, kind)] = fut.result()
                    except Exception:
                        results[(ad, kind)] = None
            return results

        results = run_pending(tasks)
        failed_tasks = [t for t in tasks if results.get((t[0]["adcode"], t[1])) is None]
        if failed_tasks:
            # 限流是瞬时的，歇几秒后对失败项补拉一轮
            job["progress"] = f"{len(failed_tasks)} 个子查询被限流，3秒后补拉…"
            time.sleep(3)
            results.update(run_pending(failed_tasks))

        failures = 0
        for district, kind in tasks:
            res = results.get((district["adcode"], kind))
            if res is None:
                failures += 1  # 单区失败不拖垮整体，结果里会标注
                continue
            items = res["stores"] if isinstance(res, dict) else res
            stat = stats[district["adcode"]]
            for item in items:
                store_points.append(
                    {"lng": item["lng"], "lat": item["lat"], "rating": item["rating"]}
                )
                stat["store_count"] += 1
                if item["rating"] is not None:
                    stat["rating_sum"] += item["rating"]
                    stat["rating_n"] += 1
                if "24" in (item.get("opentime_today") or ""):
                    stat["open24"] += 1

        rows = []
        for st in stats.values():
            rows.append(
                {
                    "adcode": st["adcode"],
                    "name": st["name"],
                    "store_count": st["store_count"],
                    "avg_rating": round(st["rating_sum"] / st["rating_n"], 2) if st["rating_n"] else None,
                    "open24": st["open24"],
                }
            )
        result = {
            "city": city_name,
            "districts": rows,
            "stores": store_points,
            "failed": failures,
        }
        job["status"] = "done"
        job["result"] = result
        _save_analysis(adcode, city_name, result)
    except Exception as exc:  # 线程内异常要落到任务状态里，前端才能看到
        job["status"] = "error"
        job["error"] = str(exc)
