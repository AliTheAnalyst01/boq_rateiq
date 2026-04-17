import React, { useState, useRef } from 'react'
import { FileSpreadsheet, Check, AlertCircle, X, Upload } from 'lucide-react'
import { parseExcelPreview } from '../utils/excelParser'
import toast from 'react-hot-toast'

export function UploadZone({ onFileSelect, disabled }) {
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const [dragging, setDragging] = useState(false)

  const handleFile = async (selectedFile) => {
    if (!selectedFile.name.endsWith('.xlsx') && !selectedFile.name.endsWith('.xls')) {
      toast.error('Only Excel (.xlsx) files are accepted')
      return
    }

    setFile(selectedFile)
    setError(null)

    try {
      const result = await parseExcelPreview(selectedFile)
      setPreview(result)

      if (!result.isValid) {
        setError(`Missing columns: ${result.missing.join(', ')}. Your file must have DESCRIPTION, QTY, and UNIT columns.`)
        return
      }

      onFileSelect?.(selectedFile)
    } catch (err) {
      setError(err.message)
      toast.error(err.message)
    }
  }

  const openFilePicker = () => {
    if (disabled) return
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = '.xlsx,.xls'
    input.onchange = (e) => {
      const f = e.target.files[0]
      if (f) handleFile(f)
    }
    input.click()
  }

  const onDragOver = (e) => { e.preventDefault(); setDragging(true) }
  const onDragLeave = () => setDragging(false)
  const onDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  const clearFile = (e) => {
    e.stopPropagation()
    setFile(null)
    setPreview(null)
    setError(null)
    onFileSelect?.(null)
  }

  return (
    <div className="w-full">
      {/* Drop zone */}
      {!file ? (
        <div
          onClick={openFilePicker}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
          className={`rounded-xl border-2 border-dashed p-10 flex flex-col items-center gap-4 cursor-pointer transition-colors select-none ${dragging ? 'drop-zone-active' : ''}`}
          style={{
            borderColor: dragging ? 'var(--color-gold)' : 'var(--color-border)',
            background: dragging ? 'var(--color-gold-bg)' : 'var(--color-bg)',
          }}
        >
          <div
            className="w-14 h-14 rounded-2xl flex items-center justify-center"
            style={{ background: 'var(--color-gold-bg)' }}
          >
            <Upload className="w-7 h-7" style={{ color: 'var(--color-gold)' }} />
          </div>
          <div className="text-center">
            <p className="font-semibold text-stone-800 text-base">
              Click to upload or drag & drop
            </p>
            <p className="text-sm text-stone-400 mt-1">
              Excel file (.xlsx) · Max 10 MB · Up to 50 items
            </p>
          </div>
        </div>
      ) : (
        /* File selected state */
        <div
          className="rounded-xl border p-5"
          style={{ borderColor: error ? '#fca5a5' : 'var(--color-border)', background: 'var(--color-bg)' }}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <div
                className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0"
                style={{ background: error ? '#fee2e2' : 'var(--color-gold-bg)' }}
              >
                <FileSpreadsheet
                  className="w-5 h-5"
                  style={{ color: error ? '#dc2626' : 'var(--color-gold)' }}
                />
              </div>
              <div>
                <p className="font-medium text-stone-800 text-sm">{file.name}</p>
                <p className="text-xs text-stone-400 mt-0.5">
                  {(file.size / 1024).toFixed(0)} KB
                  {preview && ` · ${preview.totalRows} rows`}
                </p>
              </div>
            </div>
            <button
              onClick={clearFile}
              className="p-1.5 rounded-lg hover:bg-stone-100 transition-colors flex-shrink-0"
              title="Remove file"
            >
              <X className="w-4 h-4 text-stone-400" />
            </button>
          </div>

          {preview?.isValid && !error && (
            <div className="flex items-center gap-2 mt-3 text-sm" style={{ color: 'var(--color-rate)' }}>
              <Check className="w-4 h-4" />
              <span>Valid file — {preview.totalRows} rows ready to price</span>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 mt-3 text-sm text-red-600">
              <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
