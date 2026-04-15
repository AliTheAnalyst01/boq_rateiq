import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 600000
})

api.interceptors.response.use(
  res => res,
  err => {
    const message = err.response?.data?.detail || err.message || 'Something went wrong. Please try again.'
    return Promise.reject(new Error(message))
  }
)

export const boqApi = {
  uploadFile: (file) => {
    const form = new FormData()
    form.append('file', file)
    return api.post('/fill-boq', form, {
      responseType: 'blob'
    })
  },
  checkHealth: () => api.get('/health'),
  rateItem: (payload) => api.post('/rate-item', payload),
  rateBatch: (payload) => api.post('/rate-batch', payload),
  searchItems: (q, limit = 12) => api.get('/search', { params: { q, limit } })
}
