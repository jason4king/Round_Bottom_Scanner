import { useEffect, useState } from "react";
import { api, type Diagnostics } from "./api";
import { displaySymbol } from "./display";
import { useTranslation } from "react-i18next";

const timeframes=["weekly","daily","4hour"] as const;
export default function AnalysisSidebar({symbol}:{symbol:string|null}){
  const {t,i18n}=useTranslation();
  const label=(tf:string)=>t(tf==="4hour"?"fourHour":tf);
  const [data,setData]=useState<Diagnostics|null>(null);const [error,setError]=useState<string|null>(null);
  useEffect(()=>{if(!symbol){setData(null);return}setData(null);setError(null);api.diagnostics(symbol).then(setData).catch((e:Error)=>setError(e.message))},[symbol]);
  return <aside className="analysis-panel">
    <header className="analysis-heading"><div><p className="eyebrow">ANALYSIS</p><h2>{t("analysisSummary")}</h2></div><strong className="analysis-symbol">{displaySymbol(symbol)}</strong></header>
    {!symbol?<div className="analysis-empty">{t("selectStock")}</div>:error?<div className="analysis-empty error">{error}</div>:!data?<div className="analysis-empty">{t("loading")}</div>:<>
      <section className="analysis-score"><div><span>{t("score")}</span><strong>{data.scoring.total_score}</strong></div><div><span>{t("triggeredFactors")}</span><b>{data.scoring.triggered_factors.join(" · ")||t("none")}</b></div><div><span>{t("coverage")}</span><b>× {data.scoring.coverage_multiplier}</b></div></section>
      <BottomDiagnosis data={data}/>
      <section className="analysis-section"><h3>{t("multiFactors")}</h3><div className="factor-matrix"><header><span>{t("factor")}</span>{timeframes.map(tf=><span key={tf}>{label(tf)}</span>)}</header>{[1,2,3,4,5,6].map(n=>{const id=`F${n}`;return <div key={id}><b>{id}</b>{timeframes.map(tf=>{const f=data.factors.find(x=>x.factor_id===id&&x.timeframe===tf);return <span className={f?.triggered?"on":"off"} title={f?.reason??t("triggered")} key={tf}>{f?.triggered?"●":"—"}</span>})}</div>})}</div></section>
      <section className="analysis-section"><h3>{t("periodSummary")}</h3>{timeframes.map(tf=>{const factors=data.factors.filter(x=>x.timeframe===tf);const active=factors.filter(x=>x.triggered);return <div className="period-summary" key={tf}><div><b>{label(tf)}</b><span>{active.length}/6 {t("triggered")}</span></div><p>{active.map(x=>`${x.factor_id}${x.tier?`-${x.tier}`:""}`).join(" · ")||t("noFactors")}</p><small>{data.bar_timestamps[tf]?new Date(data.bar_timestamps[tf]).toLocaleString(i18n.language,{hour12:false}):"—"}</small></div>})}</section>
      <section className="analysis-note"><b>{t("observation")}</b><span>{t("observationText")}</span></section>
    </>}
  </aside>
}

function BottomDiagnosis({data}:{data:Diagnostics}){
  const {t}=useTranslation();
  const timeframeLabel=(tf:string)=>t(tf==="4hour"?"fourHour":tf);
  const evidence:string[]=[];let confirmed=false,forming=false,breakout=false,extended=false,uptrend=false;
  for(const tf of timeframes){
    const periodFactors=data.factors.filter(f=>f.timeframe===tf);
    const active=new Set(periodFactors.filter(f=>f.triggered).map(f=>f.factor_id));
    if(active.has("F3")){
      evidence.push(t("evidenceRoundBottom",{timeframe:timeframeLabel(tf)}));
      const f3=periodFactors.find(f=>f.factor_id==="F3");
      const rise=Number(f3?.details?.rise_from_vertex_pct);
      const age=Number(f3?.details?.bars_since_vertex);
      const confirmations=["F2","F4","F5"].filter(id=>active.has(id));
      if(Number.isFinite(rise)&&rise>=35){extended=true;evidence.push(t("evidenceRiseFromBottom",{timeframe:timeframeLabel(tf),value:rise.toFixed(1)}))}
      else if((Number.isFinite(rise)&&rise>=15)||(Number.isFinite(age)&&age>=24)){breakout=true;evidence.push(t("evidenceBreakoutStage",{timeframe:timeframeLabel(tf)}))}
      else if(confirmations.length){confirmed=true;evidence.push(t("evidenceConfirmation",{timeframe:timeframeLabel(tf),factors:confirmations.join("/")}))}else forming=true;
    }
    if(active.has("F1")&&active.has("F2")){
      const f2=periodFactors.find(f=>f.factor_id==="F2");
      const tunnelDistance=Number(f2?.details?.distance)*100;
      if(Number.isFinite(tunnelDistance)&&tunnelDistance>=20){extended=true;evidence.push(t("evidenceFarFromTunnel",{timeframe:timeframeLabel(tf),value:tunnelDistance.toFixed(1)}))}
      else if(Number.isFinite(tunnelDistance)&&tunnelDistance>=8){breakout=true;evidence.push(t("evidenceLeftBottomTunnel",{timeframe:timeframeLabel(tf)}))}
      else {forming=true;evidence.push(t("evidenceEarlyCombination",{timeframe:timeframeLabel(tf)}))}
    }
    const regime=periodFactors.find(f=>f.factor_id==="F5");
    const priceDistance=Number(regime?.details?.price_above_long_tunnel_pct);
    const emaDistance=Number(regime?.details?.ema12_above_long_tunnel_pct);
    if(Number.isFinite(priceDistance)&&Number.isFinite(emaDistance)&&priceDistance>=15&&emaDistance>=5){
      uptrend=true;
      evidence.push(t("evidenceAboveLongTunnel",{timeframe:timeframeLabel(tf),value:priceDistance.toFixed(1)}));
    }
  }
  const f3=data.factors.filter(f=>f.factor_id==="F3");
  const insufficient=f3.length>0&&f3.every(f=>f.reason==="insufficient_history");
  const state=extended?t("bottomExtended"):uptrend?t("bottomUptrend"):breakout?t("bottomBreakout"):confirmed?t("bottomConfirmed"):forming?t("bottomForming"):insufficient?t("insufficient"):t("notFormed");
  const tone=extended||uptrend||breakout?"advanced":confirmed?"confirmed":forming?"forming":insufficient?"insufficient":"none";
  return <section className={`bottom-diagnosis ${tone}`}><header><span>{t("bottomDiagnosis")}</span><strong>{state}</strong></header><p>{evidence.length?evidence.join(t("evidenceSeparator")):insufficient?t("insufficientBottom"):t("noBottomEvidence")}</p><small>{t("diagnosisNote")}</small></section>
}
