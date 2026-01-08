/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://*.quicksight.aws.amazon.com https://*.amazonaws.com",
              "style-src 'self' 'unsafe-inline' https://*.quicksight.aws.amazon.com",
              "img-src 'self' data: blob: https://*.quicksight.aws.amazon.com https://*.amazonaws.com",
              "font-src 'self' data: https://*.quicksight.aws.amazon.com",
              "connect-src 'self' https://*.quicksight.aws.amazon.com https://*.amazonaws.com wss://*.quicksight.aws.amazon.com https://*.execute-api.us-east-1.amazonaws.com",
              "frame-src 'self' https://*.quicksight.aws.amazon.com",
              "frame-ancestors 'self' https://*.quicksight.aws.amazon.com",
              "worker-src 'self' blob:",
              "child-src 'self' blob: https://*.quicksight.aws.amazon.com",
              "object-src 'none'",
              "base-uri 'self'"
            ].join('; ')
          },
          {
            key: 'X-Frame-Options',
            value: 'SAMEORIGIN'
          }
        ]
      }
    ];
  }
};

module.exports = nextConfig;
