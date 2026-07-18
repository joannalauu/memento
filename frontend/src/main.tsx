import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ApiProvider } from '@/lib/api'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ApiProvider
      // The landing page ("/") renders its own signed-out UI, so an expected 401
      // there must not bounce. Everywhere else, a lost session hard-navigates to
      // the landing page rather than auto-redirecting through Auth0.
      onUnauthorized={() => {
        if (window.location.pathname !== '/') window.location.assign('/')
      }}
    >
      <App />
    </ApiProvider>
  </StrictMode>,
)
