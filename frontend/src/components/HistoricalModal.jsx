import React, { useEffect, useRef } from 'react'
import { X, FileSpreadsheet, Info, CheckCircle } from 'lucide-react'
import { formatPKR } from '../utils/formatters'

export function HistoricalModal({ isOpen, onClose, data }) {
  const tableRef = useRef(null)

  useEffect(() => {
    if (isOpen && tableRef.current && data?.matchedRows?.length) {
      const firstMatched = data.matchedRows[0]
      const rowElement = tableRef.current.querySelector(`[data-index="${firstMatched}"]`)
      if (rowElement) {
        rowElement.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }
  }, [isOpen, data])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      
      <div className="relative w-[90vw] h-[85vh] bg-white rounded-xl shadow-2xl flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <div className="flex items-center gap-3">
            <FileSpreadsheet className="w-6 h-6 text-amber-600" />
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Source File: {data?.sourceFile || 'Unknown'}</h2>
              <p className="text-sm text-gray-500">Project: {data?.projectName || 'Historical Project'}</p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        <div className="px-6 py-3 bg-amber-50 border-b border-amber-100 flex items-start gap-2">
          <Info className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-amber-800">
            The yellow rows below show where we found the historical price of {data?.historicalRate ? formatPKR(data.historicalRate) : ''} 
            for {data?.itemDescription || 'this item'}. This historical data was used to help suggest your rate.
          </p>
        </div>

        <div className="flex-1 overflow-auto p-4" ref={tableRef}>
          {data?.rows?.length > 0 ? (
            <table className="w-full text-sm">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase w-10">#</th>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase">Description</th>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase w-36">Source File</th>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-gray-500 uppercase w-20">Qty</th>
                  <th className="px-3 py-2 text-left text-xs font-semibold text-gray-500 uppercase w-16">Unit</th>
                  <th className="px-3 py-2 text-right text-xs font-semibold text-gray-500 uppercase w-28">Rate (PKR)</th>
                  <th className="px-3 py-2 text-center text-xs font-semibold text-gray-500 uppercase w-20">Score</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.rows.map((row, idx) => {
                  const isTop = idx === 0
                  const score = row.reranker_score ?? 0
                  const scoreColor = score >= 5 ? 'text-green-600' : score >= 2 ? 'text-yellow-600' : 'text-gray-400'

                  return (
                    <tr
                      key={idx}
                      data-index={idx}
                      className={`${isTop ? 'bg-yellow-50 border-l-4 border-orange-400' : 'bg-white hover:bg-gray-50'}`}
                    >
                      <td className="px-3 py-2 font-mono text-gray-500 text-xs">{idx + 1}</td>
                      <td className="px-3 py-2 text-gray-700">
                        <div className="max-w-lg">
                          {isTop && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-orange-100 text-orange-700 rounded text-xs mr-1 mb-1">
                              <CheckCircle className="w-3 h-3" /> Best Match
                            </span>
                          )}
                          <span>{row.DESCRIPTION}</span>
                          {row.section_title && (
                            <div className="text-xs text-gray-400 mt-0.5">{row.section_title}</div>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2 text-amber-700 text-xs font-medium" title={row.source_file}>
                        {row.source_file?.length > 20
                          ? row.source_file.slice(0, 20) + '…'
                          : row.source_file || '—'}
                      </td>
                      <td className="px-3 py-2 text-right text-gray-600">{row.QTY || '—'}</td>
                      <td className="px-3 py-2 text-gray-600">{row.UNIT || '—'}</td>
                      <td className="px-3 py-2 text-right font-semibold text-gray-800">
                        {row.RATE > 0 ? formatPKR(row.RATE) : '—'}
                      </td>
                      <td className={`px-3 py-2 text-center text-xs font-mono ${scoreColor}`}>
                        {score.toFixed(1)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <div className="text-center py-12 text-gray-500">
              No historical matches found
            </div>
          )}
        </div>

        <div className="flex items-center justify-between px-6 py-4 border-t bg-gray-50">
          <p className="text-sm text-gray-500">
            {data?.totalRows || 0} historical matches • Best match: {data?.sourceFile} • Score = cross-encoder relevance (higher is better)
          </p>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
