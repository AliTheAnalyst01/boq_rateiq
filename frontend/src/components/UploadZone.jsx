import React, { useState, useRef } from 'react'
import { Upload, FileSpreadsheet, Check, AlertCircle, X } from 'lucide-react'
import { parseExcelPreview } from '../utils/excelParser'
import toast from 'react-hot-toast'

export function UploadZone({ onFileSelect, disabled }) {
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const fileInputRef = useRef(null)

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
        setError(`Missing required columns: ${result.missing.join(', ')}`)
        return
      }
      
      onFileSelect?.(selectedFile)
    } catch (err) {
      setError(err.message)
      toast.error(err.message)
    }
  }

  // Click handler - directly opens file picker using native method
  const openFilePicker = async () => {
    try {
      // Create input element directly and click it
      const input = document.createElement('input')
      input.type = 'file'
      input.accept = '.xlsx,.xls'
      input.onchange = (e) => {
        const selectedFile = e.target.files[0]
        if (selectedFile) {
          handleFile(selectedFile)
        }
      }
      input.click()
    } catch (err) {
      console.error("File picker error:", err)
      toast.error('Could not open file picker')
    }
  }

  const clearFile = (e) => {
    e.stopPropagation()
    setFile(null)
    setPreview(null)
    setError(null)
  }

  return (
    <div className="w-full max-w-2xl mx-auto p-8">
      {/* CLICKABLE AREA - Opens file picker */}
      <div 
        onClick={openFilePicker}
        className="border-2 border-dashed border-gray-300 rounded-xl p-8 text-center cursor-pointer hover:border-gray-400 hover:bg-gray-50 transition-colors"
        style={{ borderColor: '#ef4444', borderWidth: '5px', background: '#fef08a' }}
      >
        <div className="flex flex-col items-center gap-4">
          <Upload className="w-12 h-12 text-gray-600" />
          
          <div>
            <p className="text-xl font-bold text-gray-800">
              CLICK HERE TO UPLOAD FILE
            </p>
            <p className="text-gray-600 mt-2">
              Select your BOQ Excel file (.xlsx)
            </p>
          </div>
        </div>
      </div>

      {file && (
        <div className="mt-4 p-4 bg-white rounded-lg border border-gray-200">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <FileSpreadsheet className="w-5 h-5 text-green-600" />
              <div>
                <p className="font-medium text-gray-800">{file.name}</p>
                <p className="text-sm text-gray-500">
                  {(file.size / 1024).toFixed(1)} KB • {preview?.totalRows || 0} rows
                </p>
              </div>
            </div>
            <button onClick={clearFile} className="p-1 hover:bg-gray-100 rounded">
              <X className="w-4 h-4 text-gray-400" />
            </button>
          </div>
          
          {preview?.isValid && (
            <div className="mt-3 flex items-center gap-2 text-green-600 text-sm">
              <Check className="w-4 h-4" />
              <span>Valid format - contains DESCRIPTION, QTY, UNIT columns</span>
            </div>
          )}
          
          {error && (
            <div className="mt-3 flex items-center gap-2 text-red-600 text-sm">
              <AlertCircle className="w-4 h-4" />
              <span>{error}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
