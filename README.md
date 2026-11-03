# petSerch — 宠物店地图查询

Python (FastAPI) + Vue3 + 高德地图。按省/市/区筛选宠物店、宠物市场、宠物医院，列表 + 地图展示，并显示店铺离你的距离。

## 目录结构

```
petSerch/
├── backend/          FastAPI 后端：高德接口代理 + 内存缓存
│   ├── main.py       接口：/api/regions（省市区树）、/api/stores（店铺检索）
│   ├── amap.py       高德API封装（行政区划 + POI搜索2.0）
│   └── config.py     配置（读 .env）
└── frontend/         Vue3 + Vite 前端：高德JS API地图 + 筛选 + 列表
```

## 准备：两个高德 Key

到 [高德开放平台控制台](https://console.amap.com) 创建应用，申请两个 Key（类型不同，勿混用）：

| Key | 用途 | 配置位置 |
|---|---|---|
| **Web 服务** | 后端调 POI 搜索 / 行政区划接口 | `backend/.env` → `AMAP_WEB_KEY` |
| **Web端 (JS API)** + 安全密钥 | 前端渲染地图 | `frontend/.env` → `VITE_AMAP_KEY`、`VITE_AMAP_SECURITY_CODE` |
| **智谱开放平台** | 联网搜索城市养宠数据（**首选**，需在控制台充值/开通「联网搜索」资源包） | `backend/.env` → `ZHIPU_API_KEY` |
| **博查**（备用） | 中文搜索源，智谱不可用时自动切换 | `backend/.env` → `BOCHA_API_KEY` |
| **Tavily**（备用） | 免费搜索源（每月1000次，中文地方内容覆盖弱） | `backend/.env` → `TAVILY_API_KEY` |

两个目录下都有 `.env.example`，复制为 `.env` 并填入对应 Key（`.env` 已被 .gitignore 忽略，不会提交）。

## 启动

### 后端（端口 8000）

```bash
cd backend
python -m venv venv
venv\Scripts\activate            # Git Bash: source venv/Scripts/activate
pip install -r requirements.txt
# 如果直连 PyPI 超时，改用清华镜像：
# pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
copy .env.example .env           # 填入 AMAP_WEB_KEY
uvicorn main:app --reload --port 8000
```

### 前端（端口 5173）

```bash
cd frontend
npm install
copy .env.example .env           # 填入 VITE_AMAP_KEY 和 VITE_AMAP_SECURITY_CODE
npm run dev
```

浏览器打开 http://localhost:5173 ，选择省市区后点「查询」。

## 设计说明

- **两种模式**：「门店查询」按区域检索店铺列表；「市场分析」面向招商——按城市逐区统计宠物店与住宅小区，生成密度热力图与分区榜单，给代理商看市场。
- **市场分析的三种视图**：店铺热力（供给侧，客户池在哪）、住宅热力（需求底盘）、供需比（约1km网格的住宅数÷店铺数，比值越高越接近"住宅多店少"的成长型区域）。榜单指标：店铺数、住宅小区数、住宅÷店比、平均评分、24h营业数。
- **市场分析是异步任务 + SQLite 持久化**：首次分析一个市需逐区拉取约200次请求（并发2、限流自动重试+补拉一轮），约1-3分钟；完成后结果存入 `backend/petserch.db`（SQLite，零安装），之后再点「开始分析」直接秒回库里的结果，榜单上会标注数据来源和分析时间。要更新数据点「🔄 重新分析」强制重拉。单区失败会标注且不会覆盖库里已有的完整数据。
- **数据实时检索，不落库**：单区查询由后端实时调高德 POI 搜索（搜索服务 2.0 `/v5/place/text`），带内存缓存（行政区划 24h、店铺结果 1h）。
- **POI 类型**：宠物店 `061211` / 宠物市场 `060502` / 宠物医疗 `090700`；住宅小区 `120302`。关键词与类型互斥——高德规则是两者同传时关键词优先，所以填了关键词就按关键词搜。
- **距离**：用高德 `AMap.Geolocation` 定位（返回 GCJ-02 坐标，与店铺坐标系一致），前端球面距离计算，按距离排序。定位失败不影响查询，只是不显示距离。
- **翻页**：单区查询最多 10 页 × 25 条 = 250 条；市场分析每子查询最多 20 页。
- **城市养宠市场数据**：市场分析页展示各城市的宠物保有量、养宠家庭数（Tavily 联网搜索 + 智谱 glm-4-flash 严格提取，来源分官方登记/白皮书/媒体三级），并派生"每万户养宠家庭宠物店数"跨城市可比指标。数据存 `city_market` 表，提取不到的不入库以便重试；需配置 `TAVILY_API_KEY` 和 `ZHIPU_API_KEY`。
- **加盟门店**：与地图检索彻底解耦的平台自有名单，第三个 Tab 一店一卡片展示，数据用 `backend/import_stores.py` 从 Excel/CSV 导入（列头模糊匹配，默认全量替换、`--append` 追加）。
- **数据口径（给招商演示时要诚实说明）**：高德对类型检索的召回有上限，实测住宅小区单区召回约 225 条封顶、部分区店铺数也会贴近 250 上限，因此区县数字是"下限"而非精确值；住宅点不含户数；高德也看不到竞品平台渗透率。方向性判断够用，精确市场数据需另接统计口径。
- 已知边界：东莞、中山等无区县建制的市，区下拉为空，直接按市级查询即可。

## 常见问题

- **提示 INVALID_USER_KEY**：`backend/.env` 里填的必须是「Web服务」类型 Key，不是 JS API Key。
- **地图白屏 / 加载失败**：检查 `frontend/.env` 的 JS API Key 与安全密钥是否成对填写，且 Key 绑定的域名白名单包含 `localhost`。
- **配额超限（CUQPS_HAS_EXCEEDED_THE_LIMIT 等）**：免费配额有限，等次日恢复或升级企业认证。注意：高德风控会对 `python-requests` 脚本 UA 误报 QPS 超限（浏览器/curl 同参数却正常），后端已内置浏览器 UA + 自动重试规避。
