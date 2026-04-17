import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { UploadZone } from '../components/UploadZone'
import { useBoqStore } from '../store/boqStore'

const steps = [
  {
    number: '01',
    title: 'Upload Your BOQ',
    body: 'Upload any Excel file with Description, Qty, and Unit columns. Empty rate cells are filled automatically.',
  },
  {
    number: '02',
    title: 'AI Finds Rates',
    body: 'We check 1,429 past construction projects and live market prices to suggest a rate for every item.',
  },
  {
    number: '03',
    title: 'Review & Download',
    body: 'See each suggested rate with a confidence level and explanation. Download the filled Excel instantly.',
  },
]

const stats = [
  { value: '1,429', label: 'Historical BOQ items' },
  { value: '7', label: 'Construction categories' },
  { value: '±15%', label: 'Target accuracy' },
  { value: 'PKR', label: 'Pakistani Rupees' },
]

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
    <div className="min-h-screen flex flex-col" style={{ background: 'var(--color-bg)' }}>

      {/* ── Header ─────────────────────────────────────── */}
      <header style={{ background: 'var(--color-header)' }} className="px-8 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div
            className="w-8 h-8 rounded flex items-center justify-center text-white font-bold text-sm"
            style={{ background: 'var(--color-gold)' }}
          >
            R
          </div>
          <span className="text-white font-semibold tracking-wide text-sm">BOQ RateIQ</span>
        </div>
        <span className="text-xs px-2 py-1 rounded-full text-white/50 border border-white/10">
          v1.0
        </span>
      </header>

      {/* ── Hero ───────────────────────────────────────── */}
      <main className="flex-1">
        <section className="max-w-3xl mx-auto px-6 pt-16 pb-8 text-center">
          <div className="animate-fade-up">
            <span
              className="inline-block text-xs font-semibold uppercase tracking-widest px-3 py-1 rounded-full mb-6"
              style={{ background: 'var(--color-gold-bg)', color: 'var(--color-gold)' }}
            >
              Construction Cost Intelligence
            </span>
            <h1 className="font-display text-5xl leading-tight text-stone-900 mb-5">
              Your BOQ.<br />Priced in minutes.
            </h1>
            <p className="text-stone-500 text-lg leading-relaxed max-w-xl mx-auto mb-10">
              Upload your Bill of Quantities Excel file. Our AI checks past construction
              projects and current market prices to suggest a rate for every line item —
              with a clear confidence level and source.
            </p>
          </div>

          {/* ── Upload card ────────────────────────────── */}
          <div
            className="animate-fade-up-1 rounded-2xl shadow-sm border p-8 mb-4"
            style={{ background: 'var(--color-card)', borderColor: 'var(--color-border)' }}
          >
            <UploadZone onFileSelect={setSelectedFile} disabled={processing} />

            {selectedFile && (
              <div className="mt-6">
                <button
                  onClick={handleAnalyze}
                  disabled={processing}
                  className="w-full py-4 rounded-xl font-semibold text-white text-base transition-all active:scale-98 disabled:opacity-60 disabled:cursor-not-allowed"
                  style={{ background: processing ? '#92400e' : 'var(--color-gold)' }}
                >
                  {processing ? (
                    <span className="flex items-center justify-center gap-3">
                      <svg className="animate-spin-slow w-5 h-5 text-white/70" viewBox="0 0 24 24" fill="none">
                        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeDasharray="40 20" />
                      </svg>
                      Analyzing your BOQ — this takes 1–2 minutes…
                    </span>
                  ) : (
                    'Analyze My BOQ →'
                  )}
                </button>

                {processing && (
                  <div className="mt-4">
                    <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--color-border)' }}>
                      <div className="h-full rounded-full progress-bar-fill" style={{ background: 'var(--color-gold)' }} />
                    </div>
                    <p className="text-xs text-stone-400 mt-2 text-center">
                      Checking historical database and live market prices…
                    </p>
                  </div>
                )}

                {uploadError && (
                  <p className="mt-3 text-sm text-red-600 text-center">{uploadError}</p>
                )}
              </div>
            )}
          </div>

          <p className="text-xs text-stone-400 animate-fade-up-2">
            Accepts <strong>.xlsx</strong> files up to 10 MB · Max 50 items per upload · Rates in PKR
          </p>
        </section>

        {/* ── How it works ───────────────────────────── */}
        <section className="max-w-4xl mx-auto px-6 py-14">
          <h2
            className="text-center font-display text-2xl text-stone-800 mb-10"
          >
            How it works
          </h2>
          <div className="grid md:grid-cols-3 gap-6">
            {steps.map((step, i) => (
              <div
                key={step.number}
                className="rounded-xl p-6 border"
                style={{
                  background: 'var(--color-card)',
                  borderColor: 'var(--color-border)',
                  animationDelay: `${i * 0.1}s`,
                }}
              >
                <div
                  className="font-rate text-3xl font-medium mb-4"
                  style={{ color: 'var(--color-gold)' }}
                >
                  {step.number}
                </div>
                <h3 className="font-semibold text-stone-900 mb-2">{step.title}</h3>
                <p className="text-sm text-stone-500 leading-relaxed">{step.body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ── Stats bar ──────────────────────────────── */}
        <section
          className="border-t border-b"
          style={{ background: 'var(--color-card)', borderColor: 'var(--color-border)' }}
        >
          <div className="max-w-4xl mx-auto px-6 py-6 grid grid-cols-2 md:grid-cols-4 gap-6">
            {stats.map((s) => (
              <div key={s.label} className="text-center">
                <p className="font-rate text-2xl font-medium text-stone-800">{s.value}</p>
                <p className="text-xs text-stone-400 mt-1">{s.label}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      {/* ── Footer ─────────────────────────────────────── */}
      <footer className="px-8 py-5 text-center text-xs text-stone-400">
        BOQ RateIQ · Rates are suggestions only · Always verify before submitting a tender
      </footer>
    </div>
  )
}
