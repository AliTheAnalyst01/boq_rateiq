import React, { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useBoqStore } from '../store/boqStore'
import { BOQTable } from '../components/BOQTable'
import { HistoricalModal } from '../components/HistoricalModal'
import { formatPKR } from '../utils/formatters'
import { FileText, DollarSign, AlertTriangle, History, TrendingUp, Download, Upload, ArrowLeft } from 'lucide-react'
import toast from 'react-hot-toast'
import { boqApi } from '../api/boqApi'

function StatCard({ icon: Icon, value, label, bgColor, iconColor }) {
  return (
    <div className={`p-5 rounded-lg ${bgColor}`}>
      <div className="flex items-center gap-3">
        <div className={`p-2 rounded-lg ${iconColor}`}>
          <Icon className="w-5 h-5" />
        </div>
        <div>
          <p className="text-2xl font-bold text-gray-900">{value}</p>
          <p className="text-sm text-gray-600">{label}</p>
        </div>
      </div>
    </div>
  )
}

export function DashboardPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const { boqData, resetAll } = useBoqStore()
  const [showHistoryModal, setShowHistoryModal] = useState(false)
  const [historyData, setHistoryData] = useState(null)

  const data = boqData || location.state?.boqData

  if (!data) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <p className="text-gray-500 mb-4">No data available</p>
          <button
            onClick={() => navigate('/')}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg"
          >
            Go to Upload
          </button>
        </div>
      </div>
    )
  }

  const highConfidenceCount = data.rows?.filter(r => r.CONFIDENCE === 'high').length || 0
  const majorGaps = data.rows?.filter(r => r.GAP_VERDICT === 'MAJOR_GAP' || r.GAP_VERDICT === 'MAJOR').length || 0

  const handleShowHistory = async (row) => {
    try {
      const { data: searchData } = await boqApi.searchItems(row.DESCRIPTION, 15)
      const results = searchData.results || []

      if (!results.length) {
        toast('No historical matches found for this item.', { icon: 'ℹ️' })
        return
      }

      // Group results by source file — show primary source file name in header
      const primarySource = results[0]?.source_file || row.HISTORICAL_SOURCE || 'Historical Data'

      setHistoryData({
        sourceFile: primarySource,
        itemDescription: row.DESCRIPTION,
        historicalRate: row.HISTORICAL_RATE,
        suggestedRate: row.SUGGESTED_RATE,
        matchedRows: results.map((_, i) => i),  // every result row is highlighted
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
    toast.success('Downloading your report...')
  }

  const handleUploadNew = () => {
    resetAll()
    navigate('/')
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate('/')}
              className="p-2 hover:bg-gray-100 rounded-lg"
            >
              <ArrowLeft className="w-5 h-5 text-gray-600" />
            </button>
            <div>
              <h1 className="text-xl font-bold text-gray-900">
                {data.originalFileName || 'BOQ Analysis'}
              </h1>
              <p className="text-sm text-gray-500">
                Processed on {new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}
              </p>
            </div>
          </div>
          <div className="flex gap-3">
            <button
              onClick={handleDownload}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700"
            >
              <Download className="w-4 h-4" />
              Download Report
            </button>
            <button
              onClick={handleUploadNew}
              className="flex items-center gap-2 px-4 py-2 border border-gray-300 rounded-lg font-medium text-gray-700 hover:bg-gray-100"
            >
              <Upload className="w-4 h-4" />
              Upload New BOQ
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
          <StatCard
            icon={FileText}
            value={data.totalItems || 0}
            label="BOQ Line Items"
            bgColor="bg-blue-50"
            iconColor="bg-blue-100 text-blue-600"
          />
          <StatCard
            icon={DollarSign}
            value={formatPKR(data.totalCost)}
            label="Total Estimated Cost"
            bgColor="bg-green-50"
            iconColor="bg-green-100 text-green-600"
          />
          <StatCard
            icon={AlertTriangle}
            value={majorGaps}
            label="Prices Need Review"
            bgColor="bg-red-50"
            iconColor="bg-red-100 text-red-600"
          />
          <StatCard
            icon={History}
            value={data.itemsWithHistory || 0}
            label="Matched to Past Projects"
            bgColor="bg-amber-50"
            iconColor="bg-amber-100 text-amber-600"
          />
          <StatCard
            icon={TrendingUp}
            value={highConfidenceCount}
            label="High Confidence Rates"
            bgColor="bg-purple-50"
            iconColor="bg-purple-100 text-purple-600"
          />
        </div>

        <div className="bg-white rounded-lg shadow-sm border">
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
