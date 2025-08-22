'use client'

import { useState } from 'react'
import axios from 'axios'

export default function Home() {
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<any>(null)
  const [fullText, setFullText] = useState<string>('')
  const [loading, setLoading] = useState(false)

  const handleUpload = async () => {
    if (!file) return
    setLoading(true)
    setResult(null)
    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await axios.post('http://127.0.0.1:8000/api/upload', formData, {
        headers: { 'Content-Type': 'multipart-form-data' }
      })
      setResult(res.data)
    } catch (err) {
      console.error('アップロード失敗:', err)
      alert('アップロードに失敗しました。')
    } finally {
      setLoading(false)
    }
  }

  const handleUploadText = async () => {
    if (!file) return
    setLoading(true)
    setFullText('')
    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await axios.post('http://127.0.0.1:8000/api/upload/text', formData, {
        headers: { 'Content-Type': 'multipart-form-data' }
      })
      setFullText(res.data.text)
    } catch (err) {
      console.error('テキスト抽出失敗:', err)
      alert('テキスト抽出に失敗しました。')
    } finally {
      setLoading(false)
    }
  }

  const styles = {
    container: {
      backgroundColor: '#f4f7f6',
      minHeight: '100vh',
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      padding: '20px',
      fontFamily: 'sans-serif'
    },
    card: {
      backgroundColor: 'white',
      padding: '40px',
      borderRadius: '10px',
      boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
      maxWidth: '600px',
      width: '100%',
      textAlign: 'center'
    },
    button: {
      backgroundColor: '#007bff',
      color: 'white',
      border: 'none',
      padding: '12px 24px',
      borderRadius: '5px',
      cursor: 'pointer',
      fontSize: '16px',
      margin: '0 5px',
      transition: 'background-color 0.2s'
    },
    // ★追加: ファイル選択ボタン用のスタイル
    fileInputButton: {
      backgroundColor: '#6c757d', // メインのボタンとは色を変える
      color: 'white',
      padding: '12px 24px',
      borderRadius: '5px',
      cursor: 'pointer',
      display: 'inline-block', // labelタグをボタンのように見せる
      marginBottom: '10px'
    },
    // ★追加: 選択されたファイル名を表示するエリアのスタイル
    fileName: {
      marginTop: '15px',
      color: '#333',
      fontSize: '14px',
    },
    resultBox: {
      marginTop: '30px',
      backgroundColor: '#f8f9fa',
      padding: '20px',
      borderRadius: '8px',
      textAlign: 'left',
      border: '1px solid #dee2e6'
    }
  }

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <h1 style={{ color: 'blue' }}>📄 出品票PDFアップロード</h1>
        
        {/* ▼▼▼ ファイル選択部分をここから修正 ▼▼▼ */}
        <div>
          {/* 1. クリックするためのカスタムボタン (label) */}
          <label htmlFor="file-upload" style={styles.fileInputButton}>
            PDFファイルを選択
          </label>
          
          {/* 2. 実際のinputタグは非表示にする */}
          <input
            id="file-upload" // labelと連携させるためのID
            type="file"
            accept="application/pdf"
            style={{ display: 'none' }} // このスタイルで非表示に
            onChange={(e) => {
              setFile(e.target.files?.[0] || null)
              setResult(null)
              setFullText('')
            }}
          />

          {/* 3. 選択されたファイル名を表示 */}
          {file && <p style={styles.fileName}>選択中のファイル: {file.name}</p>}
        </div>
        {/* ▲▲▲ ファイル選択部分の修正はここまで ▲▲▲ */}

        <div style={{ marginTop: '20px' }}>
          <button onClick={handleUpload} style={styles.button} disabled={loading || !file}>
            {loading ? '解析中...' : 'アップロードして解析'}
          </button>
          <button onClick={handleUploadText} style={styles.button} disabled={loading || !file}>
            全文テキストを表示
          </button>
        </div>

        {result && (
          <div style={styles.resultBox}>
            <h2>🚘 抽出結果</h2>
            <p><strong>出品番号:</strong> {result.listing_id || 'N/A'}</p>
            <p><strong>車種:</strong> {result.model || 'N/A'}</p>
            <p><strong>年式:</strong> {result.year || 'N/A'}</p>
            <p><strong>走行距離:</strong> {result.distance_km?.toLocaleString() || 'N/A'} km</p>
          </div>
        )}

        {fullText && (
          <div style={styles.resultBox}>
            <h2>📘 PDF全文テキスト</h2>
            <pre
              style={{
                whiteSpace: 'pre-wrap',
                background: 'transparent',
                padding: 0,
                maxHeight: 400,
                overflowY: 'auto'
              }}
            >
              {fullText}
            </pre>
          </div>
        )}
      </div>
    </div>
  )
}