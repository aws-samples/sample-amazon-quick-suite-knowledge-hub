import React, { useState, useEffect, useRef } from 'react';
import { createEmbeddingContext } from 'amazon-quicksight-embedding-sdk';
import cognitoConfig from '../lib/amplify-config';

interface ChatPopupProps {
  idToken: string;
  apiEndpoint: string;
  onTokenRefresh: (newToken: string) => void;
}

export default function ChatPopup({ idToken, apiEndpoint, onTokenRefresh }: ChatPopupProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isMinimized, setIsMinimized] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>('');
  const [showWelcome, setShowWelcome] = useState(true);
  const [hasNotification, setHasNotification] = useState(false);
  const [isEmbedded, setIsEmbedded] = useState(false);
  const [tokenExpired, setTokenExpired] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const toggleChat = () => {
    if (isOpen && !isMinimized) {
      setIsMinimized(true);
    } else {
      setIsOpen(true);
      setIsMinimized(false);
      setHasNotification(false);
      if (isEmbedded) {
        setShowWelcome(false);
      }
      setError('');
    }
  };

  const restoreChat = () => {
    setIsMinimized(false);
  };

  const startChat = () => {
    setShowWelcome(false);
  };

  const callEmbeddingAPI = async (token: string): Promise<any> => {
    const response = await fetch(apiEndpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({ idToken: token })
    });

    if (!response.ok) {
      throw new Error(`API failed: ${response.status}`);
    }

    const data = await response.json();

    if (data.status === 'Exception: InvalidGrantException:  (Code: InvalidGrantException)' ||
        data.status.includes('InvalidGrantException')) {
      throw new Error('INVALID_TOKEN');
    }

    if (data.status !== 'SUCCESS' || !data.embedUrl) {
      throw new Error('Invalid API response');
    }

    return data;
  };

  const getEmbedUrl = async (token: string): Promise<string> => {
    console.log('Generating new embed URL...');

    const apiData = await callEmbeddingAPI(token);

    if (apiData.status === 'SUCCESS' && apiData.embedUrl) {
      console.log('New embed URL generated successfully');
      return apiData.embedUrl;
    }

    throw new Error('Failed to generate embed URL');
  };

  // When popup opens and welcome is dismissed, start the embedding process
  useEffect(() => {
    if (!isOpen || showWelcome || isEmbedded) return;

    const embedChat = async () => {
      try {
        setLoading(true);
        setError('');
        console.log('Starting QuickSuite chat...');

        let currentToken = idToken;
        let embedUrl;

        try {
          embedUrl = await getEmbedUrl(currentToken);
        } catch (error) {
          if (error instanceof Error && error.message === 'INVALID_TOKEN') {
            console.log('Token expired, showing refresh button...');
            setTokenExpired(true);
            setLoading(false);
            return;
          } else {
            throw error;
          }
        }

        console.log('Got embed URL, creating QuickSight context...');

        let attempts = 0;
        while (!containerRef.current && attempts < 20) {
          await new Promise(resolve => setTimeout(resolve, 100));
          attempts++;
        }

        if (!containerRef.current) {
          throw new Error('Container not ready');
        }

        const embeddingContext = await createEmbeddingContext({
          onChange: (changeEvent, metadata) => {
            console.log('Embedding context change:', changeEvent, metadata);
          },
        });

        // Note: QuickSight visualizations may fail to render due to nested iframe restrictions
        // when QuickChat tries to embed visualizations inside the chat iframe
        await embeddingContext.embedQuickChat(
          {
            url: embedUrl,
            container: containerRef.current,
            height: "100%",
            width: "100%",
            className: "quicksight-embedding-iframe",
            withIframePlaceholder: true,
            framePermissions: {
              clipboardRead: true,
              clipboardWrite: true,
            },
            onChange: (changeEvent: any, metadata: any) => {
              console.log('Frame event:', changeEvent.eventName, metadata);
              switch (changeEvent.eventName) {
                case 'FRAME_MOUNTED': {
                  console.log("QuickChat frame mounted");
                  break;
                }
                case 'FRAME_LOADED': {
                  console.log("QuickChat frame loaded");
                  setLoading(false);
                  break;
                }
                case 'ERROR_OCCURRED': {
                  console.error("Frame error:", changeEvent);
                  setError('Frame loading error');
                  setLoading(false);
                  break;
                }
              }
            }
          },
          {
            locale: "en-US",
            agentOptions: {
              fixedAgentId: (() => {
                const agentId = process.env.NEXT_PUBLIC_QUICKSUITE_AGENT_ID;
                if (!agentId || agentId === 'REPLACE_WITH_YOUR_AGENT_ID') {
                  throw new Error('NEXT_PUBLIC_QUICKSUITE_AGENT_ID is not configured. Please update your .env.local file with your QuickSuite agent ID. See README.md for instructions.');
                }
                return agentId;
              })(),
            },
            promptOptions: {
              allowFileAttachments: true,
              showAgentKnowledgeBoundary: true,
              showWebSearch: true,
            },
            footerOptions: {
              showBrandAttribution: false,
              showUsagePolicy: false,
            },
            onMessage: async (messageEvent: any, experienceMetadata: any) => {
              console.log('Content event:', messageEvent.eventName, experienceMetadata);
              switch (messageEvent.eventName) {
                case 'CONTENT_LOADED': {
                  console.log("QuickChat content loaded");
                  setLoading(false);
                  break;
                }
                case 'ERROR_OCCURRED': {
                  console.error("Content error:", messageEvent);
                  setError('Chat initialization error');
                  setLoading(false);
                  break;
                }
              }
              if (messageEvent.eventName === 'MESSAGE_RECEIVED' && !isOpen) {
                setHasNotification(true);
              }
            }
          }
        );

        console.log('QuickChat embedded successfully');
        setIsEmbedded(true);

      } catch (err) {
        console.error('Embedding error:', err);
        if (err instanceof Error && err.message.includes('expired')) {
          console.log('Session expired, clearing cache and refreshing...');
          sessionStorage.clear();
          window.location.reload();
        } else {
          setError(err instanceof Error ? err.message : 'Failed to load chat');
          setLoading(false);
        }
      }
    };

    embedChat();
  }, [isOpen, showWelcome, idToken, apiEndpoint]);

  return (
    <>
      <div className="fixed bottom-6 right-6 z-50">
        {hasNotification && !isOpen && (
          <div className="absolute -top-2 -right-2 w-6 h-6 bg-red-500 text-white text-xs rounded-full flex items-center justify-center animate-bounce z-10">
            1
          </div>
        )}

        <button
          onClick={toggleChat}
          className={`w-16 h-16 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-700 hover:to-blue-700 text-white rounded-full shadow-2xl transition-all duration-500 ease-out flex items-center justify-center transform hover:scale-110 active:scale-95 ${
            isOpen ? 'rotate-45' : 'hover:rotate-12 animate-pulse'
          }`}
          style={{
            boxShadow: isOpen
              ? '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)'
              : '0 25px 50px -12px rgba(0, 0, 0, 0.25), 0 0 0 1px rgba(255, 255, 255, 0.1)'
          }}
          aria-label="Toggle chat"
        >
          <div className={`transition-all duration-300 ${isOpen ? 'rotate-45' : ''}`}>
            {isOpen ? (
              <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            ) : (
              <div className="relative">
                <svg className="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
              </div>
            )}
          </div>
        </button>
      </div>

      {isOpen && (
        <div
          className={`fixed bottom-24 right-6 bg-white rounded-2xl shadow-2xl border border-gray-100 z-40 flex flex-col transform transition-all duration-500 ease-out backdrop-blur-sm ${
            isMinimized
              ? 'w-80 h-16 scale-95 opacity-90'
              : 'w-80 max-w-[calc(100vw-3rem)] h-[600px] max-h-[calc(100vh-8rem)] md:w-[400px] lg:w-[450px] scale-100 opacity-100'
          }`}
          style={{
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.25), 0 0 0 1px rgba(255, 255, 255, 0.05)',
            animation: isOpen ? 'slideInUp 0.5s ease-out' : 'slideOutDown 0.3s ease-in'
          }}
        >
          <style jsx>{`
            @keyframes slideInUp {
              from {
                transform: translateY(100%) scale(0.8);
                opacity: 0;
              }
              to {
                transform: translateY(0) scale(1);
                opacity: 1;
              }
            }
            @keyframes slideOutDown {
              from {
                transform: translateY(0) scale(1);
                opacity: 1;
              }
              to {
                transform: translateY(100%) scale(0.8);
                opacity: 0;
              }
            }
          `}</style>

          <div
            className={`flex items-center justify-between p-4 bg-gradient-to-r from-purple-600 to-blue-600 text-white transition-all duration-300 ${
              isMinimized ? 'rounded-2xl cursor-pointer hover:from-purple-700 hover:to-blue-700' : 'border-b border-purple-500/20 rounded-t-2xl'
            }`}
            onClick={isMinimized ? restoreChat : undefined}
          >
            <div className="flex items-center space-x-3">
              <div className="w-8 h-8 bg-white bg-opacity-20 rounded-full flex items-center justify-center p-1 backdrop-blur-sm">
                <img src="/quicksuite.png" alt="Amazon QuickSuite" className="w-6 h-6" />
              </div>
              <div>
                <h3 className="font-semibold text-sm">Quick Suite Embedded Chat Demo</h3>
                {!isMinimized && <p className="text-xs text-purple-100 opacity-90">Embedded chat interface</p>}
              </div>
            </div>
            <div className="flex items-center space-x-2">
              <button
                onClick={toggleChat}
                className="text-white hover:text-gray-200 transition-all duration-200 p-2 rounded-full hover:bg-white hover:bg-opacity-20 active:scale-95"
                aria-label="Minimize chat"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
                </svg>
              </button>
            </div>
          </div>

          <div className={`flex-1 overflow-hidden rounded-b-2xl ${isMinimized ? 'hidden' : ''}`}>
            {showWelcome ? (
                <div className="h-full flex flex-col items-center justify-center p-6 bg-gradient-to-br from-purple-50 via-blue-50 to-indigo-50 relative overflow-hidden">
                  <div className="absolute inset-0 bg-gradient-to-br from-purple-100/30 to-blue-100/30 animate-pulse"></div>
                  <div className="relative z-10 text-center mb-6">
                    <div className="w-16 h-16 bg-gradient-to-br from-purple-100 to-blue-100 rounded-2xl flex items-center justify-center mb-4 mx-auto p-3 shadow-lg transform hover:scale-105 transition-transform duration-300">
                      <img src="/quicksuite.png" alt="Amazon QuickSuite" className="w-10 h-10" />
                    </div>
                    <h2 className="text-xl font-semibold text-gray-800 mb-2">Welcome to the Embedded Chat Demo!</h2>
                    <p className="text-gray-600 text-sm leading-relaxed max-w-sm">
                      This demonstrates how embedded chat can be integrated into your applications. Try asking questions to see how it works:
                    </p>
                  </div>

                  <div className="space-y-3 mb-6 w-full max-w-sm relative z-10">
                    <div className="flex items-center space-x-3 text-sm text-gray-700 transform hover:translate-x-1 transition-transform duration-200">
                      <div className="w-6 h-6 bg-purple-100 rounded-full flex items-center justify-center shadow-sm">
                        <span className="text-purple-600">💬</span>
                      </div>
                      <span>Ask natural language questions</span>
                    </div>
                    <div className="flex items-center space-x-3 text-sm text-gray-700 transform hover:translate-x-1 transition-transform duration-200">
                      <div className="w-6 h-6 bg-blue-100 rounded-full flex items-center justify-center shadow-sm">
                        <span className="text-blue-600">🔍</span>
                      </div>
                      <span>Explore embedded chat features</span>
                    </div>
                    <div className="flex items-center space-x-3 text-sm text-gray-700 transform hover:translate-x-1 transition-transform duration-200">
                      <div className="w-6 h-6 bg-purple-100 rounded-full flex items-center justify-center shadow-sm">
                        <span className="text-purple-600">⚡</span>
                      </div>
                      <span>Experience conversational interface</span>
                    </div>
                  </div>

                  <button
                    onClick={startChat}
                    className="bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-700 hover:to-blue-700 text-white px-8 py-3 rounded-full font-medium transition-all duration-300 transform hover:scale-105 active:scale-95 shadow-lg hover:shadow-xl relative z-10"
                  >
                    <span className="flex items-center space-x-2">
                      <span>Start Demo Chat</span>
                      <span className="text-lg">🚀</span>
                    </span>
                  </button>
                </div>
              ) : (
                <div ref={containerRef} className="w-full h-full relative">
                  {loading && (
                    <div className="flex items-center justify-center h-full bg-gradient-to-br from-gray-50 to-gray-100">
                      <div className="text-center">
                        <div className="w-12 h-12 border-4 border-purple-200 border-t-purple-600 rounded-full animate-spin mx-auto mb-4"></div>
                        <div className="text-sm text-gray-600 animate-pulse">Initializing Amazon Quick Suite...</div>
                      </div>
                    </div>
                  )}
                  {error && (
                    <div className="flex items-center justify-center h-full p-4 bg-gradient-to-br from-red-50 to-pink-50">
                      <div className="text-center">
                        <div className="w-12 h-12 bg-red-100 rounded-full flex items-center justify-center mx-auto mb-4 animate-bounce">
                          <span className="text-red-600">⚠️</span>
                        </div>
                        <div className="text-red-600 mb-2 font-medium">Chat unavailable</div>
                        <div className="text-sm text-gray-500">{error}</div>
                        <button
                          onClick={() => {
                            setShowWelcome(true);
                            setError('');
                            setIsEmbedded(false);
                          }}
                          className="mt-4 text-purple-600 hover:text-purple-700 text-sm underline transition-colors duration-200"
                        >
                          Try again
                        </button>
                      </div>
                    </div>
                  )}
                  {tokenExpired && (
                    <div className="flex items-center justify-center h-full p-4 bg-gradient-to-br from-yellow-50 to-orange-50">
                      <div className="text-center">
                        <div className="w-12 h-12 bg-yellow-100 rounded-full flex items-center justify-center mx-auto mb-4 animate-pulse">
                          <span className="text-yellow-600">⚠️</span>
                        </div>
                        <div className="text-yellow-600 mb-2 font-medium">Session Expired</div>
                        <div className="text-sm text-gray-500 mb-4">Please refresh your session to continue</div>
                        <button
                          onClick={() => {
                            sessionStorage.clear();
                            window.location.href = `${cognitoConfig.domain}/logout?client_id=${cognitoConfig.clientId}&logout_uri=${encodeURIComponent(cognitoConfig.redirectUri)}`;
                          }}
                          className="bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-700 hover:to-blue-700 text-white px-5 py-1.5 rounded-full font-medium transition-all duration-300 transform hover:scale-105 active:scale-95 shadow-lg"
                        >
                          Refresh Session
                        </button>
                      </div>
                    </div>
                  )}
                  {!loading && !error && !isEmbedded && !tokenExpired && (
                    <div className="flex items-center justify-center h-full p-4 bg-gradient-to-br from-gray-50 to-gray-100">
                      <div className="text-center text-gray-500">
                        <div className="animate-pulse">Waiting for embedding...</div>
                        <div className="text-xs mt-2 opacity-60">isEmbedded: {isEmbedded.toString()}</div>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
        </div>
      )}

      {isOpen && (
        <div
          className="fixed inset-0 bg-black bg-opacity-20 z-30 md:hidden transition-opacity duration-300"
          onClick={toggleChat}
        />
      )}
    </>
  );
}
