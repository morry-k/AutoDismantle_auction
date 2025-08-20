'use client'

import { useState } from 'react'
import axios from 'axios'

export default function Home() {
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<any>(null)
  const [fullText, setFullText] = useState<string>('')

  const handleUpload = async () => {
    if (!file) return
    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await axios.post('http://127.0.0.1:8000/api/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      setResult(res.data)
    } catch (err) {
      console.error('アップロード失敗:', err)
    }
  }

  const handleUploadText = async () => {
    if (!file) return
    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await axios.post('http://127.0.0.1:8000/api/upload/text', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      setFullText(res.data.text)
    } catch (err) {
      console.error('テキスト抽出失敗:', err)
    }
  }

  return (
    <div style={{ padding: 20 }}>
      <h1>📄 出品票PDFアップロード</h1>
      <input
        type="file"
        accept="application/pdf"
        onChange={(e) => {
          setFile(e.target.files?.[0] || null)
          setResult(null)
          setFullText('')
        }}
      />
      <div style={{ marginTop: 10 }}>
        <button onClick={handleUpload} style={{ marginRight: 10 }}>
          アップロードして解析
        </button>
        <button onClick={handleUploadText}>
          全文テキストを表示
        </button>
      </div>

      {result && (
        <div style={{ marginTop: 20 }}>
          <h2>🚘 抽出結果</h2>
          <p><strong>出品番号:</strong> {result.listing_id}</p>
          <p><strong>車種:</strong> {result.model}</p>
          <p><strong>年式:</strong> {result.year}</p>
          <p><strong>走行距離:</strong> {result.distance_km.toLocaleString()} km</p>
        </div>
      )}

      {fullText && (
        <div style={{ marginTop: 30 }}>
          <h2>📘 PDF全文テキスト</h2>
          <pre
            style={{
              whiteSpace: 'pre-wrap',
              background: '#f8f8f8',
              padding: 10,
              borderRadius: 5,
              maxHeight: 400,
              overflowY: 'auto'
            }}
          >
            {fullText}
          </pre>
        </div>
      )}
    </div>
  )
}
