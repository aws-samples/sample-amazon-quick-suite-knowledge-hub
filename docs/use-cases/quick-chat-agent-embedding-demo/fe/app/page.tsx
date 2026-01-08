'use client';

import { useEffect, useState } from 'react';
import ChatPopup from '../components/ChatPopup';
import cognitoConfig from '../lib/amplify-config';

export default function Home() {
  const [user, setUser] = useState<any>(null);
  const [idToken, setIdToken] = useState<string>('');
  const [loading, setLoading] = useState(true);

  const apiEndpoint = process.env.NEXT_PUBLIC_API_ENDPOINT || '';

  useEffect(() => {
    checkAuthState();
  }, []);

  const checkAuthState = async () => {
    try {
      const cachedToken = sessionStorage.getItem('quickchat_token');

      if (cachedToken) {
        console.log('Using cached token');
        setIdToken(cachedToken);

        const payload = JSON.parse(atob(cachedToken.split('.')[1]));
        setUser({
          email: payload.email,
          name: payload.name || payload.given_name || payload.family_name || payload['cognito:username'] || payload.email
        });
        return;
      }

      const hash = window.location.hash.substring(1);
      const hashParams = new URLSearchParams(hash);
      const idToken = hashParams.get('id_token');

      if (idToken) {
        console.log('ID token received from hash');

        // Clean up URL immediately to remove tokens
        window.history.replaceState({}, document.title, window.location.pathname);

        sessionStorage.setItem('quickchat_token', idToken);
        setIdToken(idToken);

        const payload = JSON.parse(atob(idToken.split('.')[1]));
        setUser({
          email: payload.email,
          name: payload.name || payload.given_name || payload.family_name || payload['cognito:username'] || payload.email
        });
      }
    } catch (error) {
      console.error('Auth check error:', error);
      sessionStorage.clear();
    } finally {
      setLoading(false);
    }
  };

  const handleTokenRefresh = (newToken: string) => {
    console.log('Updating token in parent component');
    setIdToken(newToken);
  };

  const handleSignIn = () => {
    const authUrl = `${cognitoConfig.domain}/oauth2/authorize?` +
      `response_type=${cognitoConfig.responseType}&` +
      `client_id=${cognitoConfig.clientId}&` +
      `redirect_uri=${encodeURIComponent(cognitoConfig.redirectUri)}&` +
      `scope=${encodeURIComponent(cognitoConfig.scope)}`;

    window.location.href = authUrl;
  };

  const handleSignOut = () => {
    setUser(null);
    setIdToken('');
    sessionStorage.clear();
    window.location.href = `${cognitoConfig.domain}/logout?client_id=${cognitoConfig.clientId}&logout_uri=${encodeURIComponent(cognitoConfig.redirectUri)}`;
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-aws-orange border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
          <div className="text-aws-dark-blue">Loading...</div>
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen bg-white">
        <header className="bg-aws-dark-blue text-white">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="flex items-center justify-between h-16">
              <div className="flex items-center space-x-8">
                <div className="flex items-center space-x-2">
                  <img src="/quicksuite.png" alt="Amazon QuickSuite" className="w-8 h-8" />
                  <span className="text-xl font-semibold">Quick Suite Chat Agent Embedding Demo</span>
                </div>
              </div>
              <div className="flex items-center space-x-4">
                <button
                  onClick={handleSignIn}
                  className="bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-700 hover:to-blue-700 text-white px-4 py-2 rounded font-medium transition-colors"
                >
                  Sign In
                </button>
              </div>
            </div>
          </div>
        </header>

        <section className="bg-white py-20">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center">
              <h1 className="text-5xl font-bold text-aws-dark-blue mb-6">
                Quick Suite Chat Agent Embedding Demo
              </h1>
              <p className="text-xl text-gray-600 mb-8 max-w-3xl mx-auto leading-relaxed">
                Experience Quick Suite's chat agent embedded directly into your application. This demo showcases how to integrate conversational capabilities that provide contextual responses within your existing workflow.
              </p>
            </div>
          </div>
        </section>

        <section className="bg-aws-gray py-16">
          <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
            <h2 className="text-3xl font-bold text-aws-dark-blue mb-4">Ready to try the demo?</h2>
            <p className="text-lg text-gray-600 mb-8">
              Sign in with your Cognito credentials to experience Quick Suite's embedded chat capabilities.
            </p>
            <button
              onClick={handleSignIn}
              className="bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-700 hover:to-blue-700 text-white px-8 py-3 rounded font-semibold text-lg transition-colors"
            >
              Start Demo
            </button>
          </div>
        </section>

        <section className="bg-white py-16">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <h2 className="text-3xl font-bold text-aws-dark-blue text-center mb-12">
              Key Embedding Features
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
              <div className="bg-white border border-gray-200 rounded-lg p-6 hover:shadow-lg transition-shadow">
                <div className="w-12 h-12 bg-gradient-to-r from-purple-600 to-blue-600 rounded-lg flex items-center justify-center mb-4">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
                  </svg>
                </div>
                <h3 className="text-xl font-semibold text-aws-dark-blue mb-3">Unified Chat Experience</h3>
                <p className="text-gray-600">Seamlessly integrate structured and unstructured data conversations into your applications.</p>
              </div>

              <div className="bg-white border border-gray-200 rounded-lg p-6 hover:shadow-lg transition-shadow">
                <div className="w-12 h-12 bg-gradient-to-r from-purple-600 to-blue-600 rounded-lg flex items-center justify-center mb-4">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                  </svg>
                </div>
                <h3 className="text-xl font-semibold text-aws-dark-blue mb-3">One-Click Embedding</h3>
                <p className="text-gray-600">Embed Quick Suite chat agents into your applications.</p>
              </div>

              <div className="bg-white border border-gray-200 rounded-lg p-6 hover:shadow-lg transition-shadow">
                <div className="w-12 h-12 bg-gradient-to-r from-purple-600 to-blue-600 rounded-lg flex items-center justify-center mb-4">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                  </svg>
                </div>
                <h3 className="text-xl font-semibold text-aws-dark-blue mb-3">Enterprise Security</h3>
                <p className="text-gray-600">Designed with access controls and data governance for enterprise applications.</p>
              </div>

              <div className="bg-white border border-gray-200 rounded-lg p-6 hover:shadow-lg transition-shadow">
                <div className="w-12 h-12 bg-gradient-to-r from-purple-600 to-blue-600 rounded-lg flex items-center justify-center mb-4">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zM21 5a2 2 0 00-2-2h-4a2 2 0 00-2 2v12a4 4 0 004 4h4a2 2 0 002-2V5z" />
                  </svg>
                </div>
                <h3 className="text-xl font-semibold text-aws-dark-blue mb-3">Custom Branding</h3>
                <p className="text-gray-600">Customize visual theming and conversational tone to match your company's brand identity.</p>
              </div>

              <div className="bg-white border border-gray-200 rounded-lg p-6 hover:shadow-lg transition-shadow">
                <div className="w-12 h-12 bg-gradient-to-r from-purple-600 to-blue-600 rounded-lg flex items-center justify-center mb-4">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                  </svg>
                </div>
                <h3 className="text-xl font-semibold text-aws-dark-blue mb-3">Application Connectivity</h3>
                <p className="text-gray-600">Connect to 40+ data sources and integrate with tools like Slack, Jira, and SharePoint.</p>
              </div>

              <div className="bg-white border border-gray-200 rounded-lg p-6 hover:shadow-lg transition-shadow">
                <div className="w-12 h-12 bg-gradient-to-r from-purple-600 to-blue-600 rounded-lg flex items-center justify-center mb-4">
                  <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                  </svg>
                </div>
                <h3 className="text-xl font-semibold text-aws-dark-blue mb-3">Registered User Auth</h3>
                <p className="text-gray-600">Seamless integration with your existing enterprise authentication infrastructure.</p>
              </div>
            </div>
          </div>
        </section>

        <section className="bg-white py-16">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="text-center mb-12">
              <h2 className="text-3xl font-bold text-aws-dark-blue mb-4">Learn More</h2>
              <p className="text-lg text-gray-600 max-w-2xl mx-auto mb-8">
                For more detailed information about the Quick Suite SDK and experience-specific options, explore these resources.
              </p>

              <div className="flex flex-col sm:flex-row gap-6 justify-center">
                <a
                  href="https://aws.amazon.com/blogs/business-intelligence/announcing-embedded-chat-in-amazon-quick-suite/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-700 hover:to-blue-700 text-white px-6 py-3 rounded font-medium transition-colors inline-flex items-center"
                >
                  Read the Blog Post
                  <svg className="w-4 h-4 ml-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                  </svg>
                </a>

                <a
                  href="https://github.com/awslabs/amazon-quicksight-embedding-sdk"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="border border-purple-600 text-purple-600 hover:bg-purple-600 hover:text-white px-6 py-3 rounded font-medium transition-colors inline-flex items-center"
                >
                  GitHub SDK
                  <svg className="w-4 h-4 ml-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                  </svg>
                </a>

                <a
                  href="https://community.amazonquicksight.com/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="border border-purple-600 text-purple-600 hover:bg-purple-600 hover:text-white px-6 py-3 rounded font-medium transition-colors inline-flex items-center"
                >
                  Join Community
                  <svg className="w-4 h-4 ml-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                  </svg>
                </a>
              </div>
            </div>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-white">
      <header className="bg-aws-dark-blue text-white">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center space-x-8">
              <div className="flex items-center space-x-2">
                <img src="/quicksuite.png" alt="Amazon QuickSuite" className="w-8 h-8" />
                <span className="text-xl font-semibold">Quick Suite Chat Agent Embedding Demo</span>
              </div>
            </div>
            <div className="flex items-center space-x-4">
              <span className="text-gray-300">Welcome, {user.name || user.email}</span>
              <button
                onClick={handleSignOut}
                className="bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded font-medium transition-colors"
              >
                Sign Out
              </button>
            </div>
          </div>
        </div>
      </header>

      {idToken && apiEndpoint && (
        <ChatPopup
          idToken={idToken}
          apiEndpoint={apiEndpoint}
          onTokenRefresh={handleTokenRefresh}
        />
      )}

      <main className="py-16">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-12">
            <h1 className="text-4xl font-bold text-aws-dark-blue mb-4">Quick Suite Embedded Chat Demo</h1>
            <p className="text-xl text-gray-600 max-w-3xl mx-auto">
              Experience the embedded chat interface. Click the chat button in the bottom right corner to start a conversation with the chat assistant.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 max-w-4xl mx-auto">
            <div className="bg-white border border-gray-200 rounded-lg p-8 hover:shadow-lg transition-shadow">
              <div className="w-12 h-12 bg-gradient-to-r from-purple-600 to-blue-600 rounded-lg flex items-center justify-center mb-4">
                <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-aws-dark-blue mb-3">Interactive Demo</h3>
              <p className="text-gray-600">Experience embedded chat capabilities through natural language conversations and real-time interactions.</p>
            </div>

            <div className="bg-white border border-gray-200 rounded-lg p-8 hover:shadow-lg transition-shadow">
              <div className="w-12 h-12 bg-gradient-to-r from-purple-600 to-blue-600 rounded-lg flex items-center justify-center mb-4">
                <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </div>
              <h3 className="text-xl font-semibold text-aws-dark-blue mb-3">Embedding Integration</h3>
              <p className="text-gray-600">See how QuickSuite chat seamlessly integrates into web applications using the embedding SDK.</p>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
