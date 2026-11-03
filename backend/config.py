import os

from dotenv import load_dotenv

load_dotenv()

# Web服务Key，见 https://console.amap.com -> 应用管理 -> Key
AMAP_WEB_KEY = os.getenv("AMAP_WEB_KEY", "")

# 智谱开放平台Key，见 https://open.bigmodel.cn -> API密钥（提取城市养宠数据用）
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "")

# 博查搜索Key，见 https://open.bochaai.com（联网搜索城市养宠数据用，中文内容覆盖好，推荐）
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY", "")

# Tavily搜索Key，见 https://app.tavily.com（备用搜索源，每月1000次免费，中文覆盖一般）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

REST_BASE = "https://restapi.amap.com"

# 宠物相关POI类型码（来源：高德POI分类与编码表 V1.06）
PET_TYPES = {
    "061211": "宠物店",
    "060502": "宠物市场",
    "090700": "宠物医疗",
}
