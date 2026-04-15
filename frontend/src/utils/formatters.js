export function formatPKR(amount) {
  if (!amount && amount !== 0) return '—'
  if (amount >= 10000000) return `Rs. ${(amount / 10000000).toFixed(2)} Cr`
  if (amount >= 100000) return `Rs. ${(amount / 100000).toFixed(2)} Lac`
  return `Rs. ${Number(amount).toLocaleString('en-PK')}`
}

export function formatConfidence(confidence) {
  const map = {
    high: { label: 'High Confidence', color: 'green', icon: '✓' },
    medium: { label: 'Medium Confidence', color: 'yellow', icon: '~' },
    low: { label: 'Low Confidence', color: 'red', icon: '⚠' },
  }
  return map[confidence] || map['low']
}

export function formatGapVerdict(verdict, pct) {
  const p = pct ? ` (${Math.abs(pct).toFixed(0)}%)` : ''
  if (verdict === 'MAJOR_GAP' || verdict === 'MAJOR') return { label: `Big Difference${p}`, color: 'red' }
  if (verdict === 'MINOR_GAP' || verdict === 'MINOR') return { label: `Small Difference${p}`, color: 'yellow' }
  return { label: 'Good Match', color: 'green' }
}

export function truncate(text, maxChars = 100) {
  if (!text) return ''
  return text.length > maxChars ? text.slice(0, maxChars) + '...' : text
}

export function formatDate(isoString) {
  if (!isoString) return ''
  return new Date(isoString).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: true
  })
}
