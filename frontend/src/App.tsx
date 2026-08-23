import { useEffect, useMemo, useState } from "react";
import { api, type ScanResult, type SystemStatus } from "./api";
import PriceChart from "./PriceChart";
import { DataTab, DiagnosticsTab, HistoryTab, ScoringTab } from "./DetailTabs";
import AnalysisSidebar from "./AnalysisSidebar";
import { displaySymbol } from "./display";
import WatchlistTab from "./WatchlistTab";
import { useTranslation } from "react-i18next";
import { setLanguage } from "./i18n";
import SystemSettingsTab from "./SystemSettingsTab";

const tabs = ["chart", "diagnostics", "scoring", "history", "data", "watchlist", "settings"] as const;
type Tab = (typeof tabs)[number];

type StockRow = ScanResult & { hasResult: boolean };
type UiMessage={key:string;values?:Record<string,number|string>}|{raw:string};

export default function App() {
  const {t,i18n}=useTranslation();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [results, setResults] = useState<ScanResult[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("chart");
  const [query, setQuery] = useState("");
  const [minimumScore, setMinimumScore] = useState(0);
  const [message, setMessage] = useState<UiMessage>({key:"connecting"});
  const [busy, setBusy] = useState(false);
  const [activeRun, setActiveRun] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.status(), api.watchlist(), api.latestResults()])
      .then(([systemStatus, watchlist, latest]) => {
        setStatus(systemStatus);
        setSymbols(watchlist.symbols);
        setResults(latest);
        setSelected(latest[0]?.symbol ?? watchlist.symbols[0] ?? null);
        setMessage({key:latest.length ? "loadedLatest" : "noOfficialResults"});
      })
      .catch((error: Error) => setMessage({raw:error.message}));
  }, []);

  useEffect(() => {
    if (!activeRun) return;
    const timer = window.setInterval(async () => {
      try {
        const run = await api.scanStatus(activeRun);
        setMessage({key:"scanProgress",values:{succeeded:run.symbols_succeeded,total:run.symbols_total,failed:run.symbols_failed}});
        if (["completed", "completed_with_errors", "failed"].includes(run.status)) {
          window.clearInterval(timer);
          setActiveRun(null);
          setBusy(false);
          const latest = await api.latestResults();
          setResults(latest);
          const firstError=run.errors[0];
          setMessage(firstError?.message ? {raw:`${run.status === "failed" ? "扫描中断" : "扫描完成但有错误"}：${firstError.symbol?`${displaySymbol(firstError.symbol)} · `:""}${firstError.message}`} : run.status === "completed" ? {key:"scanComplete",values:{succeeded:run.symbols_succeeded}} : {key:"scanEnded",values:{succeeded:run.symbols_succeeded,failed:run.symbols_failed}});
        }
      } catch (error) {
        window.clearInterval(timer);
        setActiveRun(null);
        setBusy(false);
        setMessage({raw:error instanceof Error ? error.message : "读取扫描进度失败"});
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activeRun]);

  const rows = useMemo<StockRow[]>(() => {
    const bySymbol = new Map(results.map((result) => [result.symbol, result]));
    return symbols
      .map((symbol) => {
        const result = bySymbol.get(symbol);
        return result
          ? { ...result, hasResult: true }
          : { symbol, total_score: 0, triggered_factors: [], data_status: "等待扫描", hasResult: false };
      })
      .filter((row) => row.symbol.includes(query.trim().toUpperCase()))
      .filter((row) => row.total_score >= minimumScore)
      .sort((a, b) => b.total_score - a.total_score || a.symbol.localeCompare(b.symbol));
  }, [minimumScore, query, results, symbols]);

  const selectedRow = rows.find((row) => row.symbol === selected) ?? null;

  async function startScan() {
    setBusy(true);
    try {
      const response = await api.createScan();
      setMessage({key:"scanStarted",values:{id:response.run_id.slice(0,8)}});
      setActiveRun(response.run_id);
    } catch (error) {
      setMessage({raw:error instanceof Error ? error.message : "创建扫描任务失败"});
      setBusy(false);
    }
  }

  function watchlistSaved(updated:string[]){
    setSymbols(updated);
    setStatus(current=>current?{...current,watchlist_count:updated.length,watchlist_errors:[]}:current);
    if(!selected||!updated.includes(selected))setSelected(updated[0]??null);
    setMessage({key:"watchlistSaved",values:{count:updated.length}});
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">◒</span>
          <div>
            <strong>{t("appTitle")}</strong>
            <small>{t("appSubtitle")}</small>
          </div>
        </div>
        <div className="system-state">
          <StatusDot ok={Boolean(status?.longport_configured)} />
          <span>{status?.longport_configured ? (status.longport_auth_mode === "oauth" ? "LongPort OAuth" : t("configured")) : t("notConfigured")}</span>
          <span className="divider" />
          <span>{status?.watchlist_count ?? "—"} {t("securities")}</span>
        </div>
        <select className="language-switch" aria-label={t("language")} value={i18n.language.startsWith("en")?"en-US":"zh-CN"} onChange={e=>setLanguage(e.target.value as "zh-CN"|"en-US")}><option value="zh-CN">中文</option><option value="en-US">English</option></select>
        <button className="primary-action" disabled={busy} onClick={startScan}>
          {busy ? t("scanning") : t("scan")}
        </button>
      </header>

      <section className="workspace">
        <aside className="stock-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">WATCHLIST</p>
              <h1>{t("watchlistScore")}</h1>
            </div>
            <span className="count-badge">{rows.length}</span>
          </div>

          <label className="search-box">
            <span>⌕</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("search")} />
          </label>

          <div className="filters">
            <label>
              {t("minimum")}
              <select value={minimumScore} onChange={(event) => setMinimumScore(Number(event.target.value))}>
                <option value={0}>{t("unlimited")}</option>
                <option value={10}>10+</option>
                <option value={25}>25+</option>
                <option value={50}>50+</option>
              </select>
            </label>
            <button className="quiet-button">{t("roundBottom")}</button>
            <button className="quiet-button">{t("multiPeriod")}</button>
          </div>

          <div className="scan-note">{"raw" in message?message.raw:t(message.key,message.values)}</div>

          <div className="stock-list">
            {rows.map((row) => (
              <button
                className={`stock-row ${selected === row.symbol ? "selected" : ""}`}
                key={row.symbol}
                onClick={() => setSelected(row.symbol)}
              >
                <span className="stock-main">
                  <strong>{displaySymbol(row.symbol)}</strong>
                  <small>{row.triggered_factors.length ? row.triggered_factors.join(" · ") : t("waitingFactors")}</small>
                </span>
                <span className="stock-score">{row.hasResult ? row.total_score.toFixed(0) : "—"}</span>
                <span className={`data-state ${row.data_status === "正常" ? "good" : ""}`}>{row.data_status === "正常"?t("normal"):row.data_status === "等待扫描"?t("waitingScan"):row.data_status}</span>
              </button>
            ))}
          </div>
        </aside>

        <section className="detail-panel">
          <nav className="tabs" aria-label="股票详情">
            {tabs.map((tab) => (
              <button className={activeTab === tab ? "active" : ""} key={tab} onClick={() => setActiveTab(tab)}>
                {t(`tab.${tab}`)}
              </button>
            ))}
          </nav>

          <div className="tab-content">
            {activeTab === "settings" ? <SystemSettingsTab/> : activeTab === "watchlist" ? <WatchlistTab symbols={symbols} onSaved={watchlistSaved}/> :
              !selected ? <EmptyTab tab={activeTab} symbol={selected} hasResult={false}/> :
              activeTab === "chart" ? <PriceChart symbol={selected}/> :
              activeTab === "diagnostics" ? <DiagnosticsTab symbol={selected}/> :
              activeTab === "scoring" ? <ScoringTab symbol={selected}/> :
              activeTab === "history" ? <HistoryTab symbol={selected}/> :
              <DataTab symbol={selected}/>} 
          </div>
        </section>
        <AnalysisSidebar symbol={selected}/>
      </section>
    </main>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span className={`status-dot ${ok ? "ok" : "warn"}`} />;
}

function EmptyTab({ tab, symbol, hasResult }: { tab: Tab; symbol: string | null; hasResult: boolean }) {
  const {t}=useTranslation();
  const descriptions: Record<Tab, string> = {chart:"Weekly, daily and 4-hour candles with EMA channels and pattern markers.",diagnostics:"F1–F6 trigger values, thresholds and reasons.",scoring:"Complete score calculation across timeframes.",history:"Official scan score and factor history.",data:"Cache ranges, data contract and sync errors.",watchlist:"Add, remove and save watchlist symbols.",settings:"Authentication and application settings."};
  return (
    <div className="empty-state">
      <div className="orbital-mark"><span /></div>
      <p className="eyebrow">{t(`tab.${tab}`)}</p>
      <h3>{symbol ? `${displaySymbol(symbol)} · ${hasResult ? t("loading") : t("waitingFactors")}` : t("selectStock")}</h3>
      <p>{descriptions[tab]}</p>
      <div className="contract-tags">
        <span>{t("forward")}</span><span>{t("allSessions")}</span><span>{t("localCache")}</span>
      </div>
    </div>
  );
}
