import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { displaySymbol } from "./display";
import { useTranslation } from "react-i18next";

export default function WatchlistTab({symbols,onSaved}:{symbols:string[];onSaved:(symbols:string[])=>void}){
  const {t}=useTranslation();
  const [value,setValue]=useState("");
  const [saving,setSaving]=useState(false);
  const [message,setMessage]=useState<string|null>(null);
  const [error,setError]=useState<string|null>(null);
  const fileInput=useRef<HTMLInputElement>(null);
  useEffect(()=>setValue(symbols.map(displaySymbol).join("\n")),[symbols]);
  const entries=useMemo(()=>value.split(/[\s,，;；]+/).map(x=>x.trim()).filter(Boolean),[value]);
  function normalizedDisplay(raw:string){
    const upper=raw.replace(/^\uFEFF/,"").trim().toUpperCase();
    return upper.endsWith(".US")?upper.slice(0,-3):upper;
  }
  async function importTxt(file:File|undefined){
    if(!file)return;
    setError(null);setMessage(null);
    try{
      if(!file.name.toLowerCase().endsWith(".txt"))throw new Error("请选择 .txt 文件");
      if(file.size===0)throw new Error("所选 TXT 文件为空，请先写入股票代码并保存文件");
      if(file.size>2*1024*1024)throw new Error("TXT 文件不能超过 2 MB");
      const imported=(await file.text()).split(/[\s,，;；]+/).map(normalizedDisplay).filter(Boolean);
      if(imported.length===0)throw new Error("TXT 文件中没有识别到股票代码");
      const merged:string[]=[];const seen=new Set<string>();
      for(const item of [...entries,...imported]){
        const display=normalizedDisplay(item);const key=display.toUpperCase();
        if(display&&!seen.has(key)){seen.add(key);merged.push(display)}
      }
      setValue(merged.join("\n"));
      setMessage(`已从 ${file.name} 导入 ${imported.length} 条，合并去重后共 ${merged.length} 只；请确认后保存`);
    }catch(reason){setError(reason instanceof Error?reason.message:"读取 TXT 文件失败")}
    finally{if(fileInput.current)fileInput.current.value=""}
  }
  async function save(){
    setSaving(true);setError(null);setMessage(null);
    try{
      const result=await api.saveWatchlist(entries);
      onSaved(result.symbols);
      setValue(result.symbols.map(displaySymbol).join("\n"));
      setMessage(`已保存 ${result.symbols.length} 只股票；新增股票将在下次扫描时自动补全 K 线`);
    }catch(reason){setError(reason instanceof Error?reason.message:"保存失败")}
    finally{setSaving(false)}
  }
  return <div className="watchlist-manager">
    <header><div><p className="eyebrow">WATCHLIST MANAGER</p><h2>{t("watchlistManager")}</h2></div><span>{entries.length}</span></header>
    <p className="watchlist-help">{t("watchlistHelp")}</p>
    <textarea spellCheck={false} value={value} onChange={event=>{setValue(event.target.value);setMessage(null);setError(null)}} placeholder={"AAPL\nMSFT\nNVDA"}/>
    <footer><div>{error?<span className="manager-error">{error}</span>:message?<span className="manager-success">{message}</span>:<span>{t("importHint")}</span>}</div><div className="manager-actions"><input ref={fileInput} className="file-input" type="file" accept=".txt,text/plain" onChange={event=>void importTxt(event.target.files?.[0])}/><button className="import-action" disabled={saving} onClick={()=>fileInput.current?.click()}>{t("importTxt")}</button><button className="primary-action" disabled={saving||entries.length===0} onClick={save}>{saving?t("saving"):t("saveWatchlist")}</button></div></footer>
    <section className="backfill-note"><strong>{t("autoBackfill")}</strong><span>{t("autoBackfillText")}</span></section>
  </div>
}
