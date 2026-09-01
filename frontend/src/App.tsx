import { useEffect, useMemo, useState } from "react";
import { api, type ScanResult, type SecurityName, type SystemStatus } from "./api";
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
type ResultView="scores"|"roundBottom"|"cupHandle"|"classic";
type SortOrder="scoreDesc"|"scoreAsc"|"symbolAsc"|"symbolDesc";

function savedSortOrder():SortOrder{
  const saved=localStorage.getItem("scanner-stock-sort");
  return saved==="scoreAsc"||saved==="symbolAsc"||saved==="symbolDesc"?saved:"scoreDesc";
}

function savedFavorites():string[]{
  try{
    const value=JSON.parse(localStorage.getItem("scanner-favorite-symbols")??"[]");
    return Array.isArray(value)?value.filter((symbol):symbol is string=>typeof symbol==="string"):[];
  }catch{return []}
}

export default function App() {
  const {t,i18n}=useTranslation();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [results, setResults] = useState<ScanResult[]>([]);
  const [securityNames, setSecurityNames] = useState<Record<string,SecurityName>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("chart");
  const [query, setQuery] = useState("");
  const [sortOrder,setSortOrder]=useState<SortOrder>(savedSortOrder);
  const [resultView,setResultView]=useState<ResultView>("scores");
  const [favorites,setFavorites]=useState<string[]>(savedFavorites);
  const [favoriteOnly,setFavoriteOnly]=useState(false);
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
    api.securityNames().then(response=>setSecurityNames(response.names)).catch(()=>setSecurityNames({}));
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
          : { symbol, total_score: 0, triggered_factors: [], data_status: "等待扫描", f7_pattern:null, classic_patterns:[], breakout_patterns:[], hasResult: false };
      })
      .filter((row) => {
        const search=query.trim().toLocaleUpperCase();
        const names=securityNames[row.symbol];
        return row.symbol.includes(search)||[names?.name_cn,names?.name_hk,names?.name_en].some(name=>name?.toLocaleUpperCase().includes(search));
      })
      .filter((row)=>!favoriteOnly||favorites.includes(row.symbol))
      .filter((row)=>resultView!=="roundBottom"||row.hasResult&&row.breakout_patterns.some(pattern=>pattern.buy_candidate))
      .filter((row)=>resultView!=="cupHandle"||row.hasResult&&Boolean(row.f7_pattern))
      .filter((row)=>resultView!=="classic"||row.hasResult&&row.classic_patterns.length>0)
      .sort((a,b)=>{
        if(sortOrder==="scoreAsc")return a.total_score-b.total_score||a.symbol.localeCompare(b.symbol);
        if(sortOrder==="symbolAsc")return a.symbol.localeCompare(b.symbol);
        if(sortOrder==="symbolDesc")return b.symbol.localeCompare(a.symbol);
        return b.total_score-a.total_score||a.symbol.localeCompare(b.symbol);
      });
  }, [favoriteOnly, favorites, query, resultView, results, securityNames, sortOrder, symbols]);

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

  function toggleFavorite(symbol:string){
    setFavorites(current=>{
      const next=current.includes(symbol)?current.filter(item=>item!==symbol):[...current,symbol];
      localStorage.setItem("scanner-favorite-symbols",JSON.stringify(next));
      return next;
    });
  }

  const favoriteCount=favorites.filter(symbol=>symbols.includes(symbol)).length;

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
        <div className="scan-note topbar-scan" role="status" aria-live="polite">
          {"raw" in message?message.raw:t(message.key,message.values)}
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
            <div className="watchlist-counts">
              <button type="button" className={`favorite-count ${favoriteOnly?"active":""}`} onClick={()=>setFavoriteOnly(true)} title={t("showFavorites")} aria-label={t("showFavorites")}>★ {favoriteCount}</button>
              <button type="button" className={`count-badge ${!favoriteOnly?"active":""}`} onClick={()=>setFavoriteOnly(false)} title={t("showAllStocks")} aria-label={t("showAllStocks")}>{symbols.length}</button>
            </div>
          </div>

          <div className="search-box">
            <span>⌕</span>
            <input aria-label={t("search")} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("search")} />
            {query&&<button type="button" className="search-clear" aria-label={t("clearSearch")} title={t("clearSearch")} onClick={()=>setQuery("")}>×</button>}
          </div>

          <div className="filters">
            <label>
              {t("sortBy")}
              <select value={sortOrder} onChange={(event)=>{const next=event.target.value as SortOrder;setSortOrder(next);localStorage.setItem("scanner-stock-sort",next)}}>
                <option value="scoreDesc">{t("sortScoreDesc")}</option>
                <option value="scoreAsc">{t("sortScoreAsc")}</option>
                <option value="symbolAsc">{t("sortSymbolAsc")}</option>
                <option value="symbolDesc">{t("sortSymbolDesc")}</option>
              </select>
            </label>
            <button aria-pressed={resultView==="roundBottom"} className={`quiet-button ${resultView==="roundBottom"?"active round-bottom-active":""}`} onClick={()=>setResultView("roundBottom")}>{t("roundBottom")}</button>
            <button aria-pressed={resultView==="cupHandle"} className={`quiet-button ${resultView==="cupHandle"?"active":""}`} onClick={()=>setResultView("cupHandle")}>{t("cupHandle")}</button>
            <button aria-pressed={resultView==="classic"} className={`quiet-button ${resultView==="classic"?"active classic-active":""}`} onClick={()=>setResultView("classic")}>{t("classicPatterns")}</button>
            <button aria-pressed={resultView==="scores"} className={`quiet-button ${resultView==="scores"?"active":""}`} onClick={()=>setResultView("scores")}>{t("multiPeriod")}</button>
          </div>

          <div className="stock-list">
            {rows.map((row) => (
              <div
                className={`stock-row ${selected === row.symbol ? "selected" : ""}`}
                key={row.symbol}
                onClick={() => setSelected(row.symbol)}
                onKeyDown={(event)=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();setSelected(row.symbol)}}}
                role="button"
                tabIndex={0}
              >
                <span className="stock-main">
                  <span className="stock-symbol-line"><strong>{displaySymbol(row.symbol)}</strong>{securityNames[row.symbol]&&<span className="stock-name">{i18n.language.startsWith("zh")?(securityNames[row.symbol].name_cn||securityNames[row.symbol].name_hk||securityNames[row.symbol].name_en):securityNames[row.symbol].name_en}</span>}<button type="button" className={`stock-favorite ${favorites.includes(row.symbol)?"active":""}`} onClick={(event)=>{event.stopPropagation();toggleFavorite(row.symbol)}} aria-label={favorites.includes(row.symbol)?t("removeFavorite"):t("addFavorite")} title={favorites.includes(row.symbol)?t("removeFavorite"):t("addFavorite")}>★</button></span>
                  <small>{row.triggered_factors.length ? row.triggered_factors.join(" · ") : t("waitingFactors")}</small>
                </span>
                <span className="stock-score">{row.hasResult ? row.total_score.toFixed(0) : "—"}</span>
                <span className={`data-state ${row.data_status === "正常" ? "good" : ""}`}>{row.data_status === "正常"?t("normal"):row.data_status === "等待扫描"?t("waitingScan"):row.data_status}</span>
                {row.f7_pattern&&<span className={`f7-badge ${row.f7_pattern.stage}`}><b>F7</b><span>{t(`f7.${row.f7_pattern.stage}`)}</span><em>{t(row.f7_pattern.timeframe==="4hour"?"fourHour":row.f7_pattern.timeframe)} · {row.f7_pattern.confidence.toFixed(0)}%</em></span>}
                {row.breakout_patterns.slice(0,1).map(pattern=><span className={`breakout-badge ${pattern.stage}`} key={`${pattern.timeframe}-${pattern.base_type}`}><b>{pattern.buy_candidate?"买点":"观察"}</b><span>{{cup_handle:"杯柄",double_bottom:"W双底",flat_base:"平底"}[pattern.base_type]}</span><em>{t(pattern.timeframe==="4hour"?"fourHour":pattern.timeframe)} · 枢轴 {pattern.pivot_price.toFixed(2)}</em></span>)}
                {resultView==="classic"&&row.classic_patterns.slice(0,3).map(pattern=><span className="classic-badge" key={`${pattern.timeframe}-${pattern.pattern_id}`}><b>{pattern.pattern_id}</b><span>{t(`pattern.${pattern.pattern_name}`,{defaultValue:pattern.pattern_name})}</span><em>{t(pattern.timeframe==="4hour"?"fourHour":pattern.timeframe)}</em></span>)}
              </div>
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
