import { createBrowserRouter, RouterProvider } from "react-router-dom"

import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ThemeProvider } from "@/components/app-shell/theme-context"
import { AppShell } from "@/components/app-shell/AppShell"
import { RequireAuth } from "@/features/auth/RequireAuth"
import { RootPage } from "@/features/auth/RootPage"
import { OnboardingPage } from "@/features/auth/OnboardingPage"
import { AdminHomePage } from "@/features/admin/AdminHomePage"
import { UserHomePage } from "@/features/home/UserHomePage"
import { GraphPage } from "@/features/graph/GraphPage"

const router = createBrowserRouter([
  { path: "/", element: <RootPage /> },
  {
    element: <RequireAuth />,
    children: [
      { path: "/onboarding", element: <OnboardingPage /> },
      {
        element: <AppShell />,
        children: [
          { path: "/admin", element: <AdminHomePage /> },
          { path: "/home", element: <UserHomePage /> },
          { path: "/graph", element: <GraphPage /> },
        ],
      },
    ],
  },
])

function App() {
  return (
    <ThemeProvider>
      <TooltipProvider>
        <RouterProvider router={router} />
        <Toaster richColors position="bottom-right" />
      </TooltipProvider>
    </ThemeProvider>
  )
}

export default App
