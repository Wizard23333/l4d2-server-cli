import { FormEvent, ReactNode, RefObject, useEffect, useMemo, useRef, useState } from "react";
import OperationsPanel from './OperationsPanel';
import { confirmAction } from './confirm';

type Room = {
  id: string;
  label: string;
  service: string;
  port: number;
  active: string;
  sub_state: string;
  restarts: string;
  exit_status: string;
  started_at: string;
  default_map: string;
  default_campaign_id: string;
  port_listening: boolean;
  campaigns?: Campaign[];
};

type CampaignMap = {
  name: string;
  display_name: string;
  chapter: number;
};

type Campaign = {
  id: string;
  title: string;
  source: string;
  maps: CampaignMap[];
};

type Addon = {
  filename: string;
  state: string;
  size: number;
  modified_at: number;
  kind: string;
  maps: string[];
  missions: string[];
  source: string;
  catalog_id: string;
  title: string;
  url: string;
  install_ids: string[];
  package_status: string;
  reinstallable: boolean;
  group_id?: string;
  group_title?: string;
  group_members?: string[];
};

type Job = {
  id: string;
  type?: string;
  source?: string;
  kind?: string;
  status: string;
  stage?: string;
  message?: string;
  title?: string;
  catalog_id?: string;
  workshop_id?: string;
  export_filename?: string;
  download_url?: string;
  install_ids?: string[];
  progress?: number;
  progress_label?: string;
  downloaded_bytes?: number;
  total_bytes?: number;
  items_done?: number;
  items_total?: number;
};

type ServerPlugin = {
  protected?: boolean;
  id: string;
  label: string;
  filename?: string;
  state: string;
  size?: number;
};

type ServerPlugins = {
  affected_rooms?: string[];
  metamod?: boolean;
  sourcemod?: boolean;
  plugins?: ServerPlugin[];
};

type SystemSnapshot = {
  cpu?: { cores?: number; percent?: number | null; load_average?: Array<number | null> };
  memory?: { total?: number; used?: number; available?: number; percent?: number | null };
  swap?: { total?: number; used?: number; free?: number; percent?: number | null };
  uptime?: { display?: string };
  disk?: Array<{ id: string; label: string; path: string; total?: number | null; used?: number | null; free?: number | null; percent?: number | null }>;
  processes?: Array<{ service: string; label?: string; active?: string; sub_state?: string; memory_current?: number | null; cpu_usage_nsec?: number | null }>;
};

type StateSnapshot = {
  selected_room: string;
  generated_at: number;
  rooms: Room[];
  campaigns: Campaign[];
  maps: string[];
  addons: Addon[];
  jobs: Job[];
  server_plugins: ServerPlugins;
  system?: SystemSnapshot;
};

type CatalogItem = {
  source: string;
  id: string;
  install_ids?: string[];
  title: string;
  kind: string;
  url: string;
  size?: string;
  summary?: string;
  installable: boolean;
  reason?: string;
};

type PackageFilters = {
  text: string;
  status: string;
  source: string;
  record: string;
};

type ProxyItem = {
  ok: boolean;
  workshop_id: string;
  kind?: string;
  title?: string;
  file_url?: string;
  file_size?: number;
  suggested_filename?: string;
  message?: string;
};

type ProxyResolve = ProxyItem & {
  collection?: boolean;
  items?: ProxyItem[];
};

type WorkshopParts = {
  ok: boolean;
  workshop_id: string;
  kind?: string;
  collection?: boolean;
  title?: string;
  parts: ProxyItem[];
  message?: string;
};

type Theme = "dark" | "light";
type ToastTone = "ok" | "warn" | "danger" | "info";
type ToastItem = { id: number; message: string; tone: ToastTone };

const VIEW_IDS = ["overview", "performance", "rooms", "install", "packages", "mods", "maps", "migration", "operations"] as const;
type ViewId = (typeof VIEW_IDS)[number];

const NAV_GROUPS: Array<{ group: string; items: Array<{ id: ViewId; label: string; icon: string }> }> = [
  {
    group: "Monitor",
    items: [
      { id: "overview", label: "Overview", icon: "grid" },
      { id: "performance", label: "Performance", icon: "gauge" }
    ]
  },
  {
    group: "Operate",
    items: [
      { id: "rooms", label: "Rooms", icon: "server" },
      { id: "install", label: "Install & Jobs", icon: "download" },
      { id: "operations", label: "Operations", icon: "server" }
    ]
  },
  {
    group: "Content",
    items: [
      { id: "packages", label: "Map Packages", icon: "box" },
      { id: "mods", label: "Mods & Plugins", icon: "plug" },
      { id: "maps", label: "Installed Maps", icon: "map" },
      { id: "migration", label: "Migration", icon: "swap" }
    ]
  }
];

const VIEW_TITLES: Record<ViewId, string> = {
  overview: "Overview",
  performance: "Performance",
  rooms: "Rooms",
  install: "Install & Jobs",
  packages: "Map Packages",
  mods: "Mods & Plugins",
  maps: "Installed Maps",
  migration: "Migration",
  operations: "Operations"
};

const ICON_PATHS: Record<string, ReactNode> = {
  grid: (
    <>
      <rect x="2.2" y="2.2" width="5" height="5" rx="1.2" />
      <rect x="8.8" y="2.2" width="5" height="5" rx="1.2" />
      <rect x="2.2" y="8.8" width="5" height="5" rx="1.2" />
      <rect x="8.8" y="8.8" width="5" height="5" rx="1.2" />
    </>
  ),
  gauge: (
    <>
      <path d="M2.4 11.4a6.4 6.4 0 1111.2 0" />
      <path d="M8 11l3-3.4" />
      <circle cx="8" cy="11.2" r="1.1" />
    </>
  ),
  server: (
    <>
      <rect x="2.2" y="2.6" width="11.6" height="4.4" rx="1.2" />
      <rect x="2.2" y="9" width="11.6" height="4.4" rx="1.2" />
      <path d="M4.6 4.8h.01M4.6 11.2h.01" />
    </>
  ),
  download: (
    <>
      <path d="M8 2v7.2" />
      <path d="M5 6.4L8 9.4l3-3" />
      <path d="M2.6 12.6h10.8" />
    </>
  ),
  box: (
    <>
      <path d="M8 1.9l5.6 3.1v6.1L8 14.2 2.4 11.1V5z" />
      <path d="M2.4 5L8 8.1 13.6 5" />
      <path d="M8 8.1v6.1" />
    </>
  ),
  plug: (
    <>
      <path d="M6 2v3.2M10 2v3.2" />
      <path d="M4.2 5.2h7.6v2.4a3.8 3.8 0 01-7.6 0z" />
      <path d="M8 11.2v2.8" />
    </>
  ),
  map: (
    <>
      <path d="M2.2 4.2l3.8-1.9 4 1.9 3.8-1.9v9.4l-3.8 1.9-4-1.9-3.8 1.9z" />
      <path d="M6 2.3v9.4M10 4.2v9.4" />
    </>
  ),
  swap: (
    <>
      <path d="M3.2 5.6h9.2l-2.4-2.4" />
      <path d="M12.8 10.4H3.6l2.4 2.4" />
    </>
  ),
  refresh: (
    <>
      <path d="M13.4 8a5.4 5.4 0 11-1.7-4" />
      <path d="M13.4 1.8v2.8h-2.8" />
    </>
  ),
  sun: (
    <>
      <circle cx="8" cy="8" r="2.8" />
      <path d="M8 1.2v1.6M8 13.2v1.6M1.2 8h1.6M13.2 8h1.6M3.2 3.2l1.1 1.1M11.7 11.7l1.1 1.1M12.8 3.2l-1.1 1.1M4.3 11.7l-1.1 1.1" />
    </>
  ),
  moon: <path d="M13.2 9.8A5.6 5.6 0 016.2 2.8a5.6 5.6 0 107 7z" />,
  logout: (
    <>
      <path d="M6.2 2.4H3.6a1 1 0 00-1 1v9.2a1 1 0 001 1h2.6" />
      <path d="M10.2 5.2L13 8l-2.8 2.8" />
      <path d="M12.6 8H6.4" />
    </>
  ),
  menu: <path d="M2.4 4h11.2M2.4 8h11.2M2.4 12h11.2" />,
  check: <path d="M3.2 8.4l3.1 3.1 6.5-7" />,
  alert: (
    <>
      <path d="M8 2.4l6.2 10.8H1.8z" />
      <path d="M8 6.2v3.2M8 11.6h.01" />
    </>
  ),
  info: (
    <>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 7.2v4M8 5.3h.01" />
    </>
  )
};

function Icon({ name, size = 16 }: { name: string; size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICON_PATHS[name] ?? null}
    </svg>
  );
}

const activeJobStatuses = new Set(["queued", "running"]);
const problemJobStatuses = new Set(["failed", "interrupted"]);

const ROUTINE_NOTICE = /^(Loading|Refreshing|Updated|Searching|Preparing|Resolving|Submitting|Uploading|Verifying|Signing|Queueing|Installing|Saving|Restarting|Rebuilding|Updating|Removing|Deleting|Cancelling|Re-running)/i;
const PROBLEM_NOTICE = /fail|error|invalid|denied|expired|not found|too large|timed out|unreachable|missing/i;

const THEME_KEY = "l4d2-theme";

function readTheme(): Theme {
  try {
    return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
  } catch (error) {
    return "dark";
  }
}

function readHashView(): ViewId {
  const raw = window.location.hash.replace(/^#/, "");
  return (VIEW_IDS as readonly string[]).includes(raw) ? (raw as ViewId) : "overview";
}

async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  const csrf = document.cookie.split("; ").find((part) => part.startsWith("l4d2_csrf="))?.slice(10);
  if (csrf && init.method && init.method !== "GET") headers.set("X-CSRF-Token", csrf);
  const selected = localStorage.getItem("l4d2-room");
  if (selected) headers.set("X-L4D2-Room", selected);
  const response = await fetch(input, { credentials: "same-origin", ...init, headers });
  if (response.status === 401) {
    window.location.href = "/login?expired=1";
    throw new Error("Session expired");
  }
  return response;
}

async function postForm<T = { ok: boolean; message?: string }>(path: string, fields: Record<string, string> | URLSearchParams) {
  const body = fields instanceof URLSearchParams ? fields : new URLSearchParams(fields);
  const response = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body
  });
  const payload = (await response.json()) as T;
  const status = payload as { ok?: boolean; message?: string };
  if (!response.ok || status.ok === false) throw new Error(status.message || `Operation failed (${response.status})`);
  return { response, payload };
}

function formatBytes(value?: number | null) {
  const size = Number(value || 0);
  if (!size) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let current = size;
  let unit = 0;
  while (current >= 1024 && unit < units.length - 1) {
    current /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${current} ${units[unit]}` : `${current.toFixed(1)} ${units[unit]}`;
}

function formatPercent(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)}%` : "unknown";
}

function riskClass(percent?: number | null, warn = 80, danger = 92) {
  if (typeof percent !== "number") return "";
  if (percent >= danger) return "danger";
  if (percent >= warn) return "warn";
  return "";
}

function statusClass(status: string) {
  if (["active", "enabled", "succeeded", "running"].includes(status)) return "ok";
  if (["queued", "remote", "disabled", "missing"].includes(status)) return "warn";
  if (["failed", "interrupted", "deleted", "inactive"].includes(status)) return "danger";
  return "";
}

function fileSizeMb(bytes: number) {
  if (!bytes) return "not downloaded";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function mapLabel(map: CampaignMap) {
  return `${map.chapter}. ${map.display_name} (${map.name})`;
}

const PART_SUFFIX_RE = /[\s_\-]*[(（]?\s*part\s*\d+\s*[)）]?\s*$/i;

function stripPartSuffix(title: string) {
  if (!title) return title;
  const stripped = title.replace(PART_SUFFIX_RE, "").trim();
  return stripped || title;
}

function selectedCampaignForRoom(room: Room, campaigns: Campaign[]) {
  if (room.default_campaign_id) return room.default_campaign_id;
  return campaigns[0]?.id || "";
}

function matchesPackageFilters(addon: Addon, filters: PackageFilters) {
  const source = addon.source || "local";
  const hasRecord = Boolean(addon.source && addon.catalog_id);
  if (filters.text) {
    const haystack = [addon.title, addon.filename, addon.catalog_id, addon.maps.join(" ")].join(" ").toLowerCase();
    if (!haystack.includes(filters.text.toLowerCase())) return false;
  }
  if (filters.status !== "all" && addon.state !== filters.status) return false;
  if (filters.source !== "all" && source !== filters.source) return false;
  if (filters.record === "with" && !hasRecord) return false;
  if (filters.record === "without" && hasRecord) return false;
  return true;
}

function packageSizeLabel(addon: Addon) {
  if (addon.state === "remote") return "not downloaded";
  if (addon.state === "deleted") return "removed";
  return fileSizeMb(addon.size);
}

function packageMapsLabel(addon: Addon) {
  if (!addon.maps.length) return "mission only";
  return `${addon.maps.length} map${addon.maps.length > 1 ? "s" : ""}`;
}

export default function App() {
  const [state, setState] = useState<StateSnapshot | null>(null);
  const [status, setStatus] = useState("Loading...");
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [view, setView] = useState<ViewId>(readHashView);
  const [drawer, setDrawer] = useState(false);
  const [catalogResults, setCatalogResults] = useState<CatalogItem[]>([]);
  const [jobFilter, setJobFilter] = useState("current");
  const [packageFilters, setPackageFilters] = useState<PackageFilters>({ text: "", status: "all", source: "all", record: "all" });
  const [selectedPackages, setSelectedPackages] = useState<Set<string>>(new Set());
  const [selectedManifest, setSelectedManifest] = useState<Set<string>>(new Set());
  const [autoRefresh, setAutoRefresh] = useState(true);
  const timer = useRef<number | undefined>();
  const toastSeq = useRef(0);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (error) {
      /* storage unavailable — theme still applies for this session */
    }
  }, [theme]);

  useEffect(() => {
    function onHashChange() {
      setView(readHashView());
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    document.body.style.overflow = drawer ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [drawer]);

  function navigate(next: ViewId) {
    setView(next);
    setDrawer(false);
    if (window.location.hash !== `#${next}`) {
      window.location.hash = next;
    }
    window.scrollTo({ top: 0 });
  }

  function pushToast(message: string, tone: ToastTone) {
    const id = (toastSeq.current += 1);
    setToasts((list) => [...list.slice(-3), { id, message, tone }]);
    window.setTimeout(() => {
      setToasts((list) => list.filter((item) => item.id !== id));
    }, 5200);
  }

  function setNotice(message: string, tone?: ToastTone) {
    setStatus(message);
    const text = (message || "").trim();
    if (!text) return;
    if (tone) {
      pushToast(text, tone);
      return;
    }
    if (PROBLEM_NOTICE.test(text)) {
      pushToast(text, "danger");
      return;
    }
    if (ROUTINE_NOTICE.test(text) || /cancelled/i.test(text)) return;
    pushToast(text, "ok");
  }

  async function loadState(message = "Loading...") {
    setNotice(message);
    const response = await apiFetch("/api/state");
    if (!response.ok) throw new Error("Failed to load state");
    const data = (await response.json()) as StateSnapshot;
    setState(data);
    localStorage.setItem('l4d2-room', data.selected_room);
    setNotice(`Updated ${new Date(data.generated_at * 1000).toLocaleTimeString()}`);
  }

  useEffect(() => {
    loadState().catch((error) => setNotice(error.message));
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, []);

  useEffect(() => {
    if (!state) return;
    if (timer.current) window.clearTimeout(timer.current);
    const active = state.jobs.some((job) => activeJobStatuses.has(job.status));
    if (!autoRefresh && !active) return;
    timer.current = window.setTimeout(() => {
      loadState("Refreshing...").catch(() => undefined);
    }, active ? 2000 : 30000);
  }, [state, autoRefresh]);

  async function runAction(action: () => Promise<string | undefined>, loading: string) {
    setNotice(loading);
    try {
      const message = await action();
      if (message) setNotice(message);
      await loadState("Refreshing...");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Action failed", "danger");
    }
  }

  async function logout() {
    try {
      await apiFetch("/api/logout", { method: "POST" });
    } finally {
      window.location.href = "/login";
    }
  }

  if (!state) {
    return (
      <div className="boot">
        <div className="boot-card">
          <div className="brand-logo">L4D2</div>
          <h1>Server Manager</h1>
          <p>{status}</p>
        </div>
      </div>
    );
  }

  const roomsOnline = state.rooms.filter((room) => room.active === "active" && room.port_listening).length;
  const activeJobs = state.jobs.filter((job) => activeJobStatuses.has(job.status)).length;
  const problemJobs = state.jobs.filter((job) => problemJobStatuses.has(job.status)).length;
  const health = problemJobs > 0 || roomsOnline < state.rooms.length ? "warn" : "ok";

  const navBadges: Partial<Record<ViewId, { label: string; tone: string }>> = {
    rooms: { label: `${roomsOnline}/${state.rooms.length}`, tone: roomsOnline < state.rooms.length ? "warn" : "ok" },
    install: problemJobs > 0
      ? { label: `${problemJobs} failed`, tone: "danger" }
      : activeJobs > 0
        ? { label: `${activeJobs} active`, tone: "live" }
        : { label: `${state.jobs.length}`, tone: "" }
  };

  function renderView() {
    switch (view) {
      case "overview":
        return <Overview state={state as StateSnapshot} onNavigate={navigate} />;
      case "performance":
        return <SystemPanel system={state!.system} />;
      case "rooms":
        return <RoomsPanel state={state as StateSnapshot} runAction={runAction} />;
      case "operations":
        return <OperationsPanel rooms={state!.rooms} loadState={loadState} />;
      case "install":
        return (
          <div className="two-column">
            <InstallPanel results={catalogResults} setResults={setCatalogResults} setNotice={setNotice} loadState={loadState} />
            <JobsPanel jobs={state!.jobs} jobFilter={jobFilter} setJobFilter={setJobFilter} runAction={runAction} />
          </div>
        );
      case "packages":
        return (
          <PackagesPanel
            addons={state!.addons}
            filters={packageFilters}
            setFilters={setPackageFilters}
            selectedPackages={selectedPackages}
            setSelectedPackages={setSelectedPackages}
            selectedManifest={selectedManifest}
            setSelectedManifest={setSelectedManifest}
            runAction={runAction}
            loadState={loadState}
            setNotice={setNotice}
          />
        );
      case "mods":
        return (
          <div className="two-column">
            <ModsPanel addons={state!.addons} selectedManifest={selectedManifest} setSelectedManifest={setSelectedManifest} runAction={runAction} />
            <PluginsPanel plugins={state!.server_plugins} rooms={state!.rooms} runAction={runAction} />
          </div>
        );
      case "maps":
        return <CampaignPanel campaigns={state!.campaigns} mapCount={state!.maps.length} />;
      case "migration":
        return (
          <MigrationPanel
            selectedPackages={selectedPackages}
            selectedManifest={selectedManifest}
            loadState={loadState}
            setNotice={setNotice}
          />
        );
      default:
        return null;
    }
  }

  return (
    <div className={`app ${drawer ? "drawer-open" : ""}`}>
      {drawer && <div className="scrim" onClick={() => setDrawer(false)} />}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="brand-logo">L4D2</div>
          <div className="brand-text">
            <strong>Server Manager</strong>
            <span>Dedicated</span>
          </div>
        </div>
        <nav className="nav">
          {NAV_GROUPS.map((section) => (
            <div key={section.group}>
              <div className="nav-group">{section.group}</div>
              {section.items.map((item) => {
                const badge = navBadges[item.id];
                return (
                  <button
                    key={item.id}
                    className={`nav-item ${view === item.id ? "active" : ""}`}
                    onClick={() => navigate(item.id)}
                  >
                    <span className="nav-icon"><Icon name={item.icon} /></span>
                    <span className="nav-label">{item.label}</span>
                    {badge ? <span className={`nav-badge ${badge.tone}`}>{badge.label}</span> : null}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="foot-status">
            <div className="row">
              <span className={`dot ${health}`} />
              <span title={status}>{status}</span>
            </div>
          </div>
          <select style={{ width: '100%', marginBottom: 10 }} aria-label="Selected content room" value={state.selected_room} onChange={(event) => { localStorage.setItem('l4d2-room', event.target.value); loadState('Refreshing...').catch((error) => setNotice(error.message, 'danger')); }}>
              {state.rooms.map((room) => <option value={room.id} key={room.id}>{room.label}</option>)}
          </select>
          <div className="foot-actions">
            <label className="switch">
              <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />
              <span>Auto</span>
            </label>
            <button className="icon-btn" title="Refresh now" aria-label="Refresh now" onClick={() => loadState("Refreshing...")}>
              <Icon name="refresh" />
            </button>
            <button
              className="icon-btn"
              title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              aria-label="Toggle theme"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              <Icon name={theme === "dark" ? "sun" : "moon"} />
            </button>
            <button className="icon-btn" title="Log out" aria-label="Log out" onClick={logout}>
              <Icon name="logout" />
            </button>
          </div>
        </div>
      </aside>
      <main className="main">
        <div className="mobile-bar">
          <button className="icon-btn" aria-label="Open navigation" onClick={() => setDrawer(true)}>
            <Icon name="menu" />
          </button>
          <span className="title">{VIEW_TITLES[view]}</span>
          <span className={`dot ${health}`} />
        </div>
        <div className="page">{renderView()}</div>
      </main>
      {toasts.length > 0 && (
        <div className="toast-stack">
          {toasts.map((toast) => (
            <div className={`toast ${toast.tone}`} key={toast.id} role="status">
              <Icon name={toast.tone === "ok" ? "check" : toast.tone === "info" ? "info" : "alert"} />
              <div className="toast-message">{toast.message}</div>
              <button
                className="toast-close"
                aria-label="Dismiss"
                onClick={() => setToasts((list) => list.filter((item) => item.id !== toast.id))}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Overview({ state, onNavigate }: { state: StateSnapshot; onNavigate: (view: ViewId) => void }) {
  const roomsOnline = state.rooms.filter((room) => room.active === "active" && room.port_listening).length;
  const runningJobs = state.jobs.filter((job) => activeJobStatuses.has(job.status)).length;
  const problemJobs = state.jobs.filter((job) => problemJobStatuses.has(job.status)).length;
  const mapPackages = state.addons.filter((addon) => addon.kind === "map").length;
  const mods = state.addons.filter((addon) => addon.kind !== "map");
  const disabledMods = mods.filter((addon) => addon.state === "disabled").length;

  const cards: Array<{ label: string; value: string | number; tone: string; detail: string; view?: ViewId }> = [
    {
      label: "Rooms online",
      value: `${roomsOnline}/${state.rooms.length}`,
      tone: roomsOnline < state.rooms.length ? "warn" : "ok",
      detail: roomsOnline < state.rooms.length ? "check services" : "all listening",
      view: "rooms"
    },
    {
      label: "Running jobs",
      value: runningJobs,
      tone: "",
      detail: runningJobs > 0 ? "in progress" : "idle"
    },
    {
      label: "Failed jobs",
      value: problemJobs,
      tone: problemJobs > 0 ? "danger" : "ok",
      detail: problemJobs > 0 ? "needs review" : "none",
      view: "install"
    },
    {
      label: "Map packages",
      value: mapPackages,
      tone: "",
      detail: `${state.maps.length} maps installed`,
      view: "packages"
    },
    {
      label: "Mods",
      value: mods.length,
      tone: disabledMods > 0 ? "warn" : "",
      detail: disabledMods > 0 ? `${disabledMods} disabled` : "all enabled",
      view: "mods"
    }
  ];

  return (
    <section>
      <PanelHead title="Overview" subtitle="Rooms, jobs, and content at a glance." />
      <div className="kpi-strip">
        {cards.map((card) => {
          const body = (
            <>
              <span className="kpi-label">{card.label}</span>
              <span className="kpi-value">{card.value}</span>
              <span className="kpi-detail">{card.detail}</span>
            </>
          );
          return card.view ? (
            <button className={`kpi ${card.tone}`} key={card.label} onClick={() => onNavigate(card.view as ViewId)}>
              {body}
            </button>
          ) : (
            <article className={`kpi ${card.tone}`} key={card.label}>
              {body}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function Metric({ label, value, detail, percent, warn, danger }: { label: string; value: string; detail?: string; percent?: number | null; warn?: number; danger?: number }) {
  const bounded = typeof percent === "number" ? Math.max(0, Math.min(100, percent)) : null;
  return (
    <div className={`metric ${riskClass(percent, warn, danger)}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
      {bounded !== null && <div className="bar"><i style={{ width: `${bounded}%` }} /></div>}
    </div>
  );
}

function SystemPanel({ system }: { system?: SystemSnapshot }) {
  if (!system) {
    return <section className="panel"><div className="empty-state">System metrics unavailable.</div></section>;
  }
  const cpu = system.cpu || {};
  const memory = system.memory || {};
  const swap = system.swap || {};
  const load = Array.isArray(cpu.load_average) ? cpu.load_average.filter((value) => value !== null).join(", ") : "unknown";
  return (
    <section className="panel">
      <PanelHead title="Server Performance" subtitle="Host pressure, service processes, and storage." badge="live" />
      <div className="metric-grid">
        <Metric label="CPU" value={formatPercent(cpu.percent)} detail={`${cpu.cores || 0} cores · load ${load || "unknown"}`} percent={cpu.percent} warn={75} danger={90} />
        <Metric label="Memory" value={`${formatBytes(memory.used)} / ${formatBytes(memory.total)}`} detail={`${formatBytes(memory.available)} available`} percent={memory.percent} warn={75} danger={90} />
        <Metric label="Swap" value={`${formatBytes(swap.used)} / ${formatBytes(swap.total)}`} detail={`${formatBytes(swap.free)} free`} percent={swap.percent} warn={40} danger={75} />
        <Metric label="Uptime" value={system.uptime?.display || "unknown"} />
      </div>
      <div className="process-grid">
        {(system.processes || []).map((proc) => (
          <div className="process-row" key={proc.service}>
            <div>
              <strong>{proc.label || proc.service}</strong>
              <small>{proc.service}</small>
            </div>
            <Pill label={proc.active || "unknown"} tone={proc.active === "active" ? "ok" : "warn"} />
            <span>{formatBytes(proc.memory_current)}</span>
            <span>{proc.cpu_usage_nsec ? `${Math.round(proc.cpu_usage_nsec / 1000000000)}s CPU` : "CPU unknown"}</span>
          </div>
        ))}
      </div>
      <div className="metric-grid disk-grid">
        {(system.disk || []).map((disk) => (
          <Metric
            key={disk.id}
            label={`${disk.label} disk`}
            value={`${formatBytes(disk.used)} / ${formatBytes(disk.total)}`}
            detail={`${formatBytes(disk.free)} free at ${disk.path}`}
            percent={disk.percent}
          />
        ))}
      </div>
    </section>
  );
}

function RoomsPanel({ state, runAction }: { state: StateSnapshot; runAction: (action: () => Promise<string | undefined>, loading: string) => Promise<void> }) {
  const [roomMaps, setRoomMaps] = useState<Record<string, { campaignId: string; map: string }>>({});
  const previousDefaults = useRef<Record<string, string>>({});

  useEffect(() => {
    setRoomMaps((current) => {
      const next: typeof current = {};
      state.rooms.forEach((room) => {
        const campaigns = room.campaigns || state.campaigns;
        const existing = current[room.id];
        next[room.id] = existing && previousDefaults.current[room.id] === room.default_map &&
          campaigns.some((campaign) => campaign.id === existing.campaignId && campaign.maps.some((map) => map.name === existing.map))
          ? existing : { campaignId: selectedCampaignForRoom(room, campaigns), map: room.default_map };
        previousDefaults.current[room.id] = room.default_map;
      });
      return next;
    });
  }, [state]);

  function campaignMaps(roomId: string) {
    const campaigns = state.rooms.find((room) => room.id === roomId)?.campaigns || state.campaigns;
    const campaign = campaigns.find((item) => item.id === roomMaps[roomId]?.campaignId) || campaigns[0];
    return campaign?.maps || [];
  }

  async function save(room: Room, restart: boolean) {
    const map = roomMaps[room.id]?.map || room.default_map;
    const { payload } = await postForm("/api/default-map", { room: room.id, map, restart: restart ? "1" : "0" });
    return payload.message;
  }

  async function restart(room: Room) {
    const { payload } = await postForm("/api/restart", { room: room.id });
    return payload.message;
  }

  return (
    <section>
      <PanelHead title="Room Management" subtitle="Saving a default map is safe; restarting interrupts the room." />
      <div className="room-grid">
        {state.rooms.map((room) => {
          const online = room.active === "active" && room.port_listening;
          return (
            <article className="panel room-card" key={room.id}>
              <div className="card-title">
                <div>
                  <h3>{room.label}</h3>
                  <small>{room.service} · UDP {room.port}</small>
                </div>
                <Pill label={online ? "running" : room.active} tone={online ? "ok" : "danger"} />
              </div>
              <dl className="facts">
                <dt>Default map</dt><dd>{room.default_map || "unknown"}</dd>
                <dt>Port</dt><dd>{room.port_listening ? "listening" : "not listening"}</dd>
                <dt>Restarts</dt><dd>{room.restarts}</dd>
                <dt>Started</dt><dd>{room.started_at || "unknown"}</dd>
              </dl>
              <div className="control-row">
                <select
                  value={roomMaps[room.id]?.campaignId || ""}
                  onChange={(event) => {
                    const campaign = (room.campaigns || state.campaigns).find((item) => item.id === event.target.value);
                    setRoomMaps((current) => ({ ...current, [room.id]: { campaignId: event.target.value, map: campaign?.maps[0]?.name || "" } }));
                  }}
                >
                  {(room.campaigns || state.campaigns).map((campaign) => <option value={campaign.id} key={campaign.id}>{campaign.title}</option>)}
                </select>
                <select
                  value={roomMaps[room.id]?.map || ""}
                  onChange={(event) => setRoomMaps((current) => ({ ...current, [room.id]: { ...current[room.id], map: event.target.value } }))}
                >
                  {campaignMaps(room.id).map((map) => <option value={map.name} key={map.name}>{mapLabel(map)}</option>)}
                </select>
              </div>
              <div className="actions">
                <button onClick={() => runAction(() => save(room, false), "Saving default map...")}>Save map</button>
                <button className="danger" onClick={async () => (await confirmAction(`Save and restart ${room.label}? Players will disconnect.`)) && runAction(() => save(room, true), "Saving and restarting...")}>Save &amp; restart</button>
                <button className="danger" onClick={async () => (await confirmAction(`Restart ${room.label}? Players will disconnect.`)) && runAction(() => restart(room), "Restarting room...")}>Restart</button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function InstallPanel({ results, setResults, setNotice, loadState }: {
  results: CatalogItem[];
  setResults: (items: CatalogItem[]) => void;
  setNotice: (message: string, tone?: ToastTone) => void;
  loadState: (message?: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("map");
  const [workshopId, setWorkshopId] = useState("");
  const [workshopKind, setWorkshopKind] = useState("map");
  const [submitting, setSubmitting] = useState("");
  const [proxyResolve, setProxyResolve] = useState<ProxyResolve | null>(null);
  const localFile = useRef<HTMLInputElement>(null);
  const [localKind, setLocalKind] = useState("map");
  const [localId, setLocalId] = useState("");
  const [localParts, setLocalParts] = useState<WorkshopParts | null>(null);
  const [localFiles, setLocalFiles] = useState<Array<{ file: File; pick: string }>>([]);

  async function search() {
    setNotice("Searching catalog...");
    const response = await apiFetch(`/api/catalog/search?${new URLSearchParams({ query, kind })}`);
    const payload = await response.json();
    if (!response.ok) {
      setNotice(payload.message || "Search failed", "danger");
      return;
    }
    setResults(payload.results || []);
    setNotice(`${(payload.results || []).length} result(s)`);
  }

  async function installWorkshop() {
    if (submitting) return;
    setSubmitting("Submitting Workshop install request...");
    setNotice("Queueing Workshop install...");
    try {
      const { payload } = await postForm("/api/workshop/install", { kind: workshopKind, workshop_id: workshopId.trim() });
      setNotice(payload.message || "Install queued");
      await loadState("Refreshing...");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Failed to submit install request");
    } finally {
      setSubmitting("");
    }
  }

  async function installCatalog(item: CatalogItem) {
    if (submitting) return;
    setSubmitting(`Submitting install request for ${item.title}...`);
    setNotice("Queueing catalog install...");
    try {
      const { payload } = await postForm("/api/catalog/install", {
        source: item.source,
        kind: item.kind,
        id: item.id,
        title: item.title,
        url: item.url,
        install_ids: (item.install_ids || []).join(",")
      });
      setNotice(payload.message || "Install queued");
      await loadState("Refreshing...");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Failed to submit install request");
    } finally {
      setSubmitting("");
    }
  }

  async function resolveProxy(id: string, proxyKind: string) {
    if (submitting) return;
    const trimmed = id.trim();
    if (!trimmed) {
      setNotice("Enter a Workshop ID first.", "warn");
      return;
    }
    setSubmitting("Resolving Steam download links...");
    setNotice("Resolving Workshop download links...");
    try {
      const response = await apiFetch(`/api/workshop/resolve?${new URLSearchParams({ workshop_id: trimmed, kind: proxyKind })}`);
      const payload = (await response.json()) as ProxyResolve & { message?: string };
      if (!response.ok || !payload.ok) {
        setProxyResolve(null);
        setNotice(payload.message || "Failed to resolve download links", "danger");
        return;
      }
      setProxyResolve(payload);
      setNotice("Download links ready — fetch on your machine, then upload to install.");
    } catch (error) {
      setProxyResolve(null);
      setNotice(error instanceof Error ? error.message : "Failed to resolve download links");
    } finally {
      setSubmitting("");
    }
  }

  async function installLocalFile(file: File | undefined, uploadKind: string) {
    if (submitting) return;
    if (!file) {
      setNotice("Select a .vpk file first.", "warn");
      return;
    }
    setSubmitting(`Uploading ${file.name}...`);
    setNotice("Uploading and installing...");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", uploadKind);
      const response = await apiFetch("/api/upload", { method: "POST", body: form });
      const payload = await response.json();
      setNotice(payload.message || (response.ok ? "Uploaded" : "Upload failed"), response.ok ? "ok" : "danger");
      if (response.ok) await loadState("Refreshing...");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Upload failed", "danger");
    } finally {
      setSubmitting("");
    }
  }

  async function resolveLocalParts() {
    if (submitting) return;
    const trimmed = localId.trim();
    if (!trimmed) {
      setNotice("Enter a Workshop ID first.", "warn");
      return;
    }
    setSubmitting("Resolving Workshop parts...");
    setNotice("Resolving parts that belong to this Workshop ID...");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    try {
      const response = await apiFetch(`/api/workshop/parts?${new URLSearchParams({ workshop_id: trimmed, kind: localKind })}`, { signal: controller.signal });
      const payload = (await response.json()) as WorkshopParts;
      if (!response.ok || !payload.ok) {
        setLocalParts(null);
        setLocalFiles([]);
        setNotice(payload.message || "Failed to resolve parts", "danger");
        return;
      }
      setLocalParts(payload);
      setLocalFiles([]);
      const usable = (payload.parts || []).filter((p) => p.ok);
      setNotice(`${usable.length} downloadable part(s) found — pick your .vpk file(s) to match by size.`);
    } catch (error) {
      setLocalParts(null);
      if (error instanceof DOMException && error.name === "AbortError") {
        setNotice("Resolve timed out — Steam may be unreachable from the server. Try 'Install ID' or 'Proxy download'.", "danger");
      } else {
        setNotice(error instanceof Error ? error.message : "Failed to resolve parts", "danger");
      }
    } finally {
      window.clearTimeout(timeout);
      setSubmitting("");
    }
  }

  function autoMatchPick(size: number) {
    if (!localParts) return "";
    const matches = (localParts.parts || []).filter((p) => p.ok && Number(p.file_size || 0) === size);
    return matches.length === 1 ? String(matches[0].workshop_id) : "";
  }

  function onLocalFilesChange(files: File[]) {
    if (!localParts) {
      setLocalFiles(files.map((file) => ({ file, pick: "" })));
      return;
    }
    const next = files.map((file) => ({ file, pick: autoMatchPick(file.size) }));
    setLocalFiles(next);
    const auto = next.filter((entry) => entry.pick).length;
    setNotice(`${files.length} file(s) selected · ${auto} auto-matched by size.`);
  }

  function setFilePick(index: number, pick: string) {
    setLocalFiles((prev) => prev.map((entry, i) => (i === index ? { ...entry, pick } : entry)));
  }

  async function uploadWorkshopFiles() {
    if (submitting) return;
    if (!localParts) {
      setNotice("Resolve the Workshop ID parts first.", "warn");
      return;
    }
    if (!localFiles.length) {
      setNotice("Select one or more .vpk files first.", "warn");
      return;
    }
    let done = 0;
    const failures: string[] = [];
    for (let i = 0; i < localFiles.length; i += 1) {
      const { file, pick } = localFiles[i];
      setSubmitting(`Uploading ${file.name} (${i + 1}/${localFiles.length})...`);
      setNotice(`Verifying and installing ${file.name}...`);
      try {
        const form = new FormData();
        form.append("file", file);
        form.append("kind", localKind);
        form.append("workshop_id", localParts.workshop_id);
        if (pick) form.append("expected_id", pick);
        const response = await apiFetch("/api/upload/workshop", { method: "POST", body: form });
        const payload = await response.json();
        if (response.ok) {
          done += 1;
        } else {
          failures.push(`${file.name}: ${payload.message || "failed"}`);
        }
      } catch (error) {
        failures.push(`${file.name}: ${error instanceof Error ? error.message : "failed"}`);
      }
    }
    setSubmitting("");
    if (failures.length) {
      setNotice(`Installed ${done}/${localFiles.length}. Failed: ${failures.join("; ")}`);
    } else {
      setNotice(`Installed ${done}/${localFiles.length} file(s).`);
      setLocalParts(null);
      setLocalFiles([]);
      if (localFile.current) localFile.current.value = "";
    }
    await loadState("Refreshing...");
  }

  return (
    <section className="panel">
      {submitting && (
        <div className="busy-overlay" role="alert" aria-busy="true">
          <div className="busy-card">
            <span className="busy-spinner" aria-hidden="true" />
            <span className="busy-text">{submitting}</span>
            <span className="busy-hint">Please wait — do not click again.</span>
          </div>
        </div>
      )}
      <PanelHead title="Install Maps and Mods" subtitle="Search the catalog, or install a known Workshop ID directly." badge="Workshop" />
      <form className="tool-row" onSubmit={(event) => { event.preventDefault(); search(); }}>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Run To The Hills" />
        <select value={kind} onChange={(event) => setKind(event.target.value)}>
          <option value="map">Map</option>
          <option value="mod">Mod</option>
        </select>
        <button type="submit">Search</button>
      </form>
      <div className="tool-row">
        <input value={workshopId} onChange={(event) => setWorkshopId(event.target.value)} inputMode="numeric" placeholder="Workshop ID" />
        <select value={workshopKind} onChange={(event) => setWorkshopKind(event.target.value)}>
          <option value="map">Map</option>
          <option value="mod">Mod</option>
        </select>
        <button onClick={installWorkshop} disabled={Boolean(submitting)}>Install ID</button>
        <button className="secondary" onClick={() => resolveProxy(workshopId, workshopKind)} disabled={Boolean(submitting)}>Proxy download</button>
      </div>
      {proxyResolve && (
        <ProxyDownloadCard
          resolve={proxyResolve}
          submitting={Boolean(submitting)}
          onClose={() => setProxyResolve(null)}
          onInstallFile={installLocalFile}
        />
      )}
      <div className="tool-card proxy-local">
        <h3>Verified local install</h3>
        <p>Already have the matching <code>.vpk</code>? Enter the Workshop ID, then pick the file — the server verifies it by exact byte size before installing it under that ID.</p>
        <div className="tool-row">
          <input
            value={localId}
            onChange={(event) => { setLocalId(event.target.value); setLocalParts(null); setLocalFiles([]); }}
            inputMode="numeric"
            placeholder="Workshop ID"
          />
          <select value={localKind} onChange={(event) => { setLocalKind(event.target.value); setLocalParts(null); setLocalFiles([]); }}>
            <option value="map">Map package</option>
            <option value="mod">Mod</option>
          </select>
          <button className="secondary" disabled={Boolean(submitting)} onClick={resolveLocalParts}>Resolve parts</button>
        </div>
        {localParts && (
          <>
            <div className="proxy-list">
              {(localParts.parts || []).map((part, index) => {
                const usable = part.ok && Number(part.file_size || 0) > 0;
                const matched = usable && localFiles.some((entry) => Number(part.file_size || 0) === entry.file.size);
                return (
                  <div className={`proxy-item${matched ? " matched" : ""}`} key={`${part.workshop_id || index}`}>
                    <div className="proxy-meta">
                      <strong>{part.title || `Workshop ${part.workshop_id}`}</strong>
                      <div className="mono">
                        {part.workshop_id}
                        {part.file_size ? ` · ${formatBytes(part.file_size)}` : ""}
                      </div>
                      {!part.ok && <small>{part.message || "Could not resolve this item"}</small>}
                    </div>
                    {matched && <Pill label="size match" tone="ok" />}
                  </div>
                );
              })}
            </div>
            <div className="actions">
              <FileField inputRef={localFile} accept=".vpk" label="Choose VPK file(s)" multiple onChangeFiles={onLocalFilesChange} />
              <button disabled={Boolean(submitting) || !localFiles.length} onClick={uploadWorkshopFiles}>
                Verify &amp; install{localFiles.length > 1 ? ` (${localFiles.length})` : ""}
              </button>
            </div>
            {localFiles.length > 0 && (
              <div className="proxy-list">
                {localFiles.map((entry, index) => {
                  const auto = autoMatchPick(entry.file.size);
                  return (
                    <div className="proxy-item" key={`${entry.file.name}-${index}`}>
                      <div className="proxy-meta">
                        <strong>{entry.file.name}</strong>
                        <div className="mono">{formatBytes(entry.file.size)}</div>
                      </div>
                      <div className="actions right">
                        {entry.pick
                          ? <Pill label={auto && auto === entry.pick ? "auto-matched" : "manual"} tone="ok" />
                          : <Pill label="no match" tone="warn" />}
                        <select value={entry.pick} onChange={(event) => setFilePick(index, event.target.value)}>
                          <option value="">Auto / unmatched</option>
                          {(localParts.parts || []).filter((p) => p.ok).map((p) => (
                            <option value={String(p.workshop_id)} key={String(p.workshop_id)}>
                              {p.workshop_id} · {formatBytes(p.file_size)}
                            </option>
                          ))}
                        </select>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
      <div className="result-list">
        {!results.length && <div className="empty-state">Search results will appear here.</div>}
        {results.map((item) => (
          <article className="result-item" key={`${item.source}-${item.id}`}>
            <div>
              <strong>{item.title}</strong>
              <div className="mono">{item.kind} · {item.source} {item.id}</div>
              {item.install_ids && item.install_ids.length > 1 && <div className="mono">packages {item.install_ids.join(", ")}</div>}
              {item.summary && <p>{item.summary}</p>}
              {item.reason && <small>{item.reason}</small>}
            </div>
            <div className="actions right">
              {item.size && <Pill label={item.size} />}
              <a href={item.url} target="_blank" rel="noreferrer">Open</a>
              {item.source === "workshop" && (
                <button className="secondary" disabled={Boolean(submitting)} onClick={() => resolveProxy(item.id, item.kind)}>Proxy</button>
              )}
              <button disabled={!item.installable || Boolean(submitting)} onClick={() => installCatalog(item)}>Install</button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function ProxyDownloadCard({ resolve, submitting, onClose, onInstallFile }: {
  resolve: ProxyResolve;
  submitting: boolean;
  onClose: () => void;
  onInstallFile: (file: File | undefined, kind: string) => Promise<void>;
}) {
  const items: ProxyItem[] = resolve.collection ? (resolve.items || []) : [resolve as ProxyItem];
  const downloadable = items.filter((entry) => entry.ok && entry.file_url);
  return (
    <div className="tool-card proxy-card">
      <div className="proxy-head">
        <h3>Proxy download {resolve.collection ? `· ${items.length} parts` : ""}</h3>
        <button className="secondary" onClick={onClose}>Close</button>
      </div>
      <p>Download on <strong>your machine</strong>, then upload to install. Use this when the server cannot reach Steam.</p>
      <div className="proxy-list">
        {!downloadable.length && <div className="empty-state">No downloadable link returned — the item may be restricted.</div>}
        {items.map((entry, index) => (
          <div className="proxy-item" key={`${entry.workshop_id || index}`}>
            <div className="proxy-meta">
              <strong>{entry.title || `Workshop ${entry.workshop_id}`}</strong>
              <div className="mono">
                {entry.suggested_filename || entry.workshop_id}
                {entry.file_size ? ` · ${formatBytes(entry.file_size)}` : ""}
              </div>
              {!entry.ok && <small>{entry.message || "Could not resolve this item"}</small>}
            </div>
            {entry.ok && entry.file_url && (
              <div className="actions right">
                <a className="proxy-step" href={entry.file_url} target="_blank" rel="noreferrer" download={entry.suggested_filename}>① Download</a>
                <label className="file-field proxy-step">
                  <span className="file-button">② Select &amp; install</span>
                  <input
                    type="file"
                    accept=".vpk"
                    disabled={submitting}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      event.target.value = "";
                      onInstallFile(file, entry.kind || resolve.kind || "map");
                    }}
                  />
                </label>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function JobsPanel({ jobs, jobFilter, setJobFilter, runAction }: {
  jobs: Job[];
  jobFilter: string;
  setJobFilter: (value: string) => void;
  runAction: (action: () => Promise<string | undefined>, loading: string) => Promise<void>;
}) {
  const currentJobs = jobs.filter((job) => activeJobStatuses.has(job.status) || problemJobStatuses.has(job.status));
  const problemJobs = jobs.filter((job) => problemJobStatuses.has(job.status));
  const historyJobs = jobs.filter((job) => job.status === "succeeded");
  const filtered = jobFilter === "current" ? currentJobs : jobFilter === "problem" ? problemJobs : jobFilter === "history" ? historyJobs.slice(0, 8) : jobs;

  async function cancel(job: Job) {
    const { payload } = await postForm("/api/job/cancel", { job_id: job.id });
    return payload.message;
  }

  async function remove(job: Job) {
    if (!(await confirmAction("Delete this job record? Only the history entry is removed."))) return "Delete cancelled";
    const { payload } = await postForm("/api/job/delete", { job_id: job.id });
    return payload.message;
  }

  async function rerun(job: Job) {
    const { payload } = await postForm("/api/job/rerun", { job_id: job.id });
    return payload.message;
  }

  return (
    <section className="panel">
      <PanelHead title="Jobs" subtitle="Install, export, and import tasks." badge={`${currentJobs.length} current`} />
      <Segmented value={jobFilter} setValue={setJobFilter} options={[["current", "Current"], ["problem", "Failed"], ["history", "History"], ["all", "All"]]} />
      <div className="job-list">
        {!filtered.length && <div className="empty-state">No jobs match this filter.</div>}
        {filtered.map((job) => {
          const active = activeJobStatuses.has(job.status);
          const canRerun = !active && ["succeeded", "failed", "cancelled", "interrupted"].includes(job.status);
          return (
            <article className={`job-card ${statusClass(job.status)}`} key={job.id}>
              <div className="card-title">
                <div>
                  <h3>{job.title || job.catalog_id || job.workshop_id || job.export_filename || job.id}</h3>
                  <small>{job.type || job.source || "task"} {job.kind || ""} · {job.stage || "done"}</small>
                </div>
                <Pill label={job.status} tone={statusClass(job.status)} />
              </div>
              {(active || typeof job.progress === "number") && <JobProgress job={job} active={active} />}
              {job.install_ids && job.install_ids.length > 1 && <div className="mono muted">packages {job.install_ids.join(", ")}</div>}
              {job.message && <JobMessage message={job.message} />}
              <div className="actions">
                {active && <button className="secondary" onClick={() => runAction(() => cancel(job), "Cancelling job...")}>Cancel</button>}
                {canRerun && <button onClick={() => runAction(() => rerun(job), "Re-running install...")}>Re-run</button>}
                {job.type === "export" && job.status === "succeeded" && job.download_url && <a href={job.download_url}>Download</a>}
                {!active && <button className="danger" onClick={() => runAction(() => remove(job), "Deleting job...")}>Delete</button>}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function JobProgress({ job, active }: { job: Job; active: boolean }) {
  const percent = Math.max(0, Math.min(100, Number(job.progress || 0)));
  const downloaded = Number(job.downloaded_bytes || 0);
  const total = Number(job.total_bytes || 0);
  const sample = useRef<{ bytes: number; time: number } | null>(null);
  const [speed, setSpeed] = useState(0);

  useEffect(() => {
    if (!active) {
      sample.current = null;
      setSpeed(0);
      return;
    }
    const now = Date.now();
    const prev = sample.current;
    if (prev && downloaded > prev.bytes && now > prev.time) {
      const rate = ((downloaded - prev.bytes) * 1000) / (now - prev.time);
      setSpeed(rate);
    } else if (prev && downloaded < prev.bytes) {
      setSpeed(0);
    }
    sample.current = { bytes: downloaded, time: now };
  }, [downloaded, active]);

  const itemsTotal = Number(job.items_total || 0);
  const itemsDone = Number(job.items_done || 0);
  return (
    <div className="job-progress">
      <div className="bar"><i style={{ width: `${percent}%` }} /></div>
      <div className="progress-meta mono">
        <span>{percent}%</span>
        {total > 0 && <span>{formatBytes(downloaded)} / {formatBytes(total)}</span>}
        {active && speed > 0 && <span>{formatBytes(speed)}/s</span>}
        {itemsTotal > 1 && <span>part {Math.min(itemsTotal, itemsDone + (active ? 1 : 0))}/{itemsTotal}</span>}
      </div>
    </div>
  );
}

function JobMessage({ message }: { message: string }) {
  const trimmed = message.trim();
  const lines = trimmed.split(/\r?\n/);
  const isMultiline = lines.length > 1 || trimmed.length > 160;
  if (!isMultiline) {
    return <p className="job-summary">{trimmed}</p>;
  }
  const summary = (lines.find((line) => /error|failed|exception|traceback/i.test(line)) || lines[lines.length - 1] || lines[0]).trim();
  return (
    <details className="job-detail">
      <summary><span className="job-summary">{summary.slice(0, 200)}</span><span className="more">Details</span></summary>
      <pre>{trimmed}</pre>
    </details>
  );
}

function PackagesPanel(props: {
  addons: Addon[];
  filters: PackageFilters;
  setFilters: (value: PackageFilters) => void;
  selectedPackages: Set<string>;
  setSelectedPackages: (value: Set<string>) => void;
  selectedManifest: Set<string>;
  setSelectedManifest: (value: Set<string>) => void;
  runAction: (action: () => Promise<string | undefined>, loading: string) => Promise<void>;
  loadState: (message?: string) => Promise<void>;
  setNotice: (message: string, tone?: ToastTone) => void;
}) {
  const packages = props.addons.filter((addon) => addon.kind === "map");
  const filtered = packages.filter((addon) => matchesPackageFilters(addon, props.filters));
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [regrouping, setRegrouping] = useState(false);

  type PackageGroup = { key: string; title: string; members: Addon[] };
  const groups: PackageGroup[] = [];
  const groupIndex = new Map<string, number>();
  for (const addon of filtered) {
    const key = addon.group_id || addon.catalog_id || addon.filename;
    if (groupIndex.has(key)) {
      groups[groupIndex.get(key)!].members.push(addon);
    } else {
      groupIndex.set(key, groups.length);
      groups.push({ key, title: addon.group_title || "", members: [addon] });
    }
  }

  function toggleExpanded(key: string) {
    const next = new Set(expanded);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setExpanded(next);
  }

  async function regroup() {
    setRegrouping(true);
    props.setNotice("Rebuilding map groups...");
    try {
      const { payload } = await postForm("/api/map-package/regroup", {});
      props.setNotice(payload.message || "Map groups rebuilt.");
      await props.loadState("Refreshing...");
    } catch (error) {
      props.setNotice(error instanceof Error ? error.message : "Regroup failed");
    } finally {
      setRegrouping(false);
    }
  }

  function updateFilters(partial: Partial<PackageFilters>) {
    props.setFilters({ ...props.filters, ...partial });
  }

  function toggle(setter: (value: Set<string>) => void, current: Set<string>, filename: string, checked: boolean) {
    const next = new Set(current);
    if (checked) next.add(filename);
    else next.delete(filename);
    setter(next);
  }

  async function addonState(addon: Addon) {
    const target = addon.state === "enabled" ? "disabled" : "enabled";
    const { payload } = await postForm("/api/addon/state", { filename: addon.filename, state: target });
    return payload.message;
  }

  async function deletePackage(addon: Addon, mode: "soft" | "purge") {
    const copy = mode === "purge"
      ? `Permanently delete ${addon.filename}? This removes local files and source records.`
      : `Delete local files for ${addon.filename}? Source records stay for reinstall.`;
    if (!(await confirmAction(copy))) return "Delete cancelled";
    const { payload } = await postForm("/api/map-package/delete", { filename: addon.filename, mode });
    return payload.message;
  }

  async function reinstall(addon: Addon) {
    const { payload } = await postForm("/api/map-package/reinstall", { filename: addon.filename });
    return payload.message;
  }

  async function removeManifest(addon: Addon) {
    if (!(await confirmAction(`Remove source record for ${addon.filename}? Local VPK files stay.`))) return "Remove cancelled";
    const { payload } = await postForm("/api/manifest/remove-record", { filename: addon.filename });
    return payload.message;
  }

  async function exportOne(addon: Addon) {
    const body = new URLSearchParams();
    body.append("filename", addon.filename);
    const { payload } = await postForm("/api/map-package/export-job", body);
    return payload.message;
  }

  function renderRow(addon: Addon) {
    const remote = addon.state === "remote";
    const deleted = addon.state === "deleted";
    const baseTitle = addon.title && addon.title !== addon.filename ? stripPartSuffix(addon.title) : addon.filename;
    return (
      <article className="pkg-row" key={addon.filename}>
        <div className="pkg-select">
          <label>
            <input type="checkbox" checked={props.selectedManifest.has(addon.filename)} onChange={(event) => toggle(props.setSelectedManifest, props.selectedManifest, addon.filename, event.target.checked)} />
            Manifest
          </label>
          {!remote && !deleted && (
            <label>
              <input type="checkbox" checked={props.selectedPackages.has(addon.filename)} onChange={(event) => toggle(props.setSelectedPackages, props.selectedPackages, addon.filename, event.target.checked)} />
              ZIP
            </label>
          )}
        </div>
        <div className="pkg-main">
          <div className="pkg-title">
            <strong title={baseTitle}>{baseTitle}</strong>
            {addon.source && addon.catalog_id ? <span className="tag">{addon.source} {addon.catalog_id}</span> : <span className="tag">local</span>}
          </div>
          <div className="pkg-meta" title={addon.maps.join(", ") || "mission only"}>
            {addon.filename} · {packageMapsLabel(addon)} · {packageSizeLabel(addon)}
          </div>
        </div>
        <Pill label={addon.state} tone={statusClass(addon.state)} />
        <div className="pkg-actions">
          {!deleted && !remote && (
            <button className="secondary sm" onClick={() => props.runAction(() => addonState(addon), "Updating package state...")}>
              {addon.state === "enabled" ? "Disable" : "Enable"}
            </button>
          )}
          <RowMenu>
            {addon.url && <a className="menu-item" href={addon.url} target="_blank" rel="noreferrer">Open page</a>}
            {!remote && !deleted && <button className="menu-item" onClick={() => props.runAction(() => exportOne(addon), "Queueing export...")}>Export ZIP</button>}
            {remote && addon.reinstallable && <button className="menu-item" onClick={() => props.runAction(() => reinstall(addon), "Queueing source reinstall...")}>Reinstall source</button>}
            {remote && <button className="menu-item" onClick={() => props.runAction(() => removeManifest(addon), "Removing source record...")}>Remove record</button>}
            {!remote && !deleted && <button className="menu-item danger" onClick={() => props.runAction(() => deletePackage(addon, "soft"), "Deleting local files...")}>Delete local files</button>}
            {!deleted && <button className="menu-item danger" onClick={() => props.runAction(() => deletePackage(addon, "purge"), "Purging package...")}>Purge package</button>}
          </RowMenu>
        </div>
      </article>
    );
  }

  function groupTone(members: Addon[]) {
    if (members.some((m) => m.state === "remote")) return "warn";
    if (members.some((m) => m.state === "disabled")) return "warn";
    if (members.some((m) => m.state === "deleted")) return "danger";
    return "ok";
  }

  function groupStateLabel(members: Addon[]) {
    if (members.some((m) => m.state === "remote")) return "missing parts";
    if (members.some((m) => m.state === "disabled")) return "mixed";
    if (members.some((m) => m.state === "deleted")) return "deleted";
    return "enabled";
  }

  return (
    <section className="panel">
      <PanelHead title="Map Packages" subtitle="Filter installed and recorded packages; deletion keeps source records unless purged." badge={`${filtered.length}/${packages.length} visible`} />
      <div className="filter-row">
        <input value={props.filters.text} onChange={(event) => updateFilters({ text: event.target.value })} placeholder="Filter packages" />
        <select value={props.filters.status} onChange={(event) => updateFilters({ status: event.target.value })}>
          <option value="all">All states</option>
          <option value="enabled">Enabled</option>
          <option value="disabled">Disabled</option>
          <option value="remote">Remote</option>
          <option value="deleted">Deleted</option>
        </select>
        <select value={props.filters.source} onChange={(event) => updateFilters({ source: event.target.value })}>
          <option value="all">All sources</option>
          <option value="workshop">Workshop</option>
          <option value="gamemaps">GameMaps</option>
          <option value="local">Local</option>
        </select>
        <select value={props.filters.record} onChange={(event) => updateFilters({ record: event.target.value })}>
          <option value="all">All records</option>
          <option value="with">Has record</option>
          <option value="without">No record</option>
        </select>
        <button className="secondary" disabled={regrouping} onClick={regroup}>{regrouping ? "Rebuilding..." : "Rebuild groups"}</button>
      </div>
      <div className="pkg-list">
        {!filtered.length && <div className="empty-state">No map packages match this filter.</div>}
        {groups.map((group) => {
          if (group.members.length <= 1) {
            return renderRow(group.members[0]);
          }
          const open = expanded.has(group.key);
          const totalSize = group.members.reduce((sum, m) => sum + (m.size || 0), 0);
          const groupTitle = group.title || stripPartSuffix(group.members[0].title) || group.members[0].filename;
          return (
            <div className={`pkg-group ${open ? "open" : ""}`} key={group.key}>
              <button type="button" className="pkg-group-head" onClick={() => toggleExpanded(group.key)}>
                <span className="group-arrow">{open ? "\u25be" : "\u25b8"}</span>
                <div className="group-meta">
                  <div className="group-title">
                    <strong>{groupTitle}</strong>
                    <Pill label={groupStateLabel(group.members)} tone={groupTone(group.members)} />
                  </div>
                  <div className="mono">{group.members.length} parts · workshop {group.key}</div>
                </div>
                <span>{fileSizeMb(totalSize)}</span>
              </button>
              {open && <div className="pkg-group-body">{group.members.map((member) => renderRow(member))}</div>}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function MigrationPanel({ selectedPackages, selectedManifest, loadState, setNotice }: {
  selectedPackages: Set<string>;
  selectedManifest: Set<string>;
  loadState: (message?: string) => Promise<void>;
  setNotice: (message: string, tone?: ToastTone) => void;
}) {
  const manifestFile = useRef<HTMLInputElement>(null);
  const zipFile = useRef<HTMLInputElement>(null);
  const vpkFile = useRef<HTMLInputElement>(null);
  const [vpkKind, setVpkKind] = useState("map");

  async function exportManifest() {
    if (!selectedManifest.size) {
      setNotice("Select at least one manifest record.", "warn");
      return;
    }
    setNotice("Preparing manifest...");
    const body = new URLSearchParams();
    selectedManifest.forEach((filename) => body.append("filename", filename));
    const response = await apiFetch("/api/manifest/export", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
    if (!response.ok) {
      const payload = await response.json();
      setNotice(payload.message || "Manifest export failed", "danger");
      return;
    }
    downloadBlob(response, "l4d2-manager-manifest.json");
    setNotice("Manifest exported.");
  }

  async function exportZip() {
    if (!selectedPackages.size) {
      setNotice("Select at least one installed map package for ZIP export.", "warn");
      return;
    }
    setNotice("Queueing ZIP export...");
    const body = new URLSearchParams();
    selectedPackages.forEach((filename) => body.append("filename", filename));
    const { payload } = await postForm("/api/map-package/export-job", body);
    setNotice(payload.message || "Export queued");
    await loadState("Refreshing...");
  }

  async function upload(path: string, file: File | undefined, kind?: string) {
    if (!file) {
      setNotice("Select a file first.", "warn");
      return;
    }
    const form = new FormData();
    form.append("file", file);
    if (kind) form.append("kind", kind);
    setNotice("Uploading...");
    const response = await apiFetch(path, { method: "POST", body: form });
    const payload = await response.json();
    setNotice(payload.message || (response.ok ? "Uploaded" : "Upload failed"), response.ok ? "ok" : "danger");
    if (response.ok) await loadState("Refreshing...");
  }

  return (
    <section className="panel">
      <PanelHead title="Migration" subtitle="Source manifest first; ZIP when sources are gone; VPK for single-file imports." badge="Manifest / ZIP / VPK" />
      <div className="migration-grid">
        <div className="tool-card">
          <h3>Source manifest</h3>
          <p>Moves source records and reinstall metadata without transferring large VPK files.</p>
          <div className="actions">
            <button onClick={exportManifest}>Export records</button>
            <FileField inputRef={manifestFile} accept=".json,application/json" label="Choose JSON" />
            <button className="secondary" onClick={() => upload("/api/manifest/import", manifestFile.current?.files?.[0])}>Import</button>
          </div>
        </div>
        <div className="tool-card">
          <h3>Complete ZIP</h3>
          <p>Transfers VPK files and metadata when sources are missing or the target is offline.</p>
          <div className="actions">
            <button className="secondary" onClick={exportZip}>Export ZIP</button>
            <FileField inputRef={zipFile} accept=".zip" label="Choose ZIP" />
            <button className="secondary" onClick={() => upload("/api/upload", zipFile.current?.files?.[0], "map")}>Import ZIP</button>
          </div>
        </div>
        <div className="tool-card">
          <h3>Manual VPK</h3>
          <p>Imports one local VPK that was downloaded elsewhere.</p>
          <div className="actions">
            <FileField inputRef={vpkFile} accept=".vpk" label="Choose VPK" />
            <select value={vpkKind} onChange={(event) => setVpkKind(event.target.value)}>
              <option value="map">Map package</option>
              <option value="mod">Mod</option>
            </select>
            <button className="secondary" onClick={() => upload("/api/upload", vpkFile.current?.files?.[0], vpkKind)}>Import VPK</button>
          </div>
        </div>
      </div>
    </section>
  );
}

function ModsPanel({ addons, selectedManifest, setSelectedManifest, runAction }: {
  addons: Addon[];
  selectedManifest: Set<string>;
  setSelectedManifest: (value: Set<string>) => void;
  runAction: (action: () => Promise<string | undefined>, loading: string) => Promise<void>;
}) {
  const mods = addons.filter((addon) => addon.kind !== "map");
  async function addonState(addon: Addon) {
    const target = addon.state === "enabled" ? "disabled" : "enabled";
    const { payload } = await postForm("/api/addon/state", { filename: addon.filename, state: target });
    return payload.message;
  }
  async function installManifest(addon: Addon) {
    const { payload } = await postForm("/api/manifest/install", { filename: addon.filename });
    return payload.message;
  }
  async function removeManifest(addon: Addon) {
    if (!(await confirmAction(`Remove source record for ${addon.filename}?`))) return "Remove cancelled";
    const { payload } = await postForm("/api/manifest/remove-record", { filename: addon.filename });
    return payload.message;
  }
  function toggle(addon: Addon, checked: boolean) {
    const next = new Set(selectedManifest);
    if (checked) next.add(addon.filename);
    else next.delete(addon.filename);
    setSelectedManifest(next);
  }
  return (
    <section className="panel">
      <PanelHead title="Mod Management" subtitle="Enable or disable non-map VPK packages." badge={`${mods.length} vpks`} />
      <div className="compact-list">
        {!mods.length && <div className="empty-state">No manageable non-map VPK packages yet.</div>}
        {mods.map((addon) => (
          <div className="compact-row" key={addon.filename}>
            <label>
              <input type="checkbox" checked={selectedManifest.has(addon.filename)} onChange={(event) => toggle(addon, event.target.checked)} />
              <span>
                <strong>{addon.filename}</strong>
                <span className="mono">{addon.title && addon.title !== addon.filename ? addon.title : "local package"}</span>
              </span>
            </label>
            <Pill label={addon.state} tone={statusClass(addon.state)} />
            <span>{packageSizeLabel(addon)}</span>
            <div className="actions right">
              {addon.state !== "remote" && (
                <button className="secondary sm" onClick={() => runAction(() => addonState(addon), "Updating mod state...")}>
                  {addon.state === "enabled" ? "Disable" : "Enable"}
                </button>
              )}
              <RowMenu>
                {addon.url && <a className="menu-item" href={addon.url} target="_blank" rel="noreferrer">Open page</a>}
                {addon.state === "remote" && addon.reinstallable && <button className="menu-item" onClick={() => runAction(() => installManifest(addon), "Queueing source install...")}>Install source</button>}
                {addon.state === "remote" && <button className="menu-item danger" onClick={() => runAction(() => removeManifest(addon), "Removing source record...")}>Remove record</button>}
              </RowMenu>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function PluginsPanel({ plugins, rooms, runAction }: { plugins: ServerPlugins; rooms: Room[]; runAction: (action: () => Promise<string | undefined>, loading: string) => Promise<void> }) {
  const list = plugins.plugins || [];
  const installed = list.filter((plugin) => plugin.state !== "missing").length;
  async function installStack() {
    if (!(await confirmAction("Install MetaMod, SourceMod and Left4DHooks? Rooms keep running; restart them separately to load changes."))) return "Install cancelled";
    const { payload } = await postForm("/api/server-plugins/install", { stack: "basic" });
    return payload.message;
  }
  async function setPlugin(plugin: ServerPlugin) {
    const target = plugin.state === "enabled" ? "disabled" : "enabled";
    const { payload } = await postForm("/api/server-plugins/state", { plugin: plugin.id, state: target });
    return payload.message;
  }
  async function restart(target: string) {
    if (!(await confirmAction("Restarting rooms disconnects current players. Continue?"))) return "Restart cancelled";
    const { payload } = await postForm("/api/server-plugins/restart", { target });
    return payload.message;
  }
  return (
    <section className="panel">
      <PanelHead title="Server Plugins" subtitle="MetaMod, SourceMod, and the conservative plugin stack." badge={`${installed}/${list.length} installed`} />
      <div className="health-row">
        <Pill label={`MetaMod ${plugins.metamod ? "installed" : "missing"}`} tone={plugins.metamod ? "ok" : "warn"} />
        <Pill label={`SourceMod ${plugins.sourcemod ? "installed" : "missing"}`} tone={plugins.sourcemod ? "ok" : "warn"} />
      </div>
      <div className="actions">
        <button onClick={() => runAction(installStack, "Queueing plugin install...")}>Install stack</button>
        <button className="danger" onClick={() => runAction(() => restart("all"), "Restarting rooms...")}>Restart shared rooms</button>
        {rooms.filter((room) => plugins.affected_rooms?.includes(room.id)).map((room) => <button key={room.id} className="danger" onClick={() => runAction(() => restart(room.id), `Restarting ${room.label}...`)}>Restart {room.label}</button>)}
      </div>
      <div className="compact-list">
        {list.map((plugin) => (
          <div className="compact-row" key={plugin.id}>
            <div>
              <strong>{plugin.label}</strong>
              <div className="mono">{plugin.filename || ""}</div>
            </div>
            <Pill label={plugin.state} tone={statusClass(plugin.state)} />
            <span>{plugin.size ? formatBytes(plugin.size) : ""}</span>
            <div className="actions right">
              {plugin.state !== "missing" && (
                <button className="secondary sm" disabled={plugin.protected && plugin.state === 'enabled'} onClick={() => runAction(() => setPlugin(plugin), "Updating server plugin...")}>
                  {plugin.state === "enabled" ? "Disable" : "Enable"}
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function CampaignPanel({ campaigns, mapCount }: { campaigns: Campaign[]; mapCount: number }) {
  const [query, setQuery] = useState("");
  const term = query.trim().toLowerCase();
  const filtered = term
    ? campaigns
        .map((campaign) => ({
          ...campaign,
          maps: campaign.maps.filter((map) =>
            `${map.display_name} ${map.name}`.toLowerCase().includes(term) || campaign.title.toLowerCase().includes(term)
          )
        }))
        .filter((campaign) => campaign.maps.length)
    : campaigns;
  return (
    <section className="panel">
      <PanelHead title="Installed Maps" subtitle="Grouped by detected mission metadata." badge={`${mapCount} maps`} />
      <div className="filter-row">
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter campaigns or maps" />
      </div>
      <div className="campaign-list">
        {!filtered.length && <div className="empty-state">No maps match this filter.</div>}
        {filtered.map((campaign) => (
          <details key={campaign.id} open={term ? true : campaign.source !== "other"}>
            <summary>{campaign.title} <span>{campaign.maps.length} maps</span></summary>
            <div className="chapter-grid">
              {campaign.maps.map((map) => (
                <div key={map.name}><span>{map.chapter}.</span> {map.display_name} <code>{map.name}</code></div>
              ))}
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}

function PanelHead({ title, subtitle, badge }: { title: string; subtitle?: string; badge?: string }) {
  return (
    <div className="panel-head">
      <div>
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {badge && <Pill label={badge} />}
    </div>
  );
}

function Pill({ label, tone = "" }: { label: string; tone?: string }) {
  return <span className={`pill ${tone}`}>{label}</span>;
}

function Segmented({ value, setValue, options }: { value: string; setValue: (value: string) => void; options: Array<[string, string]> }) {
  return (
    <div className="segmented">
      {options.map(([key, label]) => (
        <button key={key} className={value === key ? "active" : ""} onClick={() => setValue(key)}>{label}</button>
      ))}
    </div>
  );
}

function RowMenu({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onClick(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);
  return (
    <div className="row-menu" ref={ref}>
      <button className="secondary menu-trigger" aria-label="More actions" onClick={() => setOpen((value) => !value)}>⋯</button>
      {open && <div className="menu-popover" onClick={() => setOpen(false)}>{children}</div>}
    </div>
  );
}

function FileField({ inputRef, accept, label = "Choose file", onChange, multiple, onChangeFiles }: { inputRef: RefObject<HTMLInputElement>; accept?: string; label?: string; onChange?: (size: number) => void; multiple?: boolean; onChangeFiles?: (files: File[]) => void }) {
  const [name, setName] = useState("");
  return (
    <label className="file-field">
      <span className="file-button">{label}</span>
      <span className="file-name">{name || "No file selected"}</span>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        onChange={(event) => {
          const files = Array.from(event.target.files || []);
          setName(files.length > 1 ? `${files.length} files selected` : files[0]?.name || "");
          onChange?.(files[0]?.size || 0);
          onChangeFiles?.(files);
        }}
      />
    </label>
  );
}

function downloadBlob(response: Response, fallback: string) {
  response.blob().then((blob) => {
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : fallback;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  });
}
