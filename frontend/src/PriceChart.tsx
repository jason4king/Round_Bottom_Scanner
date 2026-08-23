import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  createTextWatermark,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { api, type BarsResponse } from "./api";
import { displaySymbol } from "./display";
import { useTranslation } from "react-i18next";

type Timeframe = "weekly" | "daily" | "4hour";
const periods: Timeframe[] = ["weekly","daily","4hour"];

export default function PriceChart({ symbol }: { symbol: string }) {
  const {t}=useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const macdHost = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const macdChart = useRef<IChartApi | null>(null);
  const [timeframe, setTimeframe] = useState<Timeframe>("daily");
  const [payload, setPayload] = useState<BarsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null);
    api.bars(symbol, timeframe)
      .then((data) => { if (!cancelled) setPayload(data); })
      .catch((reason: Error) => { if (!cancelled) { setPayload(null); setError(reason.message); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [symbol, timeframe]);

  useEffect(() => {
    if (!host.current || !macdHost.current || !payload?.bars.length) return;
    chart.current?.remove();
    macdChart.current?.remove();
    const instance = createChart(host.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#091522" }, textColor: "#8298ad", fontSize: 11 },
      grid: { vertLines: { color: "#132638" }, horzLines: { color: "#132638" } },
      rightPriceScale: { borderColor: "#263b4e" },
      timeScale: { borderColor: "#263b4e", timeVisible: timeframe === "4hour" },
      crosshair: { vertLine: { color: "#536f83" }, horzLine: { color: "#536f83" } },
    });
    chart.current = instance;
    createTextWatermark(instance.panes()[0], {
      horzAlign: "center",
      vertAlign: "center",
      lines: [{
        text: displaySymbol(symbol),
        color: "rgba(113, 151, 166, 0.10)",
        fontSize: 58,
        fontStyle: "bold",
        fontFamily: "Inter, Microsoft YaHei, sans-serif",
      }],
    });
    const macdInstance = createChart(macdHost.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#091522" }, textColor: "#8298ad", fontSize: 10 },
      grid: { vertLines: { color: "#132638" }, horzLines: { color: "#132638" } },
      rightPriceScale: { borderColor: "#263b4e", scaleMargins: { top: .12, bottom: .12 } },
      timeScale: { borderColor: "#263b4e", timeVisible: timeframe === "4hour" },
    });
    macdChart.current = macdInstance;
    const candles = instance.addSeries(CandlestickSeries, { upColor: "#4fd0ad", downColor: "#ef6b73", borderVisible: false, wickUpColor: "#4fd0ad", wickDownColor: "#ef6b73", priceLineColor: "#f0a23a" });
    const volume = instance.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "volume", lastValueVisible: false, priceLineVisible: false });
    instance.priceScale("volume").applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
    const ema12 = instance.addSeries(LineSeries, { color: "#f5f0df", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    const ema144 = instance.addSeries(LineSeries, { color: "#d9b84f", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const ema169 = instance.addSeries(LineSeries, { color: "#a88d38", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const ema576 = instance.addSeries(LineSeries, { color: "#4fc28f", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const ema676 = instance.addSeries(LineSeries, { color: "#278968", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const trendSupport = instance.addSeries(LineSeries, { color: "#33c58f", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, title: "支撑" });
    const trendResistance = instance.addSeries(LineSeries, { color: "#e16b73", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, title: "压力" });
    const points = payload.bars.map((bar) => ({ ...bar, time: Math.floor(new Date(bar.timestamp).getTime() / 1000) as UTCTimestamp }));
    candles.setData(points.map((p) => ({ time:p.time, open:p.open, high:p.high, low:p.low, close:p.close })));
    volume.setData(points.map((p) => ({ time:p.time, value:p.volume, color:p.close>=p.open ? "#347f7088" : "#8d414788" })));
    ema12.setData(points.map((p) => ({ time:p.time, value:p.ema12 })));
    ema144.setData(points.map((p) => ({ time:p.time, value:p.ema144 })));
    ema169.setData(points.map((p) => ({ time:p.time, value:p.ema169 })));
    ema576.setData(points.map((p) => ({ time:p.time, value:p.ema576 })));
    ema676.setData(points.map((p) => ({ time:p.time, value:p.ema676 })));
    trendSupport.setData(points.filter((p)=>p.trend_support!==null).map((p)=>({time:p.time,value:p.trend_support!})));
    trendResistance.setData(points.filter((p)=>p.trend_resistance!==null).map((p)=>({time:p.time,value:p.trend_resistance!})));
    const macdHistogram = macdInstance.addSeries(HistogramSeries, { priceLineVisible:false, lastValueVisible:false, base:0 });
    const dif = macdInstance.addSeries(LineSeries, { color:"#d8e4ef", lineWidth:1, priceLineVisible:false, lastValueVisible:false });
    const dea = macdInstance.addSeries(LineSeries, { color:"#d6ad42", lineWidth:1, priceLineVisible:false, lastValueVisible:false });
    macdHistogram.setData(points.map((p)=>({time:p.time,value:p.macd_hist,color:p.macd_hist>=0?"#3fb895":"#d85f69"})));
    dif.setData(points.map((p)=>({time:p.time,value:p.macd_dif})));
    dea.setData(points.map((p)=>({time:p.time,value:p.macd_dea})));
    const initialBars: Record<Timeframe, number> = { weekly: 120, daily: 100, "4hour": 140 };
    const visibleCount = Math.min(initialBars[timeframe], points.length);
    const initialRange = { from: points.length - visibleCount, to: points.length + 4 };
    instance.timeScale().setVisibleLogicalRange(initialRange);
    macdInstance.timeScale().setVisibleLogicalRange(initialRange);
    let syncing=false;
    instance.timeScale().subscribeVisibleLogicalRangeChange((range)=>{if(!range||syncing)return;syncing=true;macdInstance.timeScale().setVisibleLogicalRange(range);syncing=false});
    macdInstance.timeScale().subscribeVisibleLogicalRangeChange((range)=>{if(!range||syncing)return;syncing=true;instance.timeScale().setVisibleLogicalRange(range);syncing=false});
    return () => { instance.remove(); macdInstance.remove(); if (chart.current === instance) chart.current = null; if(macdChart.current===macdInstance)macdChart.current=null; };
  }, [payload, timeframe]);

  return <div className="chart-view">
    <div className="chart-toolbar">
      <div className="period-switch">{periods.map((period) => <button key={period} className={timeframe===period ? "active" : ""} onClick={() => setTimeframe(period)}>{t(period==="4hour"?"fourHour":period)}</button>)}</div>
      <div className="chart-contract"><span>{t("forward")}</span><span>{t("allSessions")}</span><span>{t("localCache")}</span>{payload && <span>{payload.count} {t("bars")}</span>}</div>
    </div>
    <div className="chart-legend"><span className="ema12">EMA12</span><span className="yellow">EMA144 / 169</span><span className="green">EMA576 / 676</span><span className="support">{t("trendSupport")}</span><span className="resistance">{t("trendResistance")}</span><span className="macd">{t("macdNote")}</span></div>
    {loading && <div className="chart-message">{t("chartLoading")}</div>}
    {error && <div className="chart-message error"><strong>{t("chartError")}</strong><span>{error}</span><small>{t("syncFirst")}</small></div>}
    <div className={`chart-stack ${loading || error ? "hidden" : ""}`}><div ref={host} className="chart-host"/><div className="macd-label">MACD <span>DIF</span><i>DEA</i></div><div ref={macdHost} className="macd-host"/></div>
  </div>;
}
