import { useEffect, useState } from "react";
import { api, type CacheResponse, type Diagnostics, type HistoryResponse } from "./api";
import { useTranslation } from "react-i18next";

const tfLabel:Record<string,string>={weekly:"周线",daily:"日线","4hour":"4 小时"};
function formatDate(value:string|null|undefined){return value?new Date(value).toLocaleString("zh-CN",{hour12:false}):"—"}
function Loading({error}:{error:string|null}){const {t}=useTranslation();return <div className={`detail-message ${error?"error":""}`}>{error??t("loading")}</div>}

export function DiagnosticsTab({symbol}:{symbol:string}){
  const {t}=useTranslation();
  const [data,setData]=useState<Diagnostics|null>(null);const [error,setError]=useState<string|null>(null);
  useEffect(()=>{setData(null);setError(null);api.diagnostics(symbol).then(setData).catch((e:Error)=>setError(e.message))},[symbol]);
  if(!data)return <Loading error={error}/>;
  return <div className="diagnostics-view">{["weekly","daily","4hour"].map(tf=><section className="factor-group" key={tf}><header><strong>{t(tf==="4hour"?"fourHour":tf)}</strong><small>{formatDate(data.bar_timestamps[tf])}</small></header><div className="factor-grid">{data.factors.filter(f=>f.timeframe===tf).map(f=><article className={`factor-card ${f.triggered?"triggered":""}`} key={f.factor_id}><div className="factor-title"><b>{f.factor_id}</b><span>{f.factor_id.startsWith("P")?t(`patternFamily.${f.factor_id}`,{defaultValue:f.signal_name}):f.signal_name}</span><em>{f.triggered?`${t("triggered")}${f.tier?` · ${f.tier}`:""}`:t("notTriggered")}</em></div><dl>{Object.entries(f.details).filter(([,v])=>v!==null).slice(0,5).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{k==="pattern_name"?t(`pattern.${String(v)}`,{defaultValue:String(v)}):typeof v==="number"?Number(v).toFixed(4):String(v)}</dd></div>)}</dl></article>)}</div></section>)}</div>
}

export function ScoringTab({symbol}:{symbol:string}){
  const {t}=useTranslation();
  const [data,setData]=useState<Diagnostics|null>(null);const [error,setError]=useState<string|null>(null);
  useEffect(()=>{setData(null);setError(null);api.diagnostics(symbol).then(setData).catch((e:Error)=>setError(e.message))},[symbol]);
  if(!data)return <Loading error={error}/>;const s=data.scoring;
  const map=new Map<string,{base:number;multiplier:number;score:number}>();data.factors.filter(f=>f.triggered&&/^F[1-6]$/.test(f.factor_id)).forEach(f=>{if(!map.has(f.factor_id))map.set(f.factor_id,{base:f.base_score,multiplier:f.multiplier,score:f.contribution})});const contributions=[...map.entries()];
  return <div className="scoring-view"><section className="score-formula"><div><small>{t("factorContribution")}</small><strong>{s.base_total}</strong></div><i>+</i><div><small>{t("resonance")}</small><strong>{s.confluence_total}</strong></div><i>=</i><div><small>{t("beforeMultiplier")}</small><strong>{s.pre_multiplier_score}</strong></div><i>×</i><div><small>{t("fullCoverage")}</small><strong>{s.coverage_multiplier}</strong></div><i>=</i><div className="final"><small>{t("finalScore")}</small><strong>{s.total_score}</strong></div></section><section className="score-table"><header><span>{t("factor")}</span><span>{t("baseScore")}</span><span>{t("crossMultiplier")}</span><span>{t("contribution")}</span></header>{contributions.map(([id,x])=><div key={id}><b>{id}</b><span>{x.base}</span><span>× {x.multiplier}</span><strong>{x.score}</strong></div>)}</section><section className="timeframe-scores"><h3>{t("resonance")}</h3>{Object.entries(s.timeframe_scores).map(([tf,score])=><div key={tf}><span>{t(tf==="4hour"?"fourHour":tf)}</span><strong>{score}</strong></div>)}</section></div>
}

export function HistoryTab({symbol}:{symbol:string}){
  const {t}=useTranslation();
  const [data,setData]=useState<HistoryResponse|null>(null);const [error,setError]=useState<string|null>(null);
  useEffect(()=>{setData(null);setError(null);api.history(symbol).then(setData).catch((e:Error)=>setError(e.message))},[symbol]);
  if(!data)return <Loading error={error}/>;if(!data.history.length)return <Loading error={t("noHistory")}/>;
  const max=Math.max(...data.history.map(x=>x.total_score),1);const points=data.history.map((x,i)=>`${data.history.length===1?50:(i/(data.history.length-1))*100},${92-(x.total_score/max)*78}`).join(" ");
  return <div className="history-view"><div className="history-chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points={points}/></svg><span>{t("highest")} {max}</span></div><div className="history-list">{[...data.history].reverse().map(x=><div key={x.run_id}><span>{formatDate(x.completed_at)}</span><b>{x.total_score}</b><small>{x.triggered_factors.join(" · ")||t("noFactors")}</small></div>)}</div></div>
}

export function DataTab({symbol}:{symbol:string}){
  const {t}=useTranslation();
  const [data,setData]=useState<CacheResponse|null>(null);const [error,setError]=useState<string|null>(null);
  useEffect(()=>{setData(null);setError(null);api.cache(symbol).then(setData).catch((e:Error)=>setError(e.message))},[symbol]);
  if(!data)return <Loading error={error}/>;
  return <div className="data-view"><div className="data-contract"><span>{t("authoritative")}</span><span>{t("forward")}</span><span>{t("allSessions")}</span><span>Parquet {t("localCache")}</span></div>{data.items.length?<div className="cache-table"><header><span>{t("periodSummary")}</span><span>{t("barCount")}</span><span>{t("earliest")}</span><span>{t("latest")}</span><span>{t("lastSync")}</span><span>{t("status")}</span></header>{data.items.map(x=><div key={x.timeframe}><b>{t(x.timeframe==="4hour"?"fourHour":x.timeframe)}</b><span>{x.row_count}</span><span>{formatDate(x.earliest_timestamp)}</span><span>{formatDate(x.latest_timestamp)}</span><span>{formatDate(x.updated_at)}</span><em>{x.sync_status}</em></div>)}</div>:<Loading error={t("noCache")}/>}</div>
}
