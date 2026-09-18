import React, { useState, useEffect } from 'react';
import './App.css';
import Dashboard from './components/Dashboard';
import ChatPopup from './components/ChatPopup';
import { fetchFinanceData } from './services/api';

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const result = await fetchFinanceData();
      setData(result);
    } catch (error) {
      console.error('Error loading data:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="App">
      <header className="App-header">
        <div className="header-content">
          <div className="logo-section">
            <div className="logo">AC</div>
            <div className="company-info">
              <h1>AnyCompany</h1>
              <p className="tagline">Finance Performance Dashboard</p>
            </div>
          </div>
        </div>
      </header>
      <main>
        {loading ? (
          <div className="loading">Loading...</div>
        ) : (
          <Dashboard data={data} />
        )}
      </main>
      <footer className="App-footer">
        <p>&copy; 2026 AnyCompany. All rights reserved.</p>
      </footer>
      <ChatPopup />
    </div>
  );
}

export default App;
