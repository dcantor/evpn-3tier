// Shared building blocks: icons, status pills, progress bars, times, cards, dialogs.
import { useEffect, useId, useState, type ReactNode } from "react";
import type { Status } from "./api";

const PATHS: Record<string, string> = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z",
  plus: "M12 5v14M5 12h14",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  check: "M20 6 9 17l-5-5",
  x: "M18 6 6 18M6 6l12 12",
  retry: "M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5",
  clock: "M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  inbox: "M22 12h-6l-2 3h-4l-2-3H2M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9",
  sun: "M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  ext: "M14 3h7v7M10 14 21 3M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5",
  term: "M4 17l6-6-6-6M12 19h8",
  server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  book: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5zM4 19.5V21h16",
  alert: "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  chevron: "M9 18l6-6-6-6",
  network: "M12 3v6M5 21v-4h14v4M12 9a3 3 0 1 0 0 0zM5 17v-3h14v3M12 12v2",
  chart: "M3 3v18h18M7 15l4-4 3 3 5-6",
  layers: "M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5",
  gauge: "M12 14l4-4M3.3 19a10 10 0 1 1 17.4 0z",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
};
export function Icon({ name, size = 16, className = "" }: { name: keyof typeof PATHS | string; size?: number; className?: string }) {
  return (
    <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={PATHS[name] ?? ""} /></svg>
  );
}

/** Fabric Factory's mark: a sawtooth-roof factory whose chimney smoke is a little network fabric. */
export function Logo({ size = 34 }: { size?: number }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" role="img" aria-label="Fabric Factory" className="logo-svg">
      <defs><linearGradient id={`g${id}`} x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#6366f1" /><stop offset="1" stopColor="#a855f7" /></linearGradient></defs>
      <rect width="48" height="48" rx="12" fill={`url(#g${id})`} />
      <g className="logo-smoke">
        <path d="M33.5 15.5 38.5 10.5 43.5 6.5M38.5 10.5 43 14.5M43.5 6.5 43 14.5" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" opacity=".85" />
        <circle cx="33.5" cy="15.5" r="2.3" fill="#fff" /><circle cx="38.5" cy="10.5" r="1.9" fill="#fff" /><circle cx="43.5" cy="6.5" r="1.6" fill="#fff" /><circle cx="43" cy="14.5" r="1.4" fill="#fff" />
      </g>
      <path d="M6 40V27l8-5v5l8-5v5l8-5v-6.5h7V40z" fill="#fff" />
      <g fill="#7c5cf0"><rect x="9.5" y="31" width="4" height="4" rx="1" /><rect x="16.5" y="31" width="4" height="4" rx="1" />
        <rect x="23.5" y="31" width="4" height="4" rx="1" /><rect x="31" y="26" width="3" height="9" rx="1" /></g>
    </svg>
  );
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-label="working" />;
}

const LABEL: Record<Status, string> = { submitted: "Awaiting approval", approved: "Queued", running: "Running", done: "Done",
  failed: "Failed", rejected: "Rejected", cancelled: "Cancelled" };
export function StatusPill({ status }: { status: Status }) {
  return <span className={`pill s-${status}`}>{status === "running" && <Spinner size={10} />}{LABEL[status] ?? status}</span>;
}

export function Bar({ value, status, thin }: { value: number; status?: string; thin?: boolean }) {
  return (
    <div className={`bar ${thin ? "thin" : ""} ${status ? `b-${status}` : ""}`} role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

export function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}

export function ago(iso: string | null | undefined, now = Date.now()) {
  if (!iso) return "";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
export function dur(s: number) {
  s = Math.max(0, Math.round(s));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}
export const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
export function TimeAgo({ iso }: { iso: string | null | undefined }) {
  const now = useNow(15000);
  return <time dateTime={iso ?? undefined} title={when(iso)}>{ago(iso, now)}</time>;
}

export function Card({ title, actions, children, className = "", pad = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && <header className="card-h"><h2>{title}</h2>{actions && <div className="card-a">{actions}</div>}</header>}
      <div className={pad ? "card-b" : ""}>{children}</div>
    </section>
  );
}

export function Empty({ icon = "inbox", title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return <div className="empty"><Icon name={icon} size={28} /><strong>{title}</strong>{children && <div className="mute">{children}</div>}</div>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k); return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header><h3>{title}</h3><button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button></header>
        {children}
      </div>
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return <div className="note bad"><Icon name="alert" /><div>{children}</div></div>;
}
