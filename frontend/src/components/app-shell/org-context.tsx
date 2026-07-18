/**
 * Active-org context. Holds the currently selected org (persisted to
 * localStorage so it survives navigation and reloads) and derives the current
 * user's role within it. Mounted by the app shell once orgs + user are loaded,
 * so consumers can rely on a non-null active org.
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react"

import type { ObjectId, Org, OrgRole, User } from "@/lib/api"

const STORAGE_KEY = "memento.activeOrgId"

interface OrgContextValue {
  org: Org
  orgId: ObjectId
  /** The signed-in user's role in the active org (undefined if somehow absent). */
  role: OrgRole | undefined
  orgs: Org[]
  setOrgId: (orgId: ObjectId) => void
}

const OrgContext = createContext<OrgContextValue | null>(null)

interface OrgProviderProps {
  orgs: Org[]
  me: User
  children: ReactNode
}

export function OrgProvider({ orgs, me, children }: OrgProviderProps) {
  const [activeOrgId, setActiveOrgId] = useState<ObjectId | null>(() =>
    localStorage.getItem(STORAGE_KEY),
  )

  // Resolve the active org, falling back to the first when the stored id is
  // stale (org deleted, membership lost) or unset.
  const org = orgs.find((o) => o.id === activeOrgId) ?? orgs[0]

  useEffect(() => {
    if (org && org.id !== activeOrgId) {
      setActiveOrgId(org.id)
      localStorage.setItem(STORAGE_KEY, org.id)
    }
  }, [org, activeOrgId])

  const value = useMemo<OrgContextValue>(() => {
    const setOrgId = (id: ObjectId) => {
      setActiveOrgId(id)
      localStorage.setItem(STORAGE_KEY, id)
    }
    const role = org.members.find((m) => m.userId === me.id)?.role
    return { org, orgId: org.id, role, orgs, setOrgId }
  }, [org, orgs, me.id])

  return <OrgContext value={value}>{children}</OrgContext>
}

export function useActiveOrg(): OrgContextValue {
  const ctx = useContext(OrgContext)
  if (!ctx) throw new Error("useActiveOrg must be used within an OrgProvider")
  return ctx
}
