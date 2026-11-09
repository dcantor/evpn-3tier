import { useMemo, useState } from "react";
import type { Device } from "../api";
import { Icon } from "../ui";

const ORDER = ["spine", "leaf", "border", "fw", "wan", "lb", "web", "app", "db", "ipam"];

/** The lab's devices grouped by role, with a filter; optional checkboxes for a multi-device action. */
export default function DevicePicker({ devices, selected, onSelect, checked, onCheck }: {
  devices: Device[]; selected: string | null; onSelect: (n: string) => void; checked?: Set<string>; onCheck?: (s: Set<string>) => void;
}) {
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const f = devices.filter((d) => !q || `${d.name} ${d.role_label} ${d.mgmt_ip}`.toLowerCase().includes(q.toLowerCase()));
    return ORDER.map((r) => [r, f.filter((d) => d.role === r)] as const).filter(([, ds]) => ds.length);
  }, [devices, q]);
  const toggle = (n: string) => { if (!checked || !onCheck) return; const s = new Set(checked); s.has(n) ? s.delete(n) : s.add(n); onCheck(s); };
  return (
    <div className="picker">
      <label className="search wide"><Icon name="search" /><input placeholder="Filter devices" value={q} onChange={(e) => setQ(e.target.value)} /></label>
      {groups.map(([role, ds]) => (
        <div key={role} className="pk-group">
          <div className="pk-h">{ds[0].role_label}{ds.length > 1 ? "s" : ""}<span className="tag">{ds[0].kind === "vyos" ? "VyOS" : "Alpine"}</span></div>
          {ds.map((d) => (
            <div key={d.name} className={`pk-row ${selected === d.name ? "on" : ""}`}>
              {checked && <input type="checkbox" aria-label={`select ${d.name}`} checked={checked.has(d.name)} onChange={() => toggle(d.name)} />}
              <button onClick={() => onSelect(d.name)}><strong>{d.name}</strong><span className="mono small mute">{d.mgmt_ip}</span></button>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
