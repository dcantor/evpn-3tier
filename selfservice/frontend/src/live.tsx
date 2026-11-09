// Live updates: one EventSource per tab on /api/stream. The server says which request changed; components that show
// it subscribe and fetch again. Also: the session, and toasts.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, setCsrf, type Session, type Status } from "./api";

export type Change = { id: number; status: Status; seq: number; percent: number; updated: string };
type Listener = (c: Change) => void;
type Toast = { id: number; tone: "ok" | "bad" | "info"; title: string; body?: string; href?: string };

type Ctx = {
  session: Session | null; setSession: (s: Session) => void; reloadSession: () => Promise<void>;
  subscribe: (fn: Listener) => () => void; connected: boolean;
  toast: (t: Omit<Toast, "id">) => void; toasts: Toast[]; dismiss: (id: number) => void;
};
const LiveCtx = createContext<Ctx>(null!);
export const useLive = () => useContext(LiveCtx);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [session, setSessionState] = useState<Session | null>(null);
  const [connected, setConnected] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const listeners = useRef(new Set<Listener>());
  const lastStatus = useRef(new Map<number, Status>());

  const setSession = useCallback((s: Session) => { setCsrf(s.csrf); setSessionState(s); }, []);
  const reloadSession = useCallback(async () => setSession(await api.session()), [setSession]);
  useEffect(() => { reloadSession().catch(() => setSessionState({ authenticated: false, csrf: "" })); }, [reloadSession]);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const toast = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((all) => [...all.slice(-3), { ...t, id }]);
    setTimeout(() => dismiss(id), 7000);
  }, [dismiss]);

  useEffect(() => {
    if (!session?.authenticated) return;
    const es = new EventSource("/api/stream");
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("request", (m) => {
      const c: Change = JSON.parse((m as MessageEvent).data);
      const before = lastStatus.current.get(c.id);
      lastStatus.current.set(c.id, c.status);
      if (before && before !== c.status) {
        const tone = c.status === "done" ? "ok" : c.status === "failed" ? "bad" : "info";
        toast({ tone, title: `Request #${c.id} ${c.status}`, href: `/requests/${c.id}` });
      }
      listeners.current.forEach((fn) => fn(c));
    });
    return () => { es.close(); setConnected(false); };
  }, [session?.authenticated, toast]);

  const subscribe = useCallback((fn: Listener) => { listeners.current.add(fn); return () => { listeners.current.delete(fn); }; }, []);

  return (
    <LiveCtx.Provider value={{ session, setSession, reloadSession, subscribe, connected, toast, toasts, dismiss }}>
      {children}
    </LiveCtx.Provider>
  );
}

/** Fetch with `load`, and again whenever the stream reports a change that `match` accepts (debounced). */
export function useLiveData<T>(load: () => Promise<T>, match: (c: Change) => boolean, deps: unknown[] = []) {
  const { subscribe } = useLive();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const loadRef = useRef(load); loadRef.current = load;
  const matchRef = useRef(match); matchRef.current = match;

  const refresh = useCallback(() => {
    loadRef.current().then((d) => { setData(d); setError(null); }).catch((e) => setError(String(e.message ?? e)));
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setData(null); refresh(); }, deps);
  useEffect(() => subscribe((c) => {
    if (!matchRef.current(c)) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(refresh, 250);
  }), [subscribe, refresh]);
  return { data, error, refresh, setData };
}
