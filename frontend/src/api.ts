export type SystemStatus = {
  service: "ok";
  longport_configured: boolean;
  database_ready: boolean;
  watchlist_count: number;
  watchlist_errors: string[];
  longport_auth_mode: string;
};

export type Watchlist = {
  path: string;
  symbols: string[];
  errors: string[];
};

export type ScanResult = {
  symbol: string;
  total_score: number;
  triggered_factors: string[];
  data_status: string;
  f7_pattern: {stage:"cup_complete"|"handle_forming"|"breakout_ready"|"breakout_confirmed";confidence:number;timeframe:string} | null;
  classic_patterns: {timeframe:string;pattern_id:string;signal_name:string;pattern_name:string;detected_timestamp:string;bars_ago:number}[];
};

export type ScanStatus = {
  run_id: string;
  status: string;
  symbols_total: number;
  symbols_succeeded: number;
  symbols_failed: number;
  errors: { symbol?: string; message: string }[];
};

export type ChartBar = {
  timestamp: string;
  open: number; high: number; low: number; close: number; volume: number;
  ema12: number; ema144: number; ema169: number; ema576: number; ema676: number;
  macd_dif: number; macd_dea: number; macd_hist: number;
  trend_support: number | null; trend_resistance: number | null;
  is_closed: boolean;
};

export type BarsResponse = {
  symbol: string;
  timeframe: "weekly" | "daily" | "4hour";
  source: "local_parquet";
  adjustment_type: "forward";
  trade_session: "all";
  count: number;
  bars: ChartBar[];
};

export type FactorDiagnostic = { timeframe:string; factor_id:string; triggered:boolean; signal_name:string; tier:string|null; base_score:number; multiplier:number; contribution:number; bar_timestamp:string|null; reason:string|null; details:Record<string, unknown> };
export type Diagnostics = { symbol:string; run_id:string; completed_at:string; algorithm_version:string; config_version:string; scoring:{ total_score:number; base_total:number; confluence_total:number; pre_multiplier_score:number; coverage_multiplier:number; timeframe_scores:Record<string,number>; triggered_factors:string[] }; bar_timestamps:Record<string,string>; factors:FactorDiagnostic[] };
export type HistoryResponse = { symbol:string; history:{run_id:string;completed_at:string;market_data_cutoff:string;algorithm_version:string;total_score:number;triggered_factors:string[]}[] };
export type CacheResponse = { symbol:string; items:{timeframe:string;row_count:number;earliest_timestamp:string;latest_timestamp:string;adjustment_type:string;trade_session:string;sync_status:string;updated_at:string;last_error:string|null}[] };
export type AuthSettings={auth_mode:"oauth"|"apikey";oauth_client_id:string|null;configured:boolean;token_managed_by_sdk:boolean};
export type ProxySettings={enabled:boolean;host:string;port:number};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  status: () => request<SystemStatus>("/api/v1/status"),
  watchlist: () => request<Watchlist>("/api/v1/watchlist"),
  saveWatchlist: (symbols:string[]) => request<Watchlist>("/api/v1/watchlist", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbols }),
  }),
  latestResults: () => request<ScanResult[]>("/api/v1/results/latest"),
  createScan: () =>
    request<{ run_id: string; status: string; message: string }>("/api/v1/scans", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_type: "official" }),
    }),
  scanStatus: (runId: string) => request<ScanStatus>(`/api/v1/scans/${runId}`),
  bars: (symbol: string, timeframe: string, limit = 500) =>
    request<BarsResponse>(`/api/v1/symbols/${encodeURIComponent(symbol)}/bars?timeframe=${timeframe}&limit=${limit}`),
  diagnostics: (symbol:string) => request<Diagnostics>(`/api/v1/symbols/${encodeURIComponent(symbol)}/diagnostics`),
  history: (symbol:string) => request<HistoryResponse>(`/api/v1/symbols/${encodeURIComponent(symbol)}/history`),
  cache: (symbol:string) => request<CacheResponse>(`/api/v1/symbols/${encodeURIComponent(symbol)}/cache`),
  authSettings:()=>request<AuthSettings>("/api/v1/settings/auth"),
  saveAuthSettings:(auth_mode:"oauth"|"apikey",oauth_client_id:string)=>request<AuthSettings>("/api/v1/settings/auth",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({auth_mode,oauth_client_id})}),
  authorizeOAuth:()=>request<{status:string;auth_mode:string}>("/api/v1/settings/auth/oauth/authorize",{method:"POST"}),
  proxySettings:()=>request<ProxySettings>("/api/v1/settings/proxy"),
  saveProxySettings:(enabled:boolean,host:string,port:number)=>request<ProxySettings>("/api/v1/settings/proxy",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled,host,port})}),
};
