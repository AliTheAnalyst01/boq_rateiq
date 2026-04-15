import * as XLSX from 'xlsx'

export function parseExcelPreview(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const wb = XLSX.read(e.target.result, { type: 'array' })
        const sheet = wb.Sheets[wb.SheetNames[0]]
        const rows = XLSX.utils.sheet_to_json(sheet, { defval: '' })
        const headers = Object.keys(rows[0] || {})
        const required = ['DESCRIPTION', 'QTY', 'UNIT']
        const missing = required.filter(col => !headers.includes(col))
        resolve({
          headers,
          preview: rows.slice(0, 5),
          missing,
          isValid: missing.length === 0,
          totalRows: rows.length
        })
      } catch (err) {
        reject(new Error('Could not read this file. Please make sure it is a valid Excel (.xlsx) file.'))
      }
    }
    reader.readAsArrayBuffer(file)
  })
}

export function parseExcelData(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const wb = XLSX.read(e.target.result, { type: 'array' })
        const sheet = wb.Sheets[wb.SheetNames[0]]
        const rows = XLSX.utils.sheet_to_json(sheet, { defval: '' })
        resolve(rows)
      } catch (err) {
        reject(new Error('Could not parse the result file.'))
      }
    }
    reader.readAsArrayBuffer(blob)
  })
}
