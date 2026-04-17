import React, { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useBoqStore } from '../store/boqStore'
import { BOQTable } from '../components/BOQTable'
import { HistoricalModal } from '../components/HistoricalModal'
import { formatPKR } from '../utils/formatters'
import {
  FileText, DollarSign, AlertTriangle, History,
  TrendingUp, Download, Upload, ArrowLeft, CheckCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { boqApi } from '../api/boqApi'

function StatCard({ icon: Icon, value, label, accentColor, bgColor }) {
  return (
    <div
      className="rounded-xl p-5 border flex items-start gap-4"
      style={{ background: '#ffffff', borderColor: 'var(--color-border)' }}
    >
      <div
        className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
        style={{ background: bgColor }}
      >
        <Icon className="w-5 h-5" style={{ color: accentColor }} />
      </div>
      <div className="min-w-0">
        <p className="font-rate text-xl font-medium text-stone-900 truncate">{value}</p>
        <p className="text-xs text-stone-400 mt-0.5 leading-snug">{label}</p>
      </div>
    </div>
  )
}

export function DashboardPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const { boqData, downloadBlob, downloadFileName, resetAll } = useBoqStore()
  const [showHistoryModal, setShowHistoryModal] = useState(false)
  const [historyData, setHistoryData] = useState(null)

  const data = boqData || location.state?.boqData

  if (!data) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--color-bg)' }}>
        <div className="text-center">
          <p className="text-stone-400 mb-4">No data available</p>
          <button
            onClick={() => navigate('/')}
            className="px-5 py-2.5 rounded-lg text-white font-medium text-sm"
            style={{ background: 'var(--color-gold)' }}
          >
            Go to Upload
          </button>
        </div>
      </div>
    )
  }

  const highConfidenceCount = data.rows?.filter(r => r.CONFIDENCE === 'high').length || 0
  const majorGaps = data.rows?.filter(
    r => r.GAP_VERDICT === 'MAJOR_GAP' || r.GAP_VERDICT === 'MAJOR'
  ).length || 0
  const filledCount = data.rows?.filter(r => r.SUGGESTED_RATE > 0).length || 0

  const handleShowHistory = async (row) => {
    try {
      const { data: searchData } = await boqApi.searchItems(row.DESCRIPTION, 15)
      const results = searchData.results || []

      if (!results.length) {
        toast('No historical matches found for this item.', { icon: 'ℹ️' })
        return
      }

      const primarySource = results[0]?.source_file || row.HISTORICAL_SOURCE || 'Historical Data'

      setHistoryData({
        sourceFile: primarySource,
        itemDescription: row.DESCRIPTION,
        historicalRate: row.HISTORICAL_RATE,
        suggestedRate: row.SUGGESTED_RATE,
        matchedRows: results.map((_, i) => i),
        rows: results.map((r, i) => ({
          SNO: String(i + 1),
          DESCRIPTION: r.description,
          QTY: r.qty || '—',
          UNIT: r.unit,
          RATE: r.rate,
          AMOUNT: null,
          source_file: r.source_file,
          section_title: r.section_title,
          reranker_score: r.reranker_score,
        })),
        totalRows: results.length,
      })
      setShowHistoryModal(true)
    } catch {
      toast.error('Could not load historical matches')
    }
  }

  const handleDownload = () => {
    if (!downloadBlob) {
      toast.error('Download not available — please re-upload the file.')
      return
    }
    const url = URL.createObjectURL(downloadBlob)
    const a = document.createElement('a')
    a.href = url
    a.download = downloadFileName || 'rateiq_filled_boq.xlsx'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    toast.success('Download started!')
  }

  const handleUploadNew = () => {
    resetAll()
    navigate('/')
  }

  return (
    <div className="min-h-screen flex flex-col" style={{ background: 'var(--color-bg)' }}>

      {/* ── Header ───────────────────────────────────── */}
      <header
        className="border-b px-6 py-3 flex items-center justify-between sticky top-0 z-10"
        style={{ background: '#ffffff', borderColor: 'var(--color-border)' }}
      >
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={() => navigate('/')}
            className="p-2 rounded-lg hover:bg-stone-100 transition-colors flex-shrink-0"
          >
            <ArrowLeft className="w-4 h-4 text-stone-500" />
          </button>
          <div className="min-w-0">
            <h1 className="text-sm font-semibold text-stone-900 truncate">
              {data.originalFileName || 'BOQ Analysis'}
            </h1>
            <p className="text-xs text-stone-400">
              {filledCount} rates filled ·{' '}
              {new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={handleUploadNew}
            className="hidden sm:flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium text-stone-600 hover:bg-stone-100 transition-colors border"
            style={{ borderColor: 'var(--color-border)' }}
          >
            <Upload className="w-4 h-4" />
            New BOQ
          </button>
          <button
            onClick={handleDownload}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold text-white transition-all hover:opacity-90 active:scale-95"
            style={{ background: 'var(--color-gold)' }}
          >
            <Download className="w-4 h-4" />
            Download Excel
          </button>
        </div>
      </header>

      <main className="flex-1 max-w-7xl mx-auto w-full px-6 py-6">

        {/* ── Alert: items needing review ─────────────── */}
        {majorGaps > 0 && (
          <div
            className="flex items-start gap-3 rounded-xl px-4 py-3 mb-5 border text-sm animate-fade-in"
            style={{ background: '#fff7ed', borderColor: '#fed7aa', color: '#92400e' }}
          >
            <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <span>
              <strong>{majorGaps} item{majorGaps > 1 ? 's' : ''}</strong> have a large gap
              between the historical rate and current market prices. Review these carefully
              before submitting your tender.
            </span>
          </div>
        )}

        {/* ── Stat cards ──────────────────────────────── */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
          <StatCard
            icon={FileText}
            value={data.totalItems || 0}
            label="BOQ line items"
            accentColor="#2563eb"
            bgColor="#eff6ff"
          />
          <StatCard
            icon={DollarSign}
            value={formatPKR(data.totalCost)}
            label="Total estimated cost"
            accentColor="var(--color-rate)"
            bgColor="#f0fdf4"
          />
          <StatCard
            icon={CheckCircle}
            value={highConfidenceCount}
            label="High confidence rates"
            accentColor="#15803d"
            bgColor="#dcfce7"
          />
          <StatCard
            icon={History}
            value={data.itemsWithHistory || 0}
            label="Matched to past projects"
            accentColor="var(--color-gold)"
            bgColor="var(--color-gold-bg)"
          />
          <StatCard
            icon={AlertTriangle}
            value={majorGaps}
            label="Prices need your review"
            accentColor="#dc2626"
            bgColor="#fef2f2"
          />
        </div>

        {/* ── Results table ───────────────────────────── */}
        <div
          className="rounded-xl border overflow-hidden"
          style={{ background: '#ffffff', borderColor: 'var(--color-border)' }}
        >
          <div
            className="px-5 py-4 border-b flex items-center justify-between"
            style={{ borderColor: 'var(--color-border)' }}
          >
            <div>
              <h2 className="font-semibold text-stone-900 text-sm">Rate Results</h2>
              <p className="text-xs text-stone-400 mt-0.5">
                Click any row to see the AI explanation and sources
              </p>
            </div>
            <span className="text-xs text-stone-400 font-rate">
              {data.rows?.length || 0} items
            </span>
          </div>
          <BOQTable data={data} onShowHistory={handleShowHistory} />
        </div>
      </main>

      <HistoricalModal
        isOpen={showHistoryModal}
        onClose={() => setShowHistoryModal(false)}
        data={historyData}
      />
    </div>
  )
}
