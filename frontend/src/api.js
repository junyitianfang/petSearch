async function request(url, options) {
  const res = await fetch(url, options)
  if (!res.ok) {
    let message = `请求失败（HTTP ${res.status}）`
    try {
      const body = await res.json()
      if (body.detail) message = body.detail
    } catch {
      // 非 JSON 响应，保留默认提示
    }
    throw new Error(message)
  }
  return res.json()
}

async function get(url, params) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== '' && v != null),
  )
  return request(`${url}?${qs}`)
}

async function post(url, body) {
  return request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export const fetchRegions = () => get('/api/regions', {})

export const fetchStores = ({ adcode, keywords, types }) =>
  get('/api/stores', { adcode, keywords, types: types.join(',') })

export const startAnalysis = (adcode, force = false) =>
  post('/api/analysis', { adcode, force })

export const analysisStatus = (jobId) => get(`/api/analysis/${jobId}`, {})

export const checkAnalysisCache = (adcode) => get('/api/analysis-cache', { adcode })

export const checkCityMarket = (adcode) => get('/api/city-market', { adcode })

export const fetchCityMarket = (adcode) => post('/api/city-market/fetch', { adcode })

export const fetchPartnerStores = () => get('/api/partner-stores', {})
