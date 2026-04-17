import React, { useEffect, useRef } from 'react'
import { X, FileSpreadsheet, Info, CheckCircle } from 'lucide-react'
import { formatPKR } from '../utils/formatters'

export function HistoricalModal({ isOpen, onClose, data }) {
  const tableRef = useRef(null)

  useEffect(() => {
    if (isOpen && tableRef.current && data?.matchedRows?.length) {
      const firstMatched = data.matchedRows[0]
      const rowElement = tableRef.current.querySelector(`[data-index="${firstMatched}"]`)
      if (rowElement) rowElement.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [isOpen, data])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center animate-fade-in">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div
        className="relative w-[92vw] max-w-5xl h-[85vh] rounded-2xl shadow-2xl flex flex-col overflow-hidden"
        style={{ background: '#ffffff', border: '1px solid var(--color-border)' }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0"
          style={{ borderColor: 'var(--color-border)' }}
        >
          <div className="flex items-center gap-3">
            <div
              className="w-9 h-9 rounded-lg flex items-center justify-center"
              style={{ background: 'var(--color-gold-bg)' }}
            >
              <FileSpreadsheet className="w-5 h-5" style={{ color: 'var(--color-gold)' }} />
            </div>
            <div>
              <h2 className="font-semibold text-stone-900 text-sm">
                Historical Matches — {data?.sourceFile || 'Unknown File'}
              </h2>
              <p className="text-xs text-stone-400 mt-0.5 truncate max-w-sm">
                {data?.itemDescription}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-stone-100 transition-colors"
          >
            <X className="w-4 h-4 text-stone-400" />
          </button>
        </div>

        {/* Info banner */}
        <div
          className="px-5 py-3 flex items-start gap-2 text-sm border-b flex-shrink-0"
          style={{ background: '#fffbeb', borderColor: '#fde68a', color: '#92400e' }}
        >
          <Info className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <p>
            The highlighted row below is the best historical match for{' '}
            <strong>{data?.itemDescription || 'this item'}</strong>.
            {data?.historicalRate
              ? ` Historical rate: ${formatPKR(data.historicalRate)}.`
              : ''}{' '}
            Higher score = closer match.
          </p>
        </div>

        {/* Table */}
        <div className="flex-1 overflow-auto" ref={tableRef}>
          {data?.rows?.length > 0 ? (
            <table className="w-full text-sm">
              <thead
                className="sticky top-0"
                style={{ background: '#fafaf9', borderBottom: '1px solid var(--color-border)' }}
              >
                <tr>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-stone-400 uppercase tracking-wider w-10">#</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-stone-400 uppercase tracking-wider">Description</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-stone-400 uppercase tracking-wider w-36">Source File</th>
                  <th className="px-4 py-2.5 text-right text-xs font-semibold text-stone-400 uppercase tracking-wider w-20">Qty</th>
                  <th className="px-4 py-2.5 text-left text-xs font-semibold text-stone-400 uppercase tracking-wider w-16">Unit</th>
                  <th className="px-4 py-2.5 text-right text-xs font-semibold text-stone-400 uppercase tracking-wider w-28">Rate (PKR)</th>
                  <th className="px-4 py-2.5 text-center text-xs font-semibold text-stone-400 uppercase tracking-wider w-20">Score</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row, idx) => {
                  const isTop = idx === 0
                  const score = row.reranker_score ?? 0
                  const scoreColor = score >= 5
                    ? 'var(--color-rate)'
                    : score >= 2 ? 'var(--color-gold)' : '#a8a29e'

                  return (
                    <tr
                      key={idx}
                      data-index={idx}
                      style={{
                        borderBottom: '1px solid var(--color-border)',
                        background: isTop ? '#fffbeb' : '#ffffff',
                        borderLeft: isTop ? '3px solid var(--color-gold)' : '3px solid transparent',
                      }}
                    >
                      <td className="px-4 py-2.5 font-rate text-xs text-stone-400">{idx + 1}</td>
                      <td className="px-4 py-2.5 text-stone-700">
                        <div className="max-w-lg">
                          {isTop && (
                            <span
                              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs mr-1.5 mb-1"
                              style={{ background: 'var(--color-gold-bg)', color: 'var(--color-gold)' }}
                            >
                              <CheckCircle className="w-3 h-3" />
                              Best Match
                            </span>
                          )}
                          <span>{row.DESCRIPTION}</span>
                          {row.section_title && (
                            <div className="text-xs text-stone-400 mt-0.5">{row.section_title}</div>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-xs font-medium" style={{ color: 'var(--color-gold)' }}
                        title={row.source_file}>
                        {row.source_file?.length > 22
                          ? row.source_file.slice(0, 22) + '…'
                          : row.source_file || '—'}
                      </td>
                      <td className="px-4 py-2.5 text-right font-rate text-xs text-stone-500">{row.QTY || '—'}</td>
                      <td className="px-4 py-2.5 text-xs text-stone-500">{row.UNIT || '—'}</td>
                      <td className="px-4 py-2.5 text-right font-rate font-medium text-stone-800">
                        {row.RATE > 0 ? formatPKR(row.RATE) : '—'}
                      </td>
                      <td className="px-4 py-2.5 text-center font-rate text-xs" style={{ color: scoreColor }}>
                        {score.toFixed(1)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <div className="text-center py-16 text-stone-400">
              No historical matches found
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          className="flex items-center justify-between px-6 py-3 border-t flex-shrink-0"
          style={{ background: '#fafaf9', borderColor: 'var(--color-border)' }}
        >
          <p className="text-xs text-stone-400">
            {data?.totalRows || 0} historical matches · Score = AI relevance (higher is better)
          </p>
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm font-medium text-white transition-all hover:opacity-90"
            style={{ background: 'var(--color-gold)' }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
