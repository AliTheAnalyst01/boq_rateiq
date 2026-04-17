import { create } from 'zustand'
import { boqApi } from '../api/boqApi'
import { parseExcelData } from '../utils/excelParser'
import toast from 'react-hot-toast'

export const useBoqStore = create((set, get) => ({
  uploadStatus: 'idle',
  jobId: null,
  uploadError: null,
  processing: false,

  boqData: null,
  downloadBlob: null,
  downloadFileName: null,
  activeCategory: null,
  
  historicalModal: {
    isOpen: false,
    data: null,
    loading: false,
    error: null
  },

  uploadFile: async (file) => {
    set({ uploadStatus: 'uploading', uploadError: null, processing: true })
    try {
      const response = await boqApi.uploadFile(file)
      const blob = response.data

      const rows = await parseExcelData(blob)
      
      const enrichedRows = rows.filter(row => row.SUGGESTED_RATE)
      const totalItems = enrichedRows.length
      const totalCost = enrichedRows.reduce((sum, row) => {
        const amount = row.AMOUNT || (row.SUGGESTED_RATE * (row.QTY || 0))
        return sum + (amount || 0)
      }, 0)
      
      const itemsWithHistory = enrichedRows.filter(row => row.HISTORICAL_RATE).length
      
      const boqData = {
        totalItems,
        totalCost,
        itemsWithHistory,
        rows: enrichedRows,
        originalFileName: file.name
      }
      
      set({
        uploadStatus: 'complete',
        boqData,
        downloadBlob: blob,
        downloadFileName: `rateiq_filled_${file.name}`,
        processing: false,
      })
      toast.success('Your BOQ is ready!')
      return boqData
    } catch (err) {
      const errorMsg = err.message || 'Failed to process file'
      set({ uploadStatus: 'error', uploadError: errorMsg, processing: false })
      toast.error(errorMsg)
      throw err
    }
  },

  resetAll: () => {
    set({
      uploadStatus: 'idle',
      jobId: null,
      uploadError: null,
      processing: false,
      boqData: null,
      downloadBlob: null,
      downloadFileName: null,
      activeCategory: null,
      historicalModal: {
        isOpen: false,
        data: null,
        loading: false,
        error: null
      }
    })
  },

  setActiveCategory: (category) => {
    set({ activeCategory: category })
  },

  openHistoricalModal: (data) => {
    set({ historicalModal: { isOpen: true, data, loading: false, error: null } })
  },

  closeHistoricalModal: () => {
    set({ historicalModal: { isOpen: false, data: null, loading: false, error: null } })
  }
}))
