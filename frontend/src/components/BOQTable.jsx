import React, { useState } from 'react'
import { ChevronDown, ChevronRight, FolderOpen, ExternalLink, Info } from 'lucide-react'
import { formatPKR, truncate, formatConfidence, formatGapVerdict } from '../utils/formatters'

function ConfidenceBadge({ confidence }) {
  const { label, color, icon } = formatConfidence(confidence)
  const colors = {
    green: 'bg-green-100 text-green-700',
    yellow: 'bg-yellow-100 text-yellow-700',
    red: 'bg-red-100 text-red-700'
  }
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${colors[color]}`}>
      {icon} {label}
    </span>
  )
}

function GapChip({ verdict, pct }) {
  const { label, color } = formatGapVerdict(verdict, pct)
  const colors = {
    green: 'bg-green-100 text-green-700',
    yellow: 'bg-yellow-100 text-yellow-700',
    red: 'bg-red-100 text-red-700'
  }
  return (
    <span className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium ${colors[color]}`}>
      {label}
    </span>
  )
}

function RowExpanded({ row }) {
  // Strip raw URLs from explanation — they're shown as clickable icons elsewhere
  const cleanExplanation = (text) =>
    text ? text.replace(/https?:\/\/\S+/g, '').replace(/\s{2,}/g, ' ').trim() : ''

  return (
    <div className="bg-gray-50 p-4 text-sm">
      <p className="font-medium text-gray-800 mb-2">{row.DESCRIPTION}</p>
      {row.EXPLANATION && (
        <p className="text-gray-600 mb-3">{cleanExplanation(row.EXPLANATION)}</p>
      )}
      
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-3">
        {row.SOURCES && (
          <div>
            <span className="text-xs text-gray-500 uppercase">Sources</span>
            <div className="flex flex-wrap gap-1 mt-1">
              {String(row.SOURCES).split(',').map((src, i) => (
                <span key={i} className="px-2 py-0.5 bg-gray-200 rounded text-xs">
                  {src.trim()}
                </span>
              ))}
            </div>
          </div>
        )}
        
        {row.MARKET_SOURCE && (
          <div>
            <span className="text-xs text-gray-500 uppercase">Market Source</span>
            <p className="text-blue-600">
              {row.MARKET_SOURCE}
              {row.MARKET_RATE_URL && (
                <a href={row.MARKET_RATE_URL} target="_blank" rel="noreferrer" className="ml-1 inline-flex">
                  <ExternalLink className="w-3 h-3" />
                </a>
              )}
            </p>
          </div>
        )}
        
        {row.SEARCH_KEYWORDS && (
          <div>
            <span className="text-xs text-gray-500 uppercase">Search Terms</span>
            <p className="text-gray-600 italic">{row.SEARCH_KEYWORDS}</p>
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
      <div className="text-center py-12 text-gray-500">
        No data to display
      </div>
    )
  }

  const toggleRow = (index) => {
    setExpandedRows(prev => ({
      ...prev,
      [index]: !prev[index]
    }))
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 border-b border-gray-200">
          <tr>
            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase w-16">SNO</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase">Description</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase w-24">Qty</th>
            <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase w-28">Rate</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase w-32">Confidence</th>
            <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 uppercase w-28">Historical</th>
            <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase w-28">Gap</th>
            <th className="px-4 py-3 text-center text-xs font-semibold text-gray-500 uppercase w-16">Source</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {data.rows.map((row, index) => (
            <React.Fragment key={index}>
              <tr 
                className={`hover:bg-gray-50 cursor-pointer ${expandedRows[index] ? 'bg-gray-50' : ''}`}
                onClick={() => toggleRow(index)}
              >
                <td className="px-4 py-3 font-mono text-gray-600">{row.SNO || index + 1}</td>
                <td className="px-4 py-3">
                  <div className="max-w-md truncate">{row.DESCRIPTION}</div>
                </td>
                <td className="px-4 py-3 text-gray-600">
                  {row.QTY} {row.UNIT}
                </td>
                <td className="px-4 py-3 text-right font-medium text-blue-600">
                  {formatPKR(row.SUGGESTED_RATE)}
                </td>
                <td className="px-4 py-3">
                  <ConfidenceBadge confidence={row.CONFIDENCE} />
                </td>
                <td className="px-4 py-3 text-right text-gray-600">
                  {row.HISTORICAL_RATE ? (
                    <div className="flex flex-col items-end">
                      <span>{formatPKR(row.HISTORICAL_RATE)}</span>
                      {row.HISTORICAL_SOURCE && (
                        <span className="text-xs text-amber-600" title={row.HISTORICAL_SOURCE}>
                          {row.HISTORICAL_SOURCE.length > 15 
                            ? row.HISTORICAL_SOURCE.slice(0, 15) + '...' 
                            : row.HISTORICAL_SOURCE}
                        </span>
                      )}
                    </div>
                  ) : '—'}
                </td>
                <td className="px-4 py-3">
                  <GapChip verdict={row.GAP_VERDICT} pct={row.GAP_PCT} />
                </td>
                <td className="px-4 py-3 text-center">
                  {(row.SOURCES || row.HISTORICAL_RATE) && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        onShowHistory?.(row)
                      }}
                      className="p-1.5 hover:bg-amber-100 rounded text-amber-600"
                      title="View historical matches"
                    >
                      <FolderOpen className="w-4 h-4" />
                    </button>
                  )}
                </td>
              </tr>
              {expandedRows[index] && (
                <tr key={`expanded-${index}`}>
                  <td colSpan={8} className="p-0">
                    <RowExpanded row={row} />
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
