import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router";
import { api } from "./api";
import { LiveProvider, useLive, useLiveData } from "./live";
import { Icon, Logo, Spinner } from "./ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Catalog from "./pages/Catalog";
import NewRequest from "./pages/NewRequest";
import RequestPage from "./pages/RequestPage";
import AllRequests from "./pages/AllRequests";
import ConfigsPage from "./pages/ConfigsPage";
import StatePage from "./pages/StatePage";
import "./styles.css";

function useTheme() {
  const [theme, setTheme] = useState<string>(() => { try { return localStorage.getItem("theme") ?? ""; } catch { return ""; } });
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme; else delete document.documentElement.dataset.theme;
    try { theme ? localStorage.setItem("theme", theme) : localStorage.removeItem("theme"); } catch { /* private mode */ }
  }, [theme]);
  const dark = theme ? theme === "dark" : window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  return { dark, toggle: () => setTheme(dark ? "light" : "dark") };
}

function Shell() {
  const { session, setSession, connected, toasts, dismiss } = useLive();
  const { dark, toggle } = useTheme();
  const nav = useNavigate();
  const loc = useLocation();
  const [menu, setMenu] = useState(false);
  useEffect(() => setMenu(false), [loc.pathname]);

  const canApprove = session?.authenticated && session.can_approve;
  const pending = useLiveData(() => (canApprove ? api.requests("pending") : Promise.resolve([])), () => true, [canApprove]);

  if (!session) return <div className="boot"><Spinner size={22} /></div>;
  if (!session.authenticated) return <Login />;

  const signOut = async () => { setSession(await api.signOut()); nav("/"); };
  const nPending = pending.data?.length ?? 0;

  return (
    <div className="app">
      <aside className={`side ${menu ? "open" : ""}`}>
        <Link to="/" className="brand"><Logo size={36} />
          <span><strong>Fabric Factory</strong><small>network self-service</small></span></Link>
        <nav>
          <NavLink to="/" end><Icon name="home" />Overview</NavLink>
          <NavLink to="/new" className={() => (/^\/(new|tools)/.test(loc.pathname) ? "active" : "")}><Icon name="plus" />New request</NavLink>
          {session.can_approve && <NavLink to="/approvals"><Icon name="inbox" />Approvals{nPending > 0 && <span className="count">{nPending}</span>}</NavLink>}
          {session.is_operator && <NavLink to="/requests" end><Icon name="list" />All requests</NavLink>}
        </nav>
        <div className="side-foot">
          <a href="/api/docs" target="_blank" rel="noreferrer"><Icon name="book" />API docs</a>
          <a href="/classic/"><Icon name="ext" />Classic pages</a>
          {session.is_staff && <a href="/admin/"><Icon name="server" />Django admin</a>}
        </div>
      </aside>
      {menu && <div className="scrim" onClick={() => setMenu(false)} />}
      <div className="main">
        <header className="top">
          <button className="icon-btn burger" onClick={() => setMenu(!menu)} aria-label="Menu"><Icon name="list" size={18} /></button>
          <div className={`live ${connected ? "on" : ""}`} title={connected ? "Live updates connected" : "Reconnecting…"}>
            <span className="dot" />{connected ? "Live" : "Reconnecting"}
          </div>
          <div className="grow" />
          <button className="icon-btn" onClick={toggle} aria-label="Toggle theme"><Icon name={dark ? "sun" : "moon"} /></button>
          <div className="who"><span className="avatar">{session.user.name.slice(0, 1).toUpperCase()}</span>
            <span className="who-t"><strong>{session.user.name}</strong><small>{session.can_approve ? "operator" : "requester"}</small></span></div>
          <button className="icon-btn" onClick={signOut} aria-label="Sign out" title="Sign out"><Icon name="logout" /></button>
        </header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/new" element={<Catalog />} />
            <Route path="/new/:slug" element={<NewRequest />} />
            <Route path="/tools/configs" element={<ConfigsPage />} />
            <Route path="/tools/state" element={<StatePage />} />
            <Route path="/requests" element={session.is_operator ? <AllRequests /> : <Navigate to="/" />} />
            <Route path="/approvals" element={session.can_approve ? <AllRequests pendingOnly /> : <Navigate to="/" />} />
            <Route path="/requests/:id" element={<RequestPage />} />
            <Route path="*" element={<Navigate to="/" />} />
          </Routes>
        </main>
      </div>
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast t-${t.tone}`} onClick={() => { dismiss(t.id); if (t.href) nav(t.href); }}>
            <Icon name={t.tone === "ok" ? "check" : t.tone === "bad" ? "alert" : "clock"} />
            <div><strong>{t.title}</strong>{t.body && <div className="mute">{t.body}</div>}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <LiveProvider><Shell /></LiveProvider>
    </BrowserRouter>
  </StrictMode>,
);
