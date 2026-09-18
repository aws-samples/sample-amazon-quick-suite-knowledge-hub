import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createEmbeddingContext } from 'amazon-quicksight-embedding-sdk';
import './ChatPopup.css';

const cognitoConfig = {
  clientId: process.env.REACT_APP_COGNITO_CLIENT_ID,
  domain: process.env.REACT_APP_COGNITO_DOMAIN,
  redirectUri: window.location.origin,
};

// Exchange auth code for tokens using Cognito token endpoint
async function exchangeCodeForTokens(code) {
  const params = new URLSearchParams({
    grant_type: 'authorization_code',
    client_id: cognitoConfig.clientId,
    redirect_uri: cognitoConfig.redirectUri,
    code,
  });
  const res = await fetch(`${cognitoConfig.domain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params.toString(),
  });
  if (!res.ok) throw new Error('Token exchange failed');
  return res.json();
}

async function refreshTokens(refreshToken) {
  const params = new URLSearchParams({
    grant_type: 'refresh_token',
    client_id: cognitoConfig.clientId,
    refresh_token: refreshToken,
  });
  const res = await fetch(`${cognitoConfig.domain}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params.toString(),
  });
  if (!res.ok) throw new Error('Token refresh failed');
  return res.json();
}

function isTokenExpired(token) {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.exp < Math.floor(Date.now() / 1000) + 60;
  } catch (_) { return true; }
}

function parseToken(token) {
  try { return JSON.parse(atob(token.split('.')[1])); }
  catch (_) { return {}; }
}

// ── Chat panel — receives embedUrl directly, no API call ─────────────────────
function ChatPanel({ chatEmbedUrl, agentId }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [isEmbedded, setIsEmbedded] = useState(false);
  const containerRef = useRef(null);

  useEffect(() => {
    if (isEmbedded || !chatEmbedUrl) return;
    const embed = async () => {
      setLoading(true);
      setError('');
      try {
        let attempts = 0;
        while (!containerRef.current && attempts < 20) {
          await new Promise(r => setTimeout(r, 100));
          attempts++;
        }
        if (!containerRef.current) throw new Error('Container not ready');
        const ctx = await createEmbeddingContext();
        await ctx.embedQuickChat(
          { url: chatEmbedUrl, container: containerRef.current, height: '100%', width: '100%',
            withIframePlaceholder: true,
            onChange: (e) => { if (e.eventName === 'FRAME_LOADED') setLoading(false); }
          },
          {
            agentOptions: agentId ? { fixedAgentId: agentId } : undefined,
            promptOptions: { allowFileAttachments: true, showWebSearch: true },
            footerOptions: { showBrandAttribution: false },
            onMessage: (e) => { if (e.eventName === 'CONTENT_LOADED') setLoading(false); }
          }
        );
        setIsEmbedded(true);
      } catch (err) {
        setError(err.message || 'Failed to load chat');
        setLoading(false);
      }
    };
    embed();
  }, [chatEmbedUrl, isEmbedded, agentId]);

  return (
    <div className="qs-chat-panel">
      <div className="qs-chat-panel-header">
        <span>Finance AI Assistant</span>
        <small>Ask questions about the dashboard data</small>
      </div>
      <div ref={containerRef} className="qs-chat-panel-body">
        {loading && <div className="chat-loading"><div className="chat-spinner" /><span>Loading assistant...</span></div>}
        {error && <div className="chat-error"><p>{error}</p><button onClick={() => { setError(''); setIsEmbedded(false); }}>Retry</button></div>}
        {!chatEmbedUrl && !loading && !error && <div className="chat-loading"><span>Waiting for dashboard...</span></div>}
      </div>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────────────────
export default function ChatPopup() {
  const [user, setUser] = useState(null);
  const [idToken, setIdToken] = useState('');
  const [refreshToken, setRefreshToken] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [isEmbedded, setIsEmbedded] = useState(false);
  const [chatEmbedUrl, setChatEmbedUrl] = useState('');
  const containerRef = useRef(null);

  const apiEndpoint = process.env.REACT_APP_QUICKCHAT_API_ENDPOINT;
  const agentId = process.env.REACT_APP_QUICKSUITE_AGENT_ID;

  const applyToken = useCallback((token) => {
    setIdToken(token);
    const payload = parseToken(token);
    setUser({ email: payload.email, name: payload.name || payload.email });
  }, []);

  const getFreshToken = useCallback(async (currentToken, currentRefresh) => {
    if (!isTokenExpired(currentToken)) return currentToken;
    if (!currentRefresh) return null;
    try {
      const tokens = await refreshTokens(currentRefresh);
      const newToken = tokens.id_token;
      sessionStorage.setItem('qs_id_token', newToken);
      applyToken(newToken);
      return newToken;
    } catch (_) { return null; }
  }, [applyToken]);

  // On mount: handle auth callback or restore session
  useEffect(() => {
    const hash = window.location.hash.substring(1);
    const hashParams = new URLSearchParams(hash);
    const hashToken = hashParams.get('id_token');
    if (hashToken) {
      window.history.replaceState({}, document.title, window.location.pathname);
      sessionStorage.setItem('qs_id_token', hashToken);
      sessionStorage.removeItem('qs_refresh_token');
      applyToken(hashToken);
      return;
    }

    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    if (code) {
      window.history.replaceState({}, document.title, window.location.pathname);
      exchangeCodeForTokens(code).then(tokens => {
        sessionStorage.setItem('qs_id_token', tokens.id_token);
        if (tokens.refresh_token) sessionStorage.setItem('qs_refresh_token', tokens.refresh_token);
        setRefreshToken(tokens.refresh_token || '');
        applyToken(tokens.id_token);
      }).catch(() => {});
      return;
    }

    const storedToken = sessionStorage.getItem('qs_id_token');
    const storedRefresh = sessionStorage.getItem('qs_refresh_token');
    if (storedRefresh) setRefreshToken(storedRefresh);

    if (storedToken && !isTokenExpired(storedToken)) {
      applyToken(storedToken);
    } else if (storedRefresh) {
      refreshTokens(storedRefresh).then(tokens => {
        sessionStorage.setItem('qs_id_token', tokens.id_token);
        applyToken(tokens.id_token);
      }).catch(() => {
        sessionStorage.clear();
        setUser(null); setIdToken(''); setRefreshToken('');
      });
    } else {
      sessionStorage.clear();
    }
  }, [applyToken]);

  const handleSignIn = () => {
    const url = `${cognitoConfig.domain}/oauth2/authorize?` +
      `response_type=token&client_id=${cognitoConfig.clientId}&` +
      `redirect_uri=${encodeURIComponent(cognitoConfig.redirectUri)}&` +
      `scope=${encodeURIComponent('openid email profile')}&` +
      `prompt=login`;
    window.location.href = url;
  };

  const handleSignOut = () => {
    sessionStorage.clear();
    setUser(null); setIdToken(''); setRefreshToken('');
    setIsOpen(false); setIsEmbedded(false); setError(''); setChatEmbedUrl('');
    window.location.href = `${cognitoConfig.domain}/logout?client_id=${cognitoConfig.clientId}&logout_uri=${encodeURIComponent(cognitoConfig.redirectUri)}`;
  };

  // Single API call for both dashboard + chat embed URLs
  useEffect(() => {
    if (!isOpen || isEmbedded || !idToken) return;

    const embed = async () => {
      setLoading(true);
      setError('');
      try {
        const token = await getFreshToken(idToken, refreshToken);
        if (!token) {
          sessionStorage.clear();
          setUser(null); setIdToken('');
          setLoading(false);
          return;
        }

        let res;
        try {
          res = await fetch(apiEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
            body: JSON.stringify({ idToken: token, embedType: 'both' }),
          });
        } catch (fetchErr) {
          throw new Error('Unable to reach embedding API — CORS may not be enabled for this origin');
        }
        const data = await res.json();
        if (data.status !== 'SUCCESS' || !data.embedUrl) throw new Error(data.status || 'No embed URL');

        // Pass chat URL to ChatPanel
        if (data.chatEmbedUrl) setChatEmbedUrl(data.chatEmbedUrl);

        let attempts = 0;
        while (!containerRef.current && attempts < 20) {
          await new Promise(r => setTimeout(r, 100));
          attempts++;
        }
        if (!containerRef.current) throw new Error('Container not ready');

        const ctx = await createEmbeddingContext();
        await ctx.embedDashboard(
          { url: data.embedUrl, container: containerRef.current, height: '100%', width: '100%',
            withIframePlaceholder: true,
            onChange: (e) => {
              if (e.eventName === 'FRAME_LOADED') setLoading(false);
              if (e.eventName === 'ERROR_OCCURRED') { setError('Dashboard error'); setLoading(false); }
            }
          },
          {
            toolbarOptions: { export: false, undoRedo: false, reset: false },
            onMessage: async (e) => {
              if (e.eventName === 'CONTENT_LOADED') setLoading(false);
              if (e.eventName === 'ERROR_OCCURRED') { setError('Load error'); setLoading(false); }
            }
          }
        );
        setIsEmbedded(true);
      } catch (err) {
        setError(err.message || 'Failed to load dashboard');
        setLoading(false);
      }
    };
    embed();
  }, [isOpen, idToken, isEmbedded, apiEndpoint, refreshToken, getFreshToken]);

  if (!user) {
    return (
      <div className="chat-fab-wrapper">
        <button className="chat-fab" onClick={handleSignIn} title="Sign in to access Finance Assistant">
          <svg width="26" height="26" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
          </svg>
        </button>
      </div>
    );
  }

  return (
    <>
      <div className="chat-auth-bar">
        <span>{user.name || user.email}</span>
        <button onClick={handleSignOut} className="chat-signout-btn">Sign Out</button>
      </div>
      <div className="chat-fab-wrapper">
        <button className={`chat-fab ${isOpen ? 'chat-fab--open' : ''}`}
          onClick={() => setIsOpen(o => !o)}>
          {isOpen
            ? <svg width="26" height="26" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
            : <svg width="26" height="26" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>
          }
        </button>
      </div>
      {isOpen && (
        <div className="qs-dashboard-overlay">
          <div className="qs-dashboard-header">
            <span>AnyCompany Finance Dashboard</span>
            <small>Click the chat button below to ask the AI assistant questions about this data</small>
            <button className="qs-close-btn" onClick={() => setIsOpen(false)}>✕ Close</button>
          </div>
          <div className="qs-dashboard-layout">
            <div ref={containerRef} className="qs-dashboard-container">
              {loading && <div className="chat-loading"><div className="chat-spinner" /><span>Loading Finance Dashboard...</span></div>}
              {error && <div className="chat-error"><p>{error}</p><button onClick={() => { setError(''); setIsEmbedded(false); setChatEmbedUrl(''); }}>Retry</button></div>}
            </div>
            <ChatPanel chatEmbedUrl={chatEmbedUrl} agentId={agentId} />
          </div>
        </div>
      )}
    </>
  );
}
