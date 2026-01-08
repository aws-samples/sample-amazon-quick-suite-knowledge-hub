// Cognito OAuth Configuration
const cognitoConfig = {
  userPoolId: process.env.NEXT_PUBLIC_COGNITO_USER_POOL_ID!,
  clientId: process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID!,
  domain: process.env.NEXT_PUBLIC_COGNITO_DOMAIN!,
  redirectUri: process.env.NEXT_PUBLIC_REDIRECT_URL || 'http://localhost:3000',
  responseType: 'token',
  scope: 'openid email profile'
};

export default cognitoConfig;
