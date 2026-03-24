import { useState } from 'react'
import './App.css'

function App() {
  const [apiResult, setApiResult] = useState('Not tested yet')

  const testApi = async () => {
    try {
      const response = await fetch('/api/test')
      const data = await response.json()
      setApiResult(JSON.stringify(data))
    } catch (error) {
      setApiResult(`Request failed: ${error.message}`)
    }
  }

  return (
    <main style={{ maxWidth: 720, margin: '64px auto', fontFamily: 'system-ui, sans-serif' }}>
      <h1>Dev Linker Demo</h1>
      <p>Frontend is running. Click the button to call the backend via /api/test.</p>
      <button onClick={testApi} style={{ padding: '10px 18px', marginTop: 12 }}>
        Test API
      </button>
      <pre style={{ marginTop: 20, padding: 16, background: '#f4f4f4', borderRadius: 8 }}>
        {apiResult}
      </pre>
    </main>
  )
}

export default App
