import { Amplify } from 'aws-amplify'

// These will be populated from Terraform outputs
const config = {
  Auth: {
    Cognito: {
      userPoolId: import.meta.env.VITE_USER_POOL_ID || '',
      userPoolClientId: import.meta.env.VITE_USER_POOL_CLIENT_ID || '',
      region: import.meta.env.VITE_AWS_REGION || 'ap-south-1',
    }
  },
  API: {
    REST: {
      ProdigeeAPI: {
        endpoint: import.meta.env.VITE_API_ENDPOINT || '',
        region: import.meta.env.VITE_AWS_REGION || 'ap-south-1',
      }
    }
  }
}

Amplify.configure(config)

export default config
