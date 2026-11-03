<script setup>
import AMapLoader from '@amap/amap-jsapi-loader'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  analysisStatus,
  checkAnalysisCache,
  checkCityMarket,
  fetchCityMarket,
  fetchPartnerStores,
  fetchRegions,
  fetchStores,
  startAnalysis,
} from './api'

const TYPE_OPTIONS = [
  { code: '061211', label: '宠物店' },
  { code: '060502', label: '宠物市场' },
  { code: '090700', label: '宠物医疗' },
]

const activeTab = ref('lookup')

// ==================== 门店查询 ====================
const regions = ref([])
const selProv = ref('')
const selCity = ref('')
const selDist = ref('')
const checkedTypes = ref(TYPE_OPTIONS.map((t) => t.code))
const keyword = ref('')
const stores = ref([])
const total = ref(null)
const myLoc = ref(null)
const sortBy = ref('dist')
const activeId = ref('')
const loading = ref(false)
const errorMsg = ref('')
const locateMsg = ref('')
const mapEl = ref(null)

const provList = computed(() => regions.value)
const cityList = computed(
  () => provList.value.find((p) => p.adcode === selProv.value)?.children ?? [],
)
const distList = computed(
  () => cityList.value.find((c) => c.adcode === selCity.value)?.children ?? [],
)

// 上级变化时重置下级选择
watch(selProv, () => {
  selCity.value = ''
  selDist.value = ''
})
watch(selCity, () => {
  selDist.value = ''
})

// 用选中的最深一级区域查询
const queryAdcode = computed(
  () => selDist.value || selCity.value || selProv.value,
)

// 距离优先：定位成功后由近到远；评分优先：分高在前，同分按距离
const sortedStores = computed(() => {
  const arr = [...stores.value]
  if (sortBy.value === 'rating') {
    arr.sort(
      (a, b) =>
        (b.rating ?? 0) - (a.rating ?? 0) ||
        (a.dist ?? Infinity) - (b.dist ?? Infinity),
    )
  } else if (myLoc.value) {
    arr.sort((a, b) => (a.dist ?? Infinity) - (b.dist ?? Infinity))
  }
  return arr
})

let AMap = null
let map = null
let markers = []
let meMarker = null
let infoWindow = null

onMounted(async () => {
  window._AMapSecurityConfig = {
    securityJsCode: import.meta.env.VITE_AMAP_SECURITY_CODE || '',
  }
  try {
    AMap = await AMapLoader.load({
      key: import.meta.env.VITE_AMAP_KEY || '',
      version: '2.0',
      plugins: ['AMap.Geolocation', 'AMap.HeatMap'],
    })
  } catch {
    errorMsg.value = '高德JS API加载失败，请检查 frontend/.env 中的 VITE_AMAP_KEY 配置'
    return
  }
  map = new AMap.Map(mapEl.value, { zoom: 11 })
  infoWindow = new AMap.InfoWindow()
  locate()
  try {
    regions.value = await fetchRegions()
  } catch (e) {
    errorMsg.value = `行政区划加载失败：${e.message}`
  }
  try {
    partnerStores.value = (await fetchPartnerStores()).stores
  } catch {
    // 加载失败不阻塞主流程，切到加盟门店页时展示为空态
  }
})

onBeforeUnmount(() => {
  stopPoll()
  map?.destroy()
  anaMap?.destroy()
})

function locate() {
  if (!AMap) return
  locateMsg.value = '定位中…'
  const geo = new AMap.Geolocation({ enableHighAccuracy: true, timeout: 8000 })
  geo.getCurrentPosition((status, result) => {
    if (status === 'complete') {
      myLoc.value = [result.position.lng, result.position.lat]
      locateMsg.value = ''
      placeMeMarker()
      applyDistances()
    } else {
      // 常见于浏览器拒绝授权或非安全上下文，不影响查询，只是没有距离
      locateMsg.value = '定位失败，距离信息不可用'
    }
  })
}

function placeMeMarker() {
  const dot = '<div class="me-dot" title="我的位置"></div>'
  if (meMarker) {
    meMarker.setPosition(myLoc.value)
  } else {
    meMarker = new AMap.Marker({
      position: myLoc.value,
      content: dot,
      zIndex: 999,
    })
    map.add(meMarker)
  }
}

// 高德返回和定位都是GCJ-02坐标，直接做球面距离即可
function distanceKm(from, to) {
  const rad = (d) => (d * Math.PI) / 180
  const dLat = rad(to[1] - from[1])
  const dLng = rad(to[0] - from[0])
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(from[1])) * Math.cos(rad(to[1])) * Math.sin(dLng / 2) ** 2
  return 2 * 6371 * Math.asin(Math.sqrt(s))
}

function applyDistances() {
  if (!myLoc.value) return
  stores.value = stores.value.map((s) => ({
    ...s,
    dist: distanceKm(myLoc.value, [s.lng, s.lat]),
  }))
}

function fmtDist(km) {
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`
}

async function doQuery() {
  if (!queryAdcode.value) {
    errorMsg.value = '请先选择省/市/区'
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await fetchStores({
      adcode: queryAdcode.value,
      keywords: keyword.value.trim(),
      types: checkedTypes.value,
    })
    stores.value = res.stores ?? []
    total.value = res.total ?? null
    applyDistances()
    renderMarkers()
  } catch (e) {
    errorMsg.value = e.message
    stores.value = []
    total.value = null
    renderMarkers()
  } finally {
    loading.value = false
  }
}

function renderMarkers() {
  if (!map) return
  markers.forEach((m) => map.remove(m))
  markers = stores.value.map((s) => {
    const marker = new AMap.Marker({ position: [s.lng, s.lat], title: s.name })
    marker.on('click', () => focusStore(s, false))
    map.add(marker)
    return marker
  })
  if (markers.length) map.setFitView(markers, false, [50, 50, 50, 50])
}

function infoHtml(s) {
  const tel = s.tel ? `<div>电话：${s.tel}</div>` : ''
  const rating = s.rating != null ? `<div>评分：<b class="star">★ ${s.rating.toFixed(1)}</b></div>` : ''
  const dist = s.dist != null ? `<div>距离：${fmtDist(s.dist)}</div>` : ''
  return (
    `<div class="poi-card"><b>${s.name}</b>` +
    `<div>${s.address || '暂无地址'}</div>${rating}${tel}${dist}</div>`
  )
}

function focusStore(store, pan = true) {
  activeId.value = store.id
  infoWindow.setContent(infoHtml(store))
  infoWindow.open(map, [store.lng, store.lat])
  if (pan) map.panTo([store.lng, store.lat])
}

// ==================== 加盟门店（平台自有名单，与地图无关） ====================
const partnerStores = ref([])
const partnerType = ref('')
const partnerKw = ref('')

const partnerTypeOptions = computed(() => {
  const seen = []
  partnerStores.value.forEach((s) => {
    const t = s.type || ''
    if (t && !seen.includes(t)) seen.push(t)
  })
  return [{ value: '', label: '全部类型' }, ...seen.map((t) => ({ value: t, label: t }))]
})

const filteredPartnerStores = computed(() => {
  const kw = partnerKw.value.trim().toLowerCase()
  return partnerStores.value.filter((s) => {
    if (partnerType.value && (s.type || '') !== partnerType.value) return false
    if (
      kw &&
      !s.name.toLowerCase().includes(kw) &&
      !(s.address || '').toLowerCase().includes(kw)
    ) {
      return false
    }
    return true
  })
})

function typeClass(type) {
  if (type === '洗护门店') return 't-wash'
  if (type === '实物供应商') return 't-supply'
  if (type === '综合门店') return 't-mixed'
  return 't-other'
}

// ==================== 市场分析 ====================
const anaProv = ref('')
const anaCity = ref('')
const anaRunning = ref(false)
const anaProgress = ref('')
const anaError = ref('')
const anaData = ref(null)
const anaMeta = ref('')
const anaSort = ref('store_count')
const anaMapEl = ref(null)
// null=未检查  true=库里有缓存  false=库里没有
const anaCached = ref(null)

watch(anaProv, () => {
  anaCity.value = ''
})
// 切换城市：清空旧城市结果 → 自动查数据库 → 命中直接展示缓存，
// 没有缓存才提示手动「开始分析」。这就是"切回来应先检索数据库"的正确实现。
watch(anaCity, async (adcode) => {
  anaData.value = null
  anaMeta.value = ''
  anaError.value = ''
  anaProgress.value = ''
  anaCached.value = null
  marketData.value = null
  marketFetchedAt.value = ''
  marketError.value = ''
  marketLoading.value = false
  if (!adcode) return
  try {
    const res = await checkAnalysisCache(adcode)
    if (anaCity.value !== adcode) return // 检查期间用户又切走了，丢弃结果
    anaCached.value = !!res.exists
    if (res.exists) {
      applyAnalysis(res.result)
      anaMeta.value = `数据库缓存 · 分析于 ${res.analyzed_at}`
    }
  } catch {
    anaCached.value = false
  }
})
const anaCityList = computed(
  () => provList.value.find((p) => p.adcode === anaProv.value)?.children ?? [],
)

const GRADIENTS = {
  stores: { 0.3: '#1e60ff', 0.55: '#00c8ff', 0.75: '#ffd800', 1: '#ff4d00' },
}

// 图例：色条与热力图渐变保持一致
const LEGEND = {
  title: '店铺热力 · 宠物店密度',
  hint: '颜色越暖代表宠物店越密集——存量客户池越大，适合冲签约量。颜色为同城内相对高低。',
  low: '稀疏',
  high: '密集',
}
const LEGEND_GRADIENT = (() => {
  const g = GRADIENTS.stores
  const stops = Object.keys(g)
    .sort((a, b) => a - b)
    .map((k) => `${g[k]} ${(k * 100).toFixed(0)}%`)
  return `linear-gradient(90deg, ${stops.join(', ')})`
})()

let anaMap = null
let heatLayer = null
let pollTimer = null
let pollJobId = ''

// 切到分析页时才初始化第二张地图（隐藏容器里建图会拿不到尺寸）
watch(activeTab, async (tab) => {
  if (tab === 'analysis' && AMap && !anaMap) {
    await nextTick()
    anaMap = new AMap.Map(anaMapEl.value, { zoom: 10 })
  }
})

// 榜单行：前端按所选指标排序
const anaRows = computed(() => {
  if (!anaData.value) return []
  const rows = [...anaData.value.districts]
  const key = anaSort.value
  rows.sort((a, b) => (b[key] ?? -1) - (a[key] ?? -1))
  return rows
})

async function runAnalysis(force = false) {
  if (!anaCity.value) {
    anaError.value = '请先选择城市（分析按城市下辖的区县逐一统计）'
    return
  }
  anaRunning.value = true
  anaError.value = ''
  anaMeta.value = ''
  // 「重新分析」时保留旧榜单展示，新数据拉到后再替换
  if (!force) anaData.value = null
  anaProgress.value = '提交中…'
  try {
    const res = await startAnalysis(anaCity.value, force)
    if (res.status === 'done') {
      anaRunning.value = false
      applyAnalysis(res.result)
      anaMeta.value = `数据库缓存 · 分析于 ${res.analyzed_at}`
      return
    }
    pollJobId = res.job_id
    pollTimer = setInterval(pollAnalysis, 2000)
  } catch (e) {
    anaError.value = e.message
    anaRunning.value = false
  }
}

async function pollAnalysis() {
  try {
    const res = await analysisStatus(pollJobId)
    if (res.status === 'running') {
      anaProgress.value = res.progress || ''
    } else if (res.status === 'done') {
      stopPoll()
      anaRunning.value = false
      applyAnalysis(res.result)
      anaMeta.value = `实时拉取 · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`
    } else {
      stopPoll()
      anaRunning.value = false
      anaError.value = res.error || '分析失败'
    }
  } catch (e) {
    stopPoll()
    anaRunning.value = false
    anaError.value = e.message
  }
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function applyAnalysis(result) {
  anaData.value = result
  anaProgress.value = ''
  renderHeat()
  loadMarket(false)
}

// ==================== 城市养宠市场数据（智谱联网搜索） ====================
const marketData = ref(null)
const marketFetchedAt = ref('')
const marketLoading = ref(false)
const marketError = ref('')
const autoMarketTried = ref(new Set()) // 本会话已自动尝试且无数据的城市，不再重复联网

const TIER_LABELS = {
  official: '官方登记',
  industry: '行业白皮书',
  media: '媒体/调研',
  estimate: '估算参考',
  none: '来源待核实',
}

// 只展示宠物保有量；换算成绝对数量后低于20万只的城市视为"没有查到有关数据"
const POP_MIN = 200000

function popAbsolute(d) {
  if (d.pet_population == null) return null
  const unit = d.pet_population_unit || ''
  if (unit.startsWith('亿')) return d.pet_population * 1e8
  if (unit.startsWith('万')) return d.pet_population * 1e4
  return d.pet_population
}

const marketPopValid = computed(() => {
  const d = marketData.value
  if (!d) return false
  const value = popAbsolute(d)
  return value != null && value >= POP_MIN
})

const marketPopText = computed(() => {
  const d = marketData.value
  if (!d) return ''
  return (
    d.pet_population_text ||
    (d.pet_population != null ? `${d.pet_population}${d.pet_population_unit || ''}` : '')
  )
})

const marketMetaText = computed(() => {
  const d = marketData.value
  if (!d) return ''
  const parts = []
  if (d.year) parts.push(`${d.year}年`)
  parts.push(TIER_LABELS[d.tier] || '来源待核实')
  if (d.conflict) parts.push(d.conflict)
  if (marketFetchedAt.value) parts.push(`获取于${marketFetchedAt.value}`)
  return parts.join(' · ')
})

const marketTitle = computed(() => {
  const d = marketData.value
  if (!d || !d.sources || !d.sources.length) return '数据来自智谱联网搜索'
  return '来源：\n' + d.sources.map((s) => `${s.title}\n${s.url}`).join('\n')
})

async function loadMarket(force = false) {
  const adcode = anaCity.value
  if (!adcode || marketLoading.value) return
  marketLoading.value = true
  marketError.value = ''
  try {
    if (!force) {
      const hit = await checkCityMarket(adcode)
      if (anaCity.value !== adcode) return
      if (hit.exists) {
        marketData.value = hit.data
        marketFetchedAt.value = hit.fetched_at
        return
      }
      if (autoMarketTried.value.has(adcode)) return // 本会话已自动试过且无数据
    }
    autoMarketTried.value.add(adcode)
    const res = await fetchCityMarket(adcode)
    if (anaCity.value !== adcode) return
    if (res.saved) {
      marketData.value = res.data
      marketFetchedAt.value = res.fetched_at
    }
    // saved=false：提取不到公开数据，保持空，界面显示"暂无+重试"
  } catch (e) {
    if (anaCity.value === adcode) marketError.value = e.message
  } finally {
    marketLoading.value = false
  }
}

function renderHeat() {
  if (!anaMap || !anaData.value) return
  const data = anaData.value.stores.map((p) => ({ lng: p.lng, lat: p.lat, count: 1 }))
  if (!heatLayer) {
    heatLayer = new AMap.HeatMap(anaMap, {
      radius: 22,
      opacity: [0, 0.75],
      gradient: GRADIENTS.stores,
    })
  }
  heatLayer.setDataSet({ data, max: 8 })
  if (data.length) {
    const lngs = data.map((d) => d.lng)
    const lats = data.map((d) => d.lat)
    anaMap.setBounds(
      new AMap.Bounds(
        new AMap.LngLat(Math.min(...lngs), Math.min(...lats)),
        new AMap.LngLat(Math.max(...lngs), Math.max(...lats)),
      ),
    )
  }
}
</script>

<template>
  <div class="page">
    <header class="topbar">
      <h1>🐾 petSerch</h1>
      <nav class="tabs" aria-label="功能页">
        <button type="button" :class="{ on: activeTab === 'lookup' }" @click="activeTab = 'lookup'">
          门店查询
        </button>
        <button type="button" :class="{ on: activeTab === 'analysis' }" @click="activeTab = 'analysis'">
          市场分析
        </button>
        <button type="button" :class="{ on: activeTab === 'partners' }" @click="activeTab = 'partners'">
          加盟门店
        </button>
      </nav>
      <button class="locate-btn" type="button" @click="locate">
        {{ locateMsg || '📍 我的位置' }}
      </button>
    </header>

    <!-- ==================== 门店查询 ==================== -->
    <div class="tab-pane" v-show="activeTab === 'lookup'">
      <section class="filters">
        <select v-model="selProv" aria-label="省份">
          <option value="">选择省份</option>
          <option v-for="p in provList" :key="p.adcode" :value="p.adcode">
            {{ p.name }}
          </option>
        </select>
        <select v-model="selCity" :disabled="!selProv" aria-label="城市">
          <option value="">选择城市</option>
          <option v-for="c in cityList" :key="c.adcode" :value="c.adcode">
            {{ c.name }}
          </option>
        </select>
        <select v-model="selDist" :disabled="!selCity" aria-label="区县">
          <option value="">全区</option>
          <option v-for="d in distList" :key="d.adcode" :value="d.adcode">
            {{ d.name }}
          </option>
        </select>
        <label v-for="t in TYPE_OPTIONS" :key="t.code" class="type-check">
          <input type="checkbox" :value="t.code" v-model="checkedTypes" />
          {{ t.label }}
        </label>
        <input
          v-model="keyword"
          class="kw"
          placeholder="关键词，如：猫舍"
          @keyup.enter="doQuery"
        />
        <select v-model="sortBy" aria-label="排序方式">
          <option value="dist">按距离</option>
          <option value="rating">按评分</option>
        </select>
        <button class="query-btn" type="button" :disabled="loading" @click="doQuery">
          {{ loading ? '查询中…' : '查询' }}
        </button>
      </section>

      <div v-if="errorMsg" class="error">{{ errorMsg }}</div>

      <main class="content">
        <aside class="list">
          <div v-if="total != null" class="count-bar">
            当前区域共 <b>{{ total }}</b> 家
            <span v-if="stores.length < total" class="count-note">
              （列表按距离显示前 {{ stores.length }} 家）
            </span>
          </div>
          <p v-if="!sortedStores.length" class="empty">
            {{
              loading
                ? '查询中…'
                : total === 0
                  ? '该区域没有符合条件的门店'
                  : '选择区域后点击「查询」'
            }}
          </p>
          <div
            v-for="s in sortedStores"
            :key="s.id"
            class="store"
            :class="{ active: s.id === activeId }"
            @click="focusStore(s)"
          >
            <div class="row1">
              <b>{{ s.name }}</b>
              <span class="tag">{{ s.type_label }}</span>
            </div>
            <div class="addr">{{ s.address || '暂无地址' }}</div>
            <div class="row3">
              <span v-if="s.rating != null" class="rating">★ {{ s.rating.toFixed(1) }}</span>
              <span v-if="s.dist != null" class="dist">{{ fmtDist(s.dist) }}</span>
              <span v-if="s.tel" class="tel">{{ s.tel }}</span>
            </div>
          </div>
        </aside>
        <div ref="mapEl" class="map"></div>
      </main>
    </div>

    <!-- ==================== 市场分析 ==================== -->
    <div class="tab-pane" v-show="activeTab === 'analysis'">
      <section class="filters">
        <select v-model="anaProv" aria-label="分析省份">
          <option value="">选择省份</option>
          <option v-for="p in provList" :key="p.adcode" :value="p.adcode">
            {{ p.name }}
          </option>
        </select>
        <select v-model="anaCity" :disabled="!anaProv" aria-label="分析城市">
          <option value="">选择城市</option>
          <option v-for="c in anaCityList" :key="c.adcode" :value="c.adcode">
            {{ c.name }}
          </option>
        </select>
        <button class="query-btn" type="button" :disabled="anaRunning" @click="runAnalysis(!!anaData)">
          {{ anaRunning ? '分析中…' : anaData ? '🔄 重新分析（更新数据）' : '开始分析' }}
        </button>
        <template v-if="anaData">
          <span class="divider"></span>
          <select v-model="anaSort" aria-label="榜单排序">
            <option value="store_count">榜单按宠物店数</option>
            <option value="avg_rating">榜单按平均评分</option>
          </select>
        </template>
      </section>

      <div v-if="anaRunning && anaProgress" class="progress-line">
        ⏳ {{ anaProgress }}（首次分析需逐区拉取约1-2分钟，结果缓存6小时）
      </div>
      <div v-if="anaData && anaData.failed" class="warn">
        {{ anaData.failed }} 个子查询失败（配额或网络原因），数据可能不全，可稍后重新分析
      </div>
      <div v-if="anaError" class="error">{{ anaError }}</div>

      <main class="content">
        <aside class="list">
          <div v-if="anaData" class="ana-head">
            {{ anaData.city }} · {{ anaRows.length }} 个区县 · 宠物店
            {{ anaData.stores.length }} 家
            <div v-if="anaMeta" class="ana-meta">{{ anaMeta }}</div>
          </div>
          <div v-if="anaData" class="market-strip" :title="marketPopValid ? marketTitle : '没有查到有关数据'">
            <template v-if="marketLoading">⏳ 正在联网获取养宠市场数据（搜索+AI提取，约10-30秒）…</template>
            <template v-else-if="marketData && marketPopValid">
              <span>🐕 宠物保有量 {{ marketPopText }}</span>
              <span v-if="marketMetaText" class="market-meta">{{ marketMetaText }}（悬停看来源）</span>
            </template>
            <template v-else-if="marketError">
              <span class="market-err">获取失败：{{ marketError }}</span>
              <a href="javascript:;" class="market-retry" @click="loadMarket(true)">重试</a>
            </template>
            <template v-else>
              <span>没有查到有关数据</span>
              <a href="javascript:;" class="market-retry" @click="loadMarket(true)">重试获取</a>
            </template>
          </div>
          <table v-if="anaData" class="ana-table">
            <thead>
              <tr>
                <th>区县</th>
                <th>宠物店</th>
                <th>均分</th>
                <th>24h</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in anaRows" :key="r.adcode">
                <td>{{ r.name }}</td>
                <td>{{ r.store_count }}</td>
                <td>{{ r.avg_rating ?? '—' }}</td>
                <td>{{ r.open24 }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="!anaData && !anaRunning" class="empty">
            <template v-if="anaCached === false && anaCity">
              该城市暂无缓存分析结果<br />点击「开始分析」实时拉取（约1-2分钟）
            </template>
            <template v-else>
              选择省/市后点「开始分析」<br />生成宠物店密度热力图与分区榜单
            </template>
          </p>
        </aside>
        <div class="map-wrap">
          <div ref="anaMapEl" class="map"></div>
          <div v-if="anaData" class="map-legend">
            <div class="lg-title">{{ LEGEND.title }}</div>
            <div class="lg-bar" :style="{ background: LEGEND_GRADIENT }"></div>
            <div class="lg-labels">
              <span>{{ LEGEND.low }}</span>
              <span>{{ LEGEND.high }}</span>
            </div>
            <div class="lg-hint">{{ LEGEND.hint }}</div>
          </div>
        </div>
      </main>
    </div>

    <!-- ==================== 加盟门店（平台自有名单，与地图无关） ==================== -->
    <div class="tab-pane" v-show="activeTab === 'partners'">
      <section class="filters">
        <label v-for="opt in partnerTypeOptions" :key="opt.value" class="type-check">
          <input type="radio" :value="opt.value" v-model="partnerType" />
          {{ opt.label }}
        </label>
        <input v-model="partnerKw" class="kw" placeholder="搜索店名 / 地址" />
        <span class="partner-count">共 {{ filteredPartnerStores.length }} 家</span>
      </section>

      <main class="content partner-content">
        <div class="card-grid">
          <div v-for="s in filteredPartnerStores" :key="s.id" class="partner-card">
            <span class="p-type" :class="typeClass(s.type)">{{ s.type || '门店' }}</span>
            <b class="p-name">{{ s.name }}</b>
            <div class="p-addr">📍 {{ s.address || '暂无地址' }}</div>
          </div>
        </div>
        <p v-if="!filteredPartnerStores.length" class="empty">
          {{
            partnerKw || partnerType
              ? '没有匹配的门店'
              : '暂无加盟门店数据：运行 backend/import_stores.py 导入名单'
          }}
        </p>
      </main>
    </div>
  </div>
</template>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
}
body {
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  color: #2c3e50;
}

.page {
  display: flex;
  flex-direction: column;
  height: 100%;
}

/* 各Tab内容容器：v-show常驻DOM，保证地图实例和容器不解绑 */
.tab-pane {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid #eee;
}
.topbar h1 {
  margin: 0;
  font-size: 18px;
}
.tabs {
  display: flex;
  gap: 4px;
}
.tabs button {
  border: 1px solid #ddd;
  background: #fff;
  padding: 6px 14px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.tabs button.on {
  background: #2f88ff;
  border-color: #2f88ff;
  color: #fff;
}
.locate-btn {
  border: 1px solid #ddd;
  background: #fff;
  border-radius: 6px;
  padding: 5px 10px;
  cursor: pointer;
  font-size: 13px;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid #eee;
}
.filters select,
.filters .kw {
  height: 32px;
  padding: 0 8px;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: #fff;
  font-size: 13px;
}
.filters .kw {
  width: 160px;
}
.type-check {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  cursor: pointer;
}
.divider {
  width: 1px;
  height: 20px;
  background: #e5e5e5;
}
.query-btn {
  height: 32px;
  padding: 0 18px;
  border: none;
  border-radius: 6px;
  background: #2f88ff;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
}
.query-btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.error {
  margin: 8px 16px 0;
  padding: 8px 12px;
  border-radius: 6px;
  background: #fdecea;
  color: #c0392b;
  font-size: 13px;
}
.progress-line {
  margin: 8px 16px 0;
  font-size: 13px;
  color: #555;
}
.warn {
  margin: 8px 16px 0;
  padding: 8px 12px;
  border-radius: 6px;
  background: #fff7e6;
  color: #b8860b;
  font-size: 13px;
}

.content {
  display: flex;
  flex: 1;
  min-height: 0;
}
.list {
  width: 340px;
  overflow-y: auto;
  border-right: 1px solid #eee;
  padding: 10px;
  box-sizing: border-box;
}
.empty {
  color: #999;
  font-size: 13px;
  text-align: center;
  margin-top: 40px;
}
.count-bar {
  font-size: 13px;
  color: #555;
  padding: 2px 4px 10px;
}
.count-bar b {
  color: #2f88ff;
  font-size: 15px;
}
.count-note {
  color: #aaa;
  font-size: 12px;
}
.store {
  padding: 10px 12px;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
}
.store:hover {
  border-color: #bcd9ff;
}
.store.active {
  border-color: #2f88ff;
  background: #f2f8ff;
}
.row1 {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.row1 b {
  font-size: 14px;
}
.tag {
  flex: none;
  font-size: 11px;
  color: #2f88ff;
  background: #eaf3ff;
  border-radius: 4px;
  padding: 1px 6px;
}
.addr {
  font-size: 12px;
  color: #777;
  margin-top: 4px;
}
.row3 {
  display: flex;
  gap: 12px;
  font-size: 12px;
  margin-top: 4px;
}
.rating {
  color: #f39c12;
  font-weight: bold;
}
.star {
  color: #f39c12;
}
.tel {
  color: #999;
}
.map {
  flex: 1;
  min-width: 0;
}

.map-wrap {
  position: relative;
  flex: 1;
  min-width: 0;
  display: flex;
}
.map-legend {
  position: absolute;
  left: 12px;
  bottom: 14px;
  z-index: 500;
  pointer-events: none; /* 图例不挡地图拖拽 */
  background: rgba(255, 255, 255, 0.94);
  border-radius: 8px;
  padding: 10px 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  font-size: 12px;
  color: #444;
  max-width: 240px;
}
.lg-title {
  font-weight: bold;
  margin-bottom: 6px;
  color: #2c3e50;
}
.lg-bar {
  height: 10px;
  border-radius: 5px;
}
.lg-labels {
  display: flex;
  justify-content: space-between;
  margin-top: 3px;
  color: #888;
}
.lg-hint {
  margin-top: 6px;
  color: #666;
  line-height: 1.5;
}

/* 加盟门店卡片墙 */
.partner-count {
  font-size: 13px;
  color: #555;
}
.partner-content {
  display: block;
  overflow-y: auto;
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 10px;
  padding: 14px 16px;
}
.partner-card {
  border: 1px solid #f0f0f0;
  border-radius: 10px;
  padding: 14px;
  background: #fff;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.partner-card:hover {
  border-color: #bcd9ff;
  box-shadow: 0 2px 8px rgba(47, 136, 255, 0.1);
}
.p-type {
  align-self: flex-start;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
}
.t-wash {
  background: #eaf3ff;
  color: #2f88ff;
}
.t-supply {
  background: #fff3e6;
  color: #e67e22;
}
.t-mixed {
  background: #e8f8ef;
  color: #27ae60;
}
.t-other {
  background: #f0f0f0;
  color: #888;
}
.p-name {
  font-size: 15px;
}
.p-addr {
  font-size: 12px;
  color: #777;
  line-height: 1.5;
}

.ana-head {
  font-size: 12px;
  color: #666;
  padding: 2px 4px 8px;
}
.ana-meta {
  color: #999;
  font-size: 11px;
  padding-top: 2px;
}
.market-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  font-size: 12px;
  color: #444;
  background: #f5f9ff;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
  line-height: 1.6;
}
.market-meta {
  color: #999;
  font-size: 11px;
  width: 100%;
}
.market-err {
  color: #c0392b;
}
.market-retry {
  color: #2f88ff;
}
.ana-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.ana-table th,
.ana-table td {
  padding: 6px 4px;
  border-bottom: 1px solid #f0f0f0;
  text-align: right;
}
.ana-table th:first-child,
.ana-table td:first-child {
  text-align: left;
}
.ana-table th {
  color: #999;
  font-weight: normal;
}

@media (max-width: 720px) {
  .content {
    flex-direction: column;
  }
  .list {
    width: 100%;
    max-height: 45%;
    border-right: none;
    border-bottom: 1px solid #eee;
    order: 2;
  }
  .map,
  .map-wrap {
    min-height: 55%;
    order: 1;
  }
}

/* 我的位置标记（渲染进地图容器，不能用scoped） */
.me-dot {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #2f88ff;
  border: 3px solid #fff;
  box-shadow: 0 0 0 2px #2f88ff66;
}
.poi-card {
  font-size: 13px;
  line-height: 1.6;
  max-width: 240px;
}
</style>
