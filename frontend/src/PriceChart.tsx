import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  createTextWatermark,
  type IChartApi,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";
import { api, type BarsResponse } from "./api";
import { displaySymbol } from "./display";
import { useTranslation } from "react-i18next";

type Timeframe = "weekly" | "daily" | "4hour";
type IndicatorPane = "rsi" | "macd";
const periods: Timeframe[] = ["weekly","daily","4hour"];

export default function PriceChart({ symbol }: { symbol: string }) {
  const {t}=useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const rsiHost = useRef<HTMLDivElement>(null);
  const macdHost = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const rsiChart = useRef<IChartApi | null>(null);
  const macdChart = useRef<IChartApi | null>(null);
  const [timeframe, setTimeframe] = useState<Timeframe>("daily");
  const [indicatorPane,setIndicatorPane]=useState<IndicatorPane>(()=>localStorage.getItem("chart-indicator-pane")==="macd"?"macd":"rsi");
  const [payload, setPayload] = useState<BarsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showTradeLevels,setShowTradeLevels]=useState(()=>localStorage.getItem("chart-trade-levels")!=="false");
  const [showVegas,setShowVegas]=useState(()=>localStorage.getItem("chart-vegas")!=="false");
  const [showTrendlines,setShowTrendlines]=useState(()=>localStorage.getItem("chart-trendlines")!=="false");

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
    if (!host.current || !rsiHost.current || !macdHost.current || !payload?.bars.length) return;
    chart.current?.remove();
    rsiChart.current?.remove();
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
    const rsiInstance = createChart(rsiHost.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#091522" }, textColor: "#8298ad", fontSize: 10 },
      grid: { vertLines: { color: "#132638" }, horzLines: { color: "#132638" } },
      rightPriceScale: { borderColor: "#263b4e", scaleMargins: { top: .08, bottom: .08 }, minimumWidth: 42 },
      timeScale: { borderColor: "#263b4e", timeVisible: timeframe === "4hour" },
    });
    rsiChart.current = rsiInstance;
    const macdInstance=createChart(macdHost.current,{
      autoSize:true,layout:{background:{type:ColorType.Solid,color:"#091522"},textColor:"#8298ad",fontSize:10},
      grid:{vertLines:{color:"#132638"},horzLines:{color:"#132638"}},rightPriceScale:{borderColor:"#263b4e",scaleMargins:{top:.08,bottom:.08},minimumWidth:42},timeScale:{borderColor:"#263b4e",timeVisible:timeframe==="4hour"},
    });
    macdChart.current=macdInstance;
    const candles = instance.addSeries(CandlestickSeries, { upColor: "#4fd0ad", downColor: "#ef6b73", borderVisible: false, wickUpColor: "#4fd0ad", wickDownColor: "#ef6b73", priceLineVisible: false });
    const volume = instance.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "volume", lastValueVisible: false, priceLineVisible: false });
    instance.priceScale("volume").applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
    const ema12 = instance.addSeries(LineSeries, { color: "#f5f0df", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, visible:showVegas });
    const ema144 = instance.addSeries(LineSeries, { color: "#d9b84f", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible:showVegas });
    const ema169 = instance.addSeries(LineSeries, { color: "#a88d38", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible:showVegas });
    const ema576 = instance.addSeries(LineSeries, { color: "#4fc28f", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible:showVegas });
    const ema676 = instance.addSeries(LineSeries, { color: "#278968", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible:showVegas });
    const trendSupport = instance.addSeries(LineSeries, { color: "#33c58f", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, visible:showTrendlines });
    const trendResistance = instance.addSeries(LineSeries, { color: "#e16b73", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, visible:showTrendlines });
    const rsiNeckline = instance.addSeries(LineSeries, { color: "#73a7d8", lineWidth: 2, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, visible:showTradeLevels });
    const rsiStop = instance.addSeries(LineSeries, { color: "#ef6b73", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, visible:showTradeLevels });
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
    rsiNeckline.setData(points.filter((p)=>p.rsi_neckline!==null).map((p)=>({time:p.time,value:p.rsi_neckline!})));
    rsiStop.setData(points.filter((p)=>p.rsi_stop_level!==null).map((p)=>({time:p.time,value:p.rsi_stop_level!})));
    if(payload.base_breakout){
      candles.createPriceLine({price:payload.base_breakout.pivot_price,color:payload.base_breakout.buy_candidate?"#62e0c1":"#f2b94b",lineWidth:2,lineStyle:2,axisLabelVisible:true,title:t("basePivot")});
    }
    const buyLookback:Record<Timeframe,number>={weekly:52,daily:100,"4hour":120};
    const buyStart=Math.max(0,points.length-buyLookback[timeframe]);
    const nearDisplayedBullishBlock=(low:number,time:UTCTimestamp,maxDistance=.02)=>payload.market_structure.order_blocks.some((block)=>{
      if(block.bias!=="bullish")return false;
      const confirmedAt=Math.floor(new Date(block.confirmed_at_timestamp).getTime()/1000);
      if(Number(time)<confirmedAt)return false;
      const distance=Math.max(low/block.top-1,0);
      return low>=block.bottom*.98&&distance<=maxDistance;
    });
    const mainBuyCandidates=points.filter((p,index)=>{
      if(index<buyStart)return false;
      if(p.rsi_breakout_buy||p.rsi_v_bottom_buy)return true;
      if(!p.rsi_enhanced_buy||p.bullish_order_block_distance_pct===null||p.bullish_order_block_distance_pct>5)return false;
      const candleGain=p.open>0?p.close/p.open-1:Number.POSITIVE_INFINITY;
      if(candleGain>.04)return false;
      const nearVisibleBullishBlock=nearDisplayedBullishBlock(p.low,p.time,.05);
      if(!nearVisibleBullishBlock)return false;
      const previous=index>0?points[index-1]:undefined;
      const trendReclaimed=p.close>=p.ema12&&previous!==undefined&&p.ema12>previous.ema12;
      if(!trendReclaimed&&!p.rsi_bullish_divergence)return false;
      const longTrendReclaimed=p.ema12>p.ema144&&p.ema12>p.ema169;
      if(!longTrendReclaimed&&!p.rsi_bullish_divergence)return false;
      return points.slice(Math.max(0,index-4),index+1).some((recent)=>recent.bullish_order_block_distance_pct!==null&&recent.bullish_order_block_distance_pct<=1);
    });
    const trendStartTimes=new Set<number>();
    points.forEach((p,index)=>{
      if(index<buyStart||index===0)return;
      const previous=points[index-1];
      const previousLongTop=Math.max(previous.ema144,previous.ema169);
      const currentLongTop=Math.max(p.ema144,p.ema169);
      const crossedLongTrend=previous.ema12<=previousLongTop&&p.ema12>currentLongTop;
      const priceConfirmed=p.close>currentLongTop&&p.close>p.ema12;
      const longTrendExtension=currentLongTop>0?p.close/currentLongTop-1:Number.POSITIVE_INFINITY;
      const candleGain=p.open>0?p.close/p.open-1:Number.POSITIVE_INFINITY;
      const recentBars=points.slice(Math.max(0,index-11),index+1);
      const recentOrderBlockSupport=recentBars.some((recent)=>nearDisplayedBullishBlock(recent.low,recent.time,.02));
      const recentLongTunnelSupport=recentBars.some((recent)=>{
        const tunnelTop=Math.max(recent.ema144,recent.ema169);
        const tunnelBottom=Math.min(recent.ema144,recent.ema169);
        return recent.low<=tunnelTop*1.02&&recent.low>=tunnelBottom*.95;
      });
      const recentSupport=recentOrderBlockSupport||recentLongTunnelSupport;
      if(crossedLongTrend&&priceConfirmed&&longTrendExtension<=.05&&p.ema12>previous.ema12&&p.rsi!==null&&p.rsi>=40&&p.rsi<=70&&candleGain<=.04&&recentSupport)trendStartTimes.add(Number(p.time));
    });
    const trendPullbackTimes=new Set<number>();
    points.forEach((p,index)=>{
      if(index<buyStart||index===0)return;
      const previous=points[index-1];
      // “趋势回踩”先寻找最近四根内的支撑锚点：RSI 接近 30，且价格进入
      // 当时已经存在的有效多头订单块。随后 K 线回到 EMA144 附近并收盘站稳，
      // 才在确认 K 线上画标记，不提前画回订单块触碰日。
      const supportWindow=points.slice(Math.max(buyStart,index-3),index+1);
      const supportAnchors=supportWindow.filter((recent)=>{
        const rsiNear30=recent.rsi!==null&&recent.rsi>=25&&recent.rsi<=35;
        const nearBullishOrderBlock=recent.bullish_order_block_distance_pct!==null&&recent.bullish_order_block_distance_pct<=5;
        return rsiNear30&&nearBullishOrderBlock;
      });
      const supportAnchor=supportAnchors.reduce<typeof supportAnchors[number]|undefined>((best,recent)=>
        best===undefined||(recent.rsi??100)<(best.rsi??100)?recent:best,undefined);
      if(!supportAnchor)return;
      const reachedEma144=p.ema144>0&&p.low<=p.ema144*1.02&&p.high>=p.ema144*.98;
      const closedAboveEma144=p.close>=p.ema144;
      const bullishSupportConfirmation=p.close>p.open||p.close>previous.close;
      const rsiRecovering=p.rsi!==null&&supportAnchor.rsi!==null&&p.rsi>=supportAnchor.rsi;
      if(reachedEma144&&closedAboveEma144&&bullishSupportConfirmation&&rsiRecovering)trendPullbackTimes.add(Number(p.time));
    });
    const combinedBuyPoints=points.filter((p)=>{
      if(trendStartTimes.size>0){
        const laterHighConvictionReversal=mainBuyCandidates.includes(p)&&(p.rsi_v_bottom_buy||p.rsi_bullish_divergence);
        return trendStartTimes.has(Number(p.time))||trendPullbackTimes.has(Number(p.time))||laterHighConvictionReversal;
      }
      return mainBuyCandidates.includes(p)||trendPullbackTimes.has(Number(p.time));
    });
    const mainBuyPoints=combinedBuyPoints.slice(-1);
    const priceMarkers:SeriesMarker<UTCTimestamp>[]=mainBuyPoints.map((p)=>({
      time:p.time, position:"belowBar" as const,
      color:p.rsi_v_bottom_buy?"#f5c542":"#f0a23a",
      shape:"arrowUp" as const,
      text:t(trendStartTimes.has(Number(p.time))?"trendStartBuy":trendPullbackTimes.has(Number(p.time))?"trendPullbackBuy":p.rsi_v_bottom_buy?"rsiVBottomBuy":"rsiBreakoutBuy"),
    }));
    if(payload.base_breakout){
      const signal=payload.base_breakout;
      priceMarkers.push({
        time:Math.floor(new Date(signal.timestamp).getTime()/1000) as UTCTimestamp,
        position:"belowBar",color:signal.buy_candidate?"#ff5b61":"#f2b94b",shape:signal.buy_candidate?"arrowUp":"circle",
        text:`${t(`baseType.${signal.base_type}`)} · ${t(signal.buy_candidate?"baseBuy":"baseWatch")}`,
      });
    }
    createSeriesMarkers(candles,priceMarkers);
    const rsi = rsiInstance.addSeries(LineSeries, { color:"#9b7de3", lineWidth:2, priceLineVisible:false, lastValueVisible:true });
    const rsiSignal = rsiInstance.addSeries(LineSeries, { color:"#d6ad42", lineWidth:1, priceLineVisible:false, lastValueVisible:true });
    rsi.setData(points.filter((p)=>p.rsi!==null).map((p)=>({time:p.time,value:p.rsi!})));
    rsiSignal.setData(points.filter((p)=>p.rsi_signal!==null).map((p)=>({time:p.time,value:p.rsi_signal!})));
    [30, 50, 70].forEach((level) => rsi.createPriceLine({
      price:level,
      color:level===70?"rgba(255,145,152,.72)":level===30?"rgba(116,224,174,.72)":"rgba(130,152,173,.45)",
      lineWidth:1,lineStyle:2,axisLabelVisible:true,title:"",
    }));
    createSeriesMarkers(rsi, points.filter((p)=>(p.rsi_w_bottom || p.rsi_enhanced_buy) && p.rsi!==null).map((p)=>({
      time:p.time, position:"belowBar" as const,
      color:p.rsi_enhanced_buy?"#4fd0ad":"#73a7d8",
      shape:p.rsi_enhanced_buy?"arrowUp" as const:"circle" as const,
      text:p.rsi_enhanced_buy?(p.rsi_bullish_divergence?t("rsiDivergenceConfirmed"):t("rsiMomentumConfirmed")):t("rsiWSetup"),
    })));
    const macdHistogram=macdInstance.addSeries(HistogramSeries,{priceLineVisible:false,lastValueVisible:true,priceFormat:{type:"price",precision:3,minMove:.001}});
    const macdLine=macdInstance.addSeries(LineSeries,{color:"#4f8ff7",lineWidth:2,priceLineVisible:false,lastValueVisible:true});
    const macdSignal=macdInstance.addSeries(LineSeries,{color:"#ef6b73",lineWidth:1,priceLineVisible:false,lastValueVisible:true});
    macdHistogram.setData(points.map((p,index)=>({time:p.time,value:p.macd_hist*2,color:p.macd_hist>=0?(p.macd_hist_growing?"#26a69a":"#b2dfdb"):(p.macd_hist_growing?"#ffcdd2":"#ff5252")})));
    macdLine.setData(points.map(p=>({time:p.time,value:p.macd})));
    macdSignal.setData(points.map(p=>({time:p.time,value:p.macd_signal})));
    macdHistogram.createPriceLine({price:0,color:"rgba(130,152,173,.35)",lineWidth:1,lineStyle:0,axisLabelVisible:false,title:""});
    points.filter(p=>p.macd_divergence_from_timestamp&&p.macd_divergence_to_timestamp&&p.macd_divergence_from_value!==null&&p.macd_divergence_to_value!==null).forEach(p=>{
      const divergenceLine=macdInstance.addSeries(LineSeries,{color:"rgba(255,255,255,.9)",lineWidth:1,lineStyle:2,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false});
      divergenceLine.setData([
        {time:Math.floor(new Date(p.macd_divergence_from_timestamp!).getTime()/1000) as UTCTimestamp,value:p.macd_divergence_from_value!*2},
        {time:Math.floor(new Date(p.macd_divergence_to_timestamp!).getTime()/1000) as UTCTimestamp,value:p.macd_divergence_to_value!*2},
      ]);
    });
    createSeriesMarkers(macdLine,points.filter(p=>p.macd_golden_cross||p.macd_dead_cross).map(p=>({
      time:p.time,position:p.macd_dead_cross?"aboveBar" as const:"belowBar" as const,
      color:p.macd_golden_cross?"#4f8ff7":"#ef6b73",shape:p.macd_dead_cross?"arrowDown" as const:"arrowUp" as const,
    })));
    createSeriesMarkers(macdHistogram,points.filter(p=>(p.macd_bull_divergence||p.macd_bear_divergence)&&p.macd_divergence_to_value!==null).map(p=>({
      time:p.time,position:p.macd_bear_divergence?"atPriceTop" as const:"atPriceBottom" as const,price:p.macd_divergence_to_value!*2,
      color:"#f0a23a",shape:p.macd_bear_divergence?"arrowDown" as const:"arrowUp" as const,
      text:t(p.macd_bear_divergence?"macdBearDivergence":"macdBullDivergence"),
    })));
    const pointsByTime=new Map(points.map((point)=>[Number(point.time),point]));
    let crosshairSyncing=false;
    const syncMainCrosshair=(param:{time?:unknown})=>{
      if(crosshairSyncing)return;
      crosshairSyncing=true;
      const point=param.time===undefined?undefined:pointsByTime.get(Number(param.time));
      if(point?.rsi!==null&&point?.rsi!==undefined)rsiInstance.setCrosshairPosition(point.rsi,point.time,rsi);
      else rsiInstance.clearCrosshairPosition();
      if(point)macdInstance.setCrosshairPosition(point.macd,point.time,macdLine);else macdInstance.clearCrosshairPosition();
      crosshairSyncing=false;
    };
    const syncMacdCrosshair=(param:{time?:unknown})=>{
      if(crosshairSyncing)return;crosshairSyncing=true;
      const point=param.time===undefined?undefined:pointsByTime.get(Number(param.time));
      if(point){instance.setCrosshairPosition(point.close,point.time,candles);if(point.rsi!==null)rsiInstance.setCrosshairPosition(point.rsi,point.time,rsi)}
      else{instance.clearCrosshairPosition();rsiInstance.clearCrosshairPosition()}
      crosshairSyncing=false;
    };
    const syncRsiCrosshair=(param:{time?:unknown})=>{
      if(crosshairSyncing)return;
      crosshairSyncing=true;
      const point=param.time===undefined?undefined:pointsByTime.get(Number(param.time));
      if(point)instance.setCrosshairPosition(point.close,point.time,candles);
      else instance.clearCrosshairPosition();
      crosshairSyncing=false;
    };
    instance.subscribeCrosshairMove(syncMainCrosshair);
    rsiInstance.subscribeCrosshairMove(syncRsiCrosshair);
    macdInstance.subscribeCrosshairMove(syncMacdCrosshair);
    const initialBars: Record<Timeframe, number> = { weekly: 120, daily: 100, "4hour": 140 };
    const visibleCount = Math.min(initialBars[timeframe], points.length);
    const initialRange = { from: points.length - visibleCount, to: points.length + 4 };
    instance.timeScale().setVisibleLogicalRange(initialRange);
    rsiInstance.timeScale().setVisibleLogicalRange(initialRange);
    macdInstance.timeScale().setVisibleLogicalRange(initialRange);
    const overlay=document.createElement("canvas");
    Object.assign(overlay.style,{position:"absolute",inset:"0",pointerEvents:"none",zIndex:"4"});
    host.current.appendChild(overlay);
    const drawStructure=()=>{
      const width=host.current?.clientWidth??0, height=host.current?.clientHeight??0, ratio=window.devicePixelRatio||1;
      overlay.width=Math.max(1,Math.floor(width*ratio)); overlay.height=Math.max(1,Math.floor(height*ratio)); overlay.style.width=`${width}px`; overlay.style.height=`${height}px`;
      const context=overlay.getContext("2d"); if(!context)return; context.scale(ratio,ratio); context.clearRect(0,0,width,height);
      payload.market_structure.order_blocks.forEach((block)=>{
        const start=instance.timeScale().timeToCoordinate(Math.floor(new Date(block.confirmed_at_timestamp).getTime()/1000) as UTCTimestamp);
        const top=candles.priceToCoordinate(block.top), bottom=candles.priceToCoordinate(block.bottom);
        if(start===null||top===null||bottom===null)return;
        context.fillStyle=block.bias==="bullish"?"rgba(49,121,245,.18)":"rgba(247,124,128,.18)";
        context.fillRect(start,Math.min(top,bottom),Math.max(0,width-start),Math.abs(bottom-top));
      });
      payload.market_structure.levels.forEach((level)=>{
        const start=instance.timeScale().timeToCoordinate(Math.floor(new Date(level.start_timestamp).getTime()/1000) as UTCTimestamp), y=candles.priceToCoordinate(level.price);
        if(start===null||y===null)return; context.strokeStyle=level.kind.endsWith("low")?"#2dbfa0":"#ef626c"; context.fillStyle=context.strokeStyle;
        context.setLineDash([8,7]); context.beginPath(); context.moveTo(start,y); context.lineTo(width,y); context.stroke(); context.setLineDash([]);
        context.font="11px Inter, Microsoft YaHei, sans-serif"; context.textAlign="right"; context.fillText(t(`structure.${level.kind}`),width-8,Math.max(12,y-5));
      });
    };
    const resizeObserver=new ResizeObserver(drawStructure); resizeObserver.observe(host.current); requestAnimationFrame(drawStructure);
    let syncing=false;
    instance.timeScale().subscribeVisibleLogicalRangeChange((range)=>{drawStructure();if(!range||syncing)return;syncing=true;rsiInstance.timeScale().setVisibleLogicalRange(range);macdInstance.timeScale().setVisibleLogicalRange(range);syncing=false});
    rsiInstance.timeScale().subscribeVisibleLogicalRangeChange((range)=>{if(!range||syncing)return;syncing=true;instance.timeScale().setVisibleLogicalRange(range);macdInstance.timeScale().setVisibleLogicalRange(range);syncing=false});
    macdInstance.timeScale().subscribeVisibleLogicalRangeChange((range)=>{if(!range||syncing)return;syncing=true;instance.timeScale().setVisibleLogicalRange(range);rsiInstance.timeScale().setVisibleLogicalRange(range);syncing=false});
    return () => { instance.unsubscribeCrosshairMove(syncMainCrosshair); rsiInstance.unsubscribeCrosshairMove(syncRsiCrosshair); macdInstance.unsubscribeCrosshairMove(syncMacdCrosshair); resizeObserver.disconnect(); overlay.remove(); instance.remove(); rsiInstance.remove(); macdInstance.remove(); if (chart.current === instance) chart.current = null; if(rsiChart.current===rsiInstance)rsiChart.current=null;if(macdChart.current===macdInstance)macdChart.current=null; };
  }, [payload, timeframe, t, showTradeLevels, showVegas, showTrendlines]);

  return <div className="chart-view">
    <div className="chart-toolbar">
      <div className="chart-switches"><div className="period-switch">{periods.map((period) => <button key={period} className={timeframe===period ? "active" : ""} onClick={() => setTimeframe(period)}>{t(period==="4hour"?"fourHour":period)}</button>)}</div><div className="indicator-switch"><button className={indicatorPane==="rsi"?"active":""} onClick={()=>{setIndicatorPane("rsi");localStorage.setItem("chart-indicator-pane","rsi")}}>RSI</button><button className={indicatorPane==="macd"?"active":""} onClick={()=>{setIndicatorPane("macd");localStorage.setItem("chart-indicator-pane","macd")}}>MACD</button></div></div>
      <div className="chart-contract"><button className={showVegas?"active":""} onClick={()=>setShowVegas((visible)=>{localStorage.setItem("chart-vegas",String(!visible));return !visible})}>{t("vegasChannel")}</button><button className={showTrendlines?"active":""} onClick={()=>setShowTrendlines((visible)=>{localStorage.setItem("chart-trendlines",String(!visible));return !visible})}>{t("trendlines")}</button><button className={showTradeLevels?"active":""} onClick={()=>setShowTradeLevels((visible)=>{localStorage.setItem("chart-trade-levels",String(!visible));return !visible})}>{t("tradeLevels")}</button><span>{t("forward")}</span><span>{t("allSessions")}</span><span>{t("localCache")}</span>{payload && <span>{payload.count} {t("bars")}</span>}</div>
    </div>
    <div className="chart-legend"><span className="ema12">EMA12</span><span className="yellow">EMA144 / 169</span><span className="green">EMA576 / 676</span><span className="support">{t("trendSupport")}</span><span className="resistance">{t("trendResistance")}</span><span className="structure">{t("marketStructure")}</span><span className="rsi">{t("rsiNote")}</span></div>
    {loading && <div className="chart-message">{t("chartLoading")}</div>}
    {error && <div className="chart-message error"><strong>{t("chartError")}</strong><span>{error}</span><small>{t("syncFirst")}</small></div>}
    <div className={`chart-stack ${loading || error ? "hidden" : ""}`}><div ref={host} className="chart-host"/>{indicatorPane==="rsi"?<div className="rsi-label">RSI 10 <span>SMA 10</span><i>{t("rsiEnhancedSignal")}</i></div>:<div className="macd-label">MACD XD <span>12/26/9</span><i>{t("macdArea")}: {payload?.bars.at(-1)?.macd_area.toFixed(1)}</i></div>}<div className="indicator-host-stack"><div ref={rsiHost} className={`rsi-host ${indicatorPane!=="rsi"?"indicator-hidden":""}`}/><div ref={macdHost} className={`macd-host ${indicatorPane!=="macd"?"indicator-hidden":""}`}/></div></div>
  </div>;
}
