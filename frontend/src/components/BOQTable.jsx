import React, { useState } from 'react'
import { ChevronDown, ChevronRight, FolderOpen, ExternalLink } from 'lucide-react'
import { formatPKR, formatConfidence, formatGapVerdict } from '../utils/formatters'

function ConfidenceBadge({ confidence }) {
  const { label, color } = formatConfidence(confidence)
  const styles = {
    green:  { bg: '#dcfce7', text: '#166534' },
    yellow: { bg: '#fef9c3', text: '#854d0e' },
    red:    { bg: '#fee2e2', text: '#991b1b' },
  }
  const s = styles[color] || styles.red

  // Simplified labels for non-technical users
  const friendly = {
    green:  'Verified',
    yellow: 'Estimated',
    red:    'Needs Review',
  }

  return (
    <span
      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium"
      style={{ background: s.bg, color: s.text }}
    >
      {color === 'green' && '✓ '}
      {color === 'yellow' && '~ '}
      {color === 'red' && '⚠ '}
      {friendly[color]}
    </span>
  )
}

function GapChip({ verdict, pct }) {
  const { label, color } = formatGapVerdict(verdict, pct)
  const styles = {
    green:  { bg: '#dcfce7', text: '#166534' },
    yellow: { bg: '#fef9c3', text: '#854d0e' },
    red:    { bg: '#fee2e2', text: '#991b1b' },
  }
  const s = styles[color] || styles.green
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium"
      style={{ background: s.bg, color: s.text }}
    >
      {label}
    </span>
  )
}

function ExpandedRow({ row }) {
  const cleanExplanation = (text) =>
    text ? text.replace(/https?:\/\/\S+/g, '').replace(/\s{2,}/g, ' ').trim() : ''

  return (
    <div className="px-5 pb-4 pt-3 border-t" style={{ background: '#fafaf9', borderColor: 'var(--color-border)' }}>
      {row.EXPLANATION && (
        <p className="text-sm text-stone-600 mb-4 leading-relaxed max-w-3xl">
          {cleanExplanation(row.EXPLANATION)}
        </p>
      )}

      <div className="flex flex-wrap gap-6 text-xs">
        {row.SOURCES && (
          <div>
            <span className="uppercase tracking-wider text-stone-400 font-medium">Past projects</span>
            <div className="flex flex-wrap gap-1.5 mt-1.5">
              {String(row.SOURCES).split(',').map((src, i) => (
                <span
                  key={i}
                  className="px-2 py-0.5 rounded text-stone-600 border"
                  style={{ background: '#ffffff', borderColor: 'var(--color-border)' }}
                >
                  {src.trim()}
                </span>
              ))}
            </div>
          </div>
        )}

        {row.MARKET_SOURCE && (
          <div>
            <span className="uppercase tracking-wider text-stone-400 font-medium">Market source</span>
            <div className="mt-1.5 flex items-center gap-1">
              <span style={{ color: 'var(--color-gold)' }}>{row.MARKET_SOURCE}</span>
              {row.MARKET_RATE_URL && (
                <a
                  href={row.MARKET_RATE_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex"
                  title="Open source"
                >
                  <ExternalLink className="w-3 h-3" style={{ color: 'var(--color-gold)' }} />
                </a>
              )}
            </div>
          </div>
        )}

        {row.SEARCH_KEYWORDS && (
          <div>
            <span className="uppercase tracking-wider text-stone-400 font-medium">Search terms used</span>
            <p className="mt-1.5 text-stone-500 italic">{row.SEARCH_KEYWORDS}</p>
          </div>
        )}
      </div>
    </div>
  )
}

export function BOQTable({ data, onShowHistory }) {
  const [expandedRows, setExpandedRows] = useState({})

  if (!data?.rows?.length) {
    return (
      <div className="text-center py-16 text-stone-400">
        No data to display
      </div>
    )
  }

  const toggleRow = (index) => {
    setExpandedRows(prev => ({ ...prev, [index]: !prev[index] }))
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead style={{ background: '#fafaf9', borderBottom: '1px solid var(--color-border)' }}>
          <tr>
            <th className="px-4 py-3 text-left text-xs font-semibold text-stone-400 uppercase tracking-wider w-12" />
            <th className="px-4 py-3 text-left text-xs font-semibold text-stone-400 uppercase tracking-wider w-12">No.</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-stone-400 uppercase tracking-wider">Description</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-stone-400 uppercase tracking-wider w-24">Qty</th>
            <th className="px-4 py-3 text-right text-xs font-semibold text-stone-400 uppercase tracking-wider w-32">
              Suggested Rate
            </th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-stone-400 uppercase tracking-wider w-32">Confidence</th>
            <th className="px-4 py-3 text-right text-xs font-semibold text-stone-400 uppercase tracking-wider w-28">Historical</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-stone-400 uppercase tracking-wider w-28">Gap</th>
            <th className="px-4 py-3 w-12" />
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, index) => (
            <React.Fragment key={index}>
              <tr
                className="cursor-pointer transition-colors"
                style={{
                  borderBottom: '1px solid var(--color-border)',
                  background: expandedRows[index] ? '#fafaf9' : '#ffffff',
                }}
                onMouseEnter={e => { if (!expandedRows[index]) e.currentTarget.style.background = '#fafaf9' }}
                onMouseLeave={e => { if (!expandedRows[index]) e.currentTarget.style.background = '#ffffff' }}
                onClick={() => toggleRow(index)}
              >
                {/* expand chevron */}
                <td className="pl-4 py-3 text-stone-300">
                  {expandedRows[index]
                    ? <ChevronDown className="w-3.5 h-3.5" />
                    : <ChevronRight className="w-3.5 h-3.5" />}
                </td>

                <td className="px-4 py-3 font-rate text-xs text-stone-400">{row.SNO || index + 1}</td>

                <td className="px-4 py-3">
                  <div className="max-w-sm text-stone-800 leading-snug line-clamp-2">
                    {row.DESCRIPTION}
                  </div>
                </td>

                <td className="px-4 py-3 text-stone-500 font-rate text-xs">
                  {row.QTY} {row.UNIT}
                </td>

                <td className="px-4 py-3 text-right">
                  <span className="font-rate font-medium text-base" style={{ color: 'var(--color-rate)' }}>
                    {formatPKR(row.SUGGESTED_RATE)}
                  </span>
                </td>

                <td className="px-4 py-3">
                  <ConfidenceBadge confidence={row.CONFIDENCE} />
                </td>

                <td className="px-4 py-3 text-right">
                  {row.HISTORICAL_RATE ? (
                    <div>
                      <span className="font-rate text-xs text-stone-600">{formatPKR(row.HISTORICAL_RATE)}</span>
                      {row.HISTORICAL_SOURCE && (
                        <div
                          className="text-xs truncate max-w-[100px] ml-auto"
                          style={{ color: 'var(--color-gold)' }}
                          title={row.HISTORICAL_SOURCE}
                        >
                          {row.HISTORICAL_SOURCE.length > 18
                            ? row.HISTORICAL_SOURCE.slice(0, 18) + '…'
                            : row.HISTORICAL_SOURCE}
                        </div>
                      )}
                    </div>
                  ) : (
                    <span className="text-stone-300">—</span>
                  )}
                </td>

                <td className="px-4 py-3">
                  <GapChip verdict={row.GAP_VERDICT} pct={row.GAP_PCT} />
                </td>

                {/* history button */}
                <td className="px-3 py-3 text-center">
                  {(row.SOURCES || row.HISTORICAL_RATE) && (
                    <button
                      onClick={(e) => { e.stopPropagation(); onShowHistory?.(row) }}
                      className="p-1.5 rounded-lg transition-colors"
                      style={{ color: 'var(--color-gold)' }}
                      onMouseEnter={e => { e.currentTarget.style.background = 'var(--color-gold-bg)' }}
                      onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
                      title="View historical matches"
                    >
                      <FolderOpen className="w-4 h-4" />
                    </button>
                  )}
                </td>
              </tr>

              {expandedRows[index] && (
                <tr key={`exp-${index}`} style={{ borderBottom: '1px solid var(--color-border)' }}>
                  <td colSpan={9} className="p-0">
                    <ExpandedRow row={row} />
                  </td>
                </tr>
              )}
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  )
}
