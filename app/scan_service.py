from __future__ import annotations

import hashlib, json, threading
from datetime import datetime, timezone
from uuid import UUID, uuid4
import pandas as pd
from app.config import Settings
from app.database import Database
from app.market_data import LongPortProvider, ParquetBarRepository, TIMEFRAMES, _is_closed, cache_needs_sync
from app.scanner_engine import scan_symbol
from app.watchlist import load_watchlist
from app.json_utils import json_safe

class ScanService:
    def __init__(self,settings:Settings,database:Database,provider:LongPortProvider,repository:ParquetBarRepository):
        self.settings=settings; self.database=database; self.provider=provider; self.repository=repository
        self._lock=threading.Lock(); self._active_run_id:UUID|None=None

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active_run_id is not None

    def run_results(self, run_id: UUID) -> dict[str, dict]:
        with self.database.connect(read_only=True) as c:
            rows = c.execute("SELECT symbol,total_score,triggered_factors_json FROM scan_results WHERE run_id=?",[run_id]).fetchall()
        return {row[0]: {"total_score": row[1], "triggered_factors": json.loads(row[2])} for row in rows}

    def sync_symbol(self, symbol: str) -> dict[str, int]:
        """Force an immediate LongPort refresh of one symbol's cached bars, all timeframes."""
        if self.active:
            raise RuntimeError("已有扫描任务正在运行，请稍后重试")
        if not self.provider.configured:
            raise RuntimeError("LongPort 凭据尚未配置")
        if self.settings.longport_auth_mode.lower() == "oauth":
            self.provider.ensure_authenticated()
        updated: dict[str, int] = {}
        for tf in TIMEFRAMES:
            cached = self.repository.read(symbol, tf)
            desired_session = "all" if tf == "4hour" else "intraday"
            session_mismatch = not cached.empty and str(cached.iloc[-1].get("trade_session", "all")) != desired_session
            request_count = self.settings.bars_per_timeframe if cached.empty or session_mismatch else self.settings.tail_refresh_bars
            merged = self.repository.merge(symbol, tf, self.provider.fetch_bars(symbol, tf, request_count))
            self._save_manifest(symbol, tf, merged)
            updated[tf] = len(merged)
        return updated

    def create_run(self,run_type:str,sync_timeframes:tuple[str,...]=TIMEFRAMES)->tuple[UUID,str,str]:
        with self._lock:
            if self._active_run_id:return self._active_run_id,"running","已有扫描任务正在运行"
            if sync_timeframes and not self.provider.configured:raise RuntimeError("LongPort 凭据尚未配置")
            symbols=load_watchlist(self.settings.watchlist_path).symbols; run_id=uuid4()
            invalid=set(sync_timeframes)-set(TIMEFRAMES)
            if invalid:raise ValueError(f"Unsupported sync timeframes: {sorted(invalid)}")
            cfg={"initial_bars_per_timeframe":self.settings.bars_per_timeframe,"tail_refresh_bars":self.settings.tail_refresh_bars,"timeframes":list(TIMEFRAMES),"sync_timeframes":list(sync_timeframes),"adjustment_type":"forward","trade_session":"all","closed_bars_only":run_type=="official","auth_mode":self.settings.longport_auth_mode}; cfg_json=json.dumps(cfg,sort_keys=True); cfg_hash=hashlib.sha256(cfg_json.encode()).hexdigest()
            with self.database.connect() as c:c.execute("INSERT INTO scan_runs (run_id,run_type,status,started_at,algorithm_version,config_version,config_json,config_hash,watchlist_json,symbols_total) VALUES (?,?,'running',?,?,?,?,?,?,?)",[run_id,run_type,datetime.now(timezone.utc),self.settings.algorithm_version,self.settings.config_version,cfg_json,cfg_hash,json.dumps(symbols),len(symbols)])
            self._active_run_id=run_id; threading.Thread(target=self._execute,args=(run_id,run_type,symbols,sync_timeframes),daemon=True).start()
            return run_id,"running","行情同步与扫描已开始"

    def _execute(self,run_id:UUID,run_type:str,symbols:list[str],sync_timeframes:tuple[str,...]=TIMEFRAMES)->None:
        succeeded=failed=0; errors=[]; cutoff=None; status="failed"
        try:
            if sync_timeframes and self.settings.longport_auth_mode.lower() == "oauth":
                self.provider.ensure_authenticated()
            for symbol in symbols:
                try:
                    frames={}
                    for tf in TIMEFRAMES:
                        cached=self.repository.read(symbol,tf)
                        desired_session="all" if tf=="4hour" else "intraday"
                        session_mismatch=not cached.empty and str(cached.iloc[-1].get("trade_session","all"))!=desired_session
                        if tf in sync_timeframes and (session_mismatch or cache_needs_sync(cached,tf)):
                            if cached.empty and not self.settings.auto_backfill_new_symbols:
                                raise ValueError(f"{tf} 没有本地 K 线，且自动补全已关闭")
                            request_count = self.settings.bars_per_timeframe if cached.empty or session_mismatch else self.settings.tail_refresh_bars
                            merged=self.repository.merge(symbol,tf,self.provider.fetch_bars(symbol,tf,request_count)); self._save_manifest(symbol,tf,merged)
                        else:
                            merged=cached
                        now=pd.Timestamp.now(tz="UTC"); merged=merged.copy()
                        merged["is_closed"]=merged["timestamp_utc"].map(lambda value:_is_closed(value,tf,now))
                        usable=merged[merged.is_closed] if run_type=="official" else merged
                        if usable.empty:raise ValueError(f"{tf} 没有可用 K 线")
                        frames[tf]=usable.set_index("timestamp_utc")[["open","high","low","close","volume"]].rename(columns=str.title)
                    result=scan_symbol(symbol,frames); self._save_result(run_id,result); succeeded+=1
                    latest=max(pd.Timestamp(v["bar_timestamp"]) for v in result["timeframes"].values()); cutoff=max(cutoff,latest) if cutoff else latest
                except Exception as exc:
                    failed+=1; errors.append({"symbol":symbol,"message":str(exc)}); self._save_error(run_id,symbol,str(exc))
                with self.database.connect() as c:
                    c.execute("UPDATE scan_runs SET symbols_succeeded=?, symbols_failed=? WHERE run_id=?",[succeeded,failed,run_id])
            status="completed" if failed==0 else "completed_with_errors"
        except Exception as exc:errors.append({"message":str(exc)})
        finally:
            with self.database.connect() as c:c.execute("UPDATE scan_runs SET status=?,completed_at=?,market_data_cutoff=?,symbols_succeeded=?,symbols_failed=?,error_summary=? WHERE run_id=?",[status,datetime.now(timezone.utc),cutoff,succeeded,failed,json.dumps(errors,ensure_ascii=False),run_id])
            with self._lock:self._active_run_id=None

    def _save_manifest(self,symbol,tf,bars):
        trade_session="all" if tf=="4hour" else "intraday"
        with self.database.connect() as c:c.execute("INSERT OR REPLACE INTO market_cache_manifest VALUES (?,?,?,?,?,?,?,?,?,?,?)",[symbol,tf,str(self.repository.path_for(symbol,tf)),len(bars),bars.timestamp_utc.min(),bars.timestamp_utc.max(),"forward",trade_session,"ok",datetime.now(timezone.utc),None])

    def _save_result(self,run_id,result):
        s=result["scoring"]; t=result["timeframes"]
        with self.database.connect() as c:
            c.begin()
            try:
                triggered_json=json.dumps(result["triggered_factors"])
                duplicate_runs=[]
                if self.settings.deduplicate_unchanged_results:
                    duplicates=c.execute("""
                        SELECT r.run_id
                        FROM scan_results r JOIN scan_runs sr ON sr.run_id=r.run_id
                        WHERE r.symbol=? AND sr.run_type='official'
                          AND sr.status IN ('completed','completed_with_errors')
                          AND sr.algorithm_version=? AND sr.config_version=?
                          AND r.total_score=? AND CAST(r.triggered_factors_json AS VARCHAR)=?
                          AND r.weekly_bar_timestamp IS NOT DISTINCT FROM ?
                          AND r.daily_bar_timestamp IS NOT DISTINCT FROM ?
                          AND r.four_hour_bar_timestamp IS NOT DISTINCT FROM ?
                        ORDER BY sr.completed_at DESC
                    """,[result["symbol"],self.settings.algorithm_version,self.settings.config_version,s["total_score"],triggered_json,t["weekly"]["bar_timestamp"],t["daily"]["bar_timestamp"],t["4hour"]["bar_timestamp"]]).fetchall()
                    duplicate_runs=[row[0] for row in duplicates]
                c.execute("INSERT INTO scan_results (run_id,symbol,total_score,base_total,confluence_total,pre_multiplier_score,coverage_multiplier,triggered_factors_json,weekly_score,daily_score,four_hour_score,weekly_bar_timestamp,daily_bar_timestamp,four_hour_bar_timestamp,data_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[run_id,result["symbol"],s["total_score"],s["base_total"],s["confluence_total"],s["pre_multiplier_score"],s["coverage_multiplier"],json.dumps(result["triggered_factors"]),s["confluence"]["weekly"]["score"],s["confluence"]["daily"]["score"],s["confluence"]["4hour"]["score"],t["weekly"]["bar_timestamp"],t["daily"]["bar_timestamp"],t["4hour"]["bar_timestamp"],"正常"])
                for tf,tv in t.items():
                    for fid,f in tv["factors"].items():
                        x=s["contributions"].get(fid,{})
                        safe_details=json_safe(f["details"])
                        c.execute("INSERT INTO factor_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",[run_id,result["symbol"],tf,fid,f["triggered"],f["signal_name"],f["tier"],x.get("base_score",0),x.get("multiplier",0),x.get("score",0),f["timestamp"],safe_details.get("reason"),json.dumps(safe_details,ensure_ascii=False,allow_nan=False)])
                for duplicate_run in duplicate_runs:
                    c.execute("DELETE FROM factor_results WHERE run_id=? AND symbol=?",[duplicate_run,result["symbol"]])
                    c.execute("DELETE FROM scan_results WHERE run_id=? AND symbol=?",[duplicate_run,result["symbol"]])
                c.commit()
            except Exception:c.rollback();raise

    def _save_error(self,run_id,symbol,message):
        with self.database.connect() as c:c.execute("INSERT INTO scan_errors VALUES (?,?,?,?,?,?,?,current_timestamp)",[uuid4(),run_id,symbol,None,"scan","symbol_failed",message[:2000]])

    def run_status(self,run_id:UUID):
        with self.database.connect(read_only=True) as c:row=c.execute("SELECT status,symbols_total,symbols_succeeded,symbols_failed,started_at,completed_at,error_summary FROM scan_runs WHERE run_id=?",[run_id]).fetchone()
        if not row:return None
        return {"run_id":str(run_id),"status":row[0],"symbols_total":row[1],"symbols_succeeded":row[2],"symbols_failed":row[3],"started_at":row[4],"completed_at":row[5],"errors":json.loads(row[6]) if row[6] else []}
