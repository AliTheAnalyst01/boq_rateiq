import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UploadZone } from '../components/UploadZone'
import { useBoqStore } from '../store/boqStore'
import { FileText, Cpu, BarChart3 } from 'lucide-react'

export function HomePage() {
  const navigate = useNavigate()
  const { uploadFile, processing, uploadError } = useBoqStore()
  const [selectedFile, setSelectedFile] = useState(null)

  const handleAnalyze = async () => {
    if (!selectedFile) return
    try {
      const result = await uploadFile(selectedFile)
      navigate('/dashboard', { state: { boqData: result } })
    } catch (err) {
      console.error(err)
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-4xl mx-auto px-6 py-16">
        <div className="text-center mb-12">
          <h1 className="text-3xl font-bold text-gray-900 mb-3">
            Upload your BOQ. Get rates in minutes.
          </h1>
          <p className="text-gray-600 text-lg">
            Smart rate suggestions powered by historical project data and market research
          </p>
        </div>

        <div className="mb-8">
          <UploadZone 
            onFileSelect={setSelectedFile} 
            disabled={processing}
          />
        </div>

        {selectedFile && (
          <div className="text-center">
            <button
              onClick={handleAnalyze}
              disabled={processing}
              className="px-8 py-3 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {processing ? 'Processing...' : 'Analyze My BOQ'}
            </button>
            {uploadError && (
              <p className="mt-3 text-red-600">{uploadError}</p>
            )}
          </div>
        )}

        <div className="mt-16 flex justify-center gap-12">
          <div className="flex flex-col items-center text-center">
            <div className="w-12 h-12 bg-blue-100 rounded-full flex items-center justify-center mb-3">
              <FileText className="w-6 h-6 text-blue-600" />
            </div>
            <p className="font-medium text-gray-800">Upload Your File</p>
            <p className="text-sm text-gray-500 mt-1">Upload your BOQ Excel file</p>
          </div>
          
          <div className="flex items-center pt-6">
            <div className="w-8 h-px bg-gray-300" />
          </div>

          <div className="flex flex-col items-center text-center">
            <div className="w-12 h-12 bg-purple-100 rounded-full flex items-center justify-center mb-3">
              <Cpu className="w-6 h-6 text-purple-600" />
            </div>
            <p className="font-medium text-gray-800">AI Finds Rates</p>
            <p className="text-sm text-gray-500 mt-1">Checks market prices and past projects</p>
          </div>

          <div className="flex items-center pt-6">
            <div className="w-8 h-px bg-gray-300" />
          </div>

          <div className="flex flex-col items-center text-center">
            <div className="w-12 h-12 bg-green-100 rounded-full flex items-center justify-center mb-3">
              <BarChart3 className="w-6 h-6 text-green-600" />
            </div>
            <p className="font-medium text-gray-800">View & Download</p>
            <p className="text-sm text-gray-500 mt-1">See suggested rates and download report</p>
          </div>
        </div>
      </div>
    </div>
  )
}
