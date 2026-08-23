import {useEffect,useState} from "react";
import {api,type AuthSettings} from "./api";
import {useTranslation} from "react-i18next";

export default function SystemSettingsTab(){
  const {t}=useTranslation();
  const [data,setData]=useState<AuthSettings|null>(null);
  const [mode,setMode]=useState<"oauth"|"apikey">("oauth");
  const [clientId,setClientId]=useState("");
  const [proxyEnabled,setProxyEnabled]=useState(false);
  const [proxyHost,setProxyHost]=useState("127.0.0.1");
  const [proxyPort,setProxyPort]=useState(7890);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState("");
  const [error,setError]=useState("");

  useEffect(()=>{
    Promise.all([api.authSettings(),api.proxySettings()]).then(([auth,proxy])=>{
      setData(auth); setMode(auth.auth_mode); setClientId(auth.oauth_client_id??"");
      setProxyEnabled(proxy.enabled); setProxyHost(proxy.host); setProxyPort(proxy.port);
    }).catch((e:Error)=>setError(e.message));
  },[]);

  async function saveAuth(){
    setBusy(true); setError(""); setMessage("");
    try{const x=await api.saveAuthSettings(mode,clientId);setData(x);setMessage(t("authSaved"))}catch(e){setError(e instanceof Error?e.message:t("saveFailed"))}finally{setBusy(false)}
  }
  async function authorize(){
    setBusy(true); setError(""); setMessage(t("waitingAuthorization"));
    try{await api.authorizeOAuth();setMessage(t("oauthSuccess"))}catch(e){setError(e instanceof Error?e.message:t("oauthFailed"));setMessage("")}finally{setBusy(false)}
  }
  async function saveProxy(){
    setBusy(true); setError(""); setMessage("");
    try{const x=await api.saveProxySettings(proxyEnabled,proxyHost,proxyPort);setProxyEnabled(x.enabled);setProxyHost(x.host);setProxyPort(x.port);const url=`http://${x.host}:${x.port}`;setMessage(x.enabled?t("proxyEnabledMessage",{url}):t("proxyDisabledMessage"))}catch(e){setError(e instanceof Error?e.message:t("proxySaveFailed"))}finally{setBusy(false)}
  }

  return <div className="system-settings">
    <header><div><p className="eyebrow">SYSTEM SETTINGS</p><h2>{t("systemSettings")}</h2></div><span className={data?.configured?"setting-ok":"setting-warn"}>{data?.configured?t("settingsConfigured"):t("settingsNotConfigured")}</span></header>
    <section className="settings-card">
      <h3>{t("authMethod")}</h3>
      <label className="auth-choice"><input type="radio" checked={mode==="oauth"} onChange={()=>setMode("oauth")}/><div><strong>{t("oauthRecommended")}</strong><span>{t("oauthDescription")}</span></div></label>
      <label className="auth-choice"><input type="radio" checked={mode==="apikey"} onChange={()=>setMode("apikey")}/><div><strong>{t("apiKeyMethod")}</strong><span>{t("apiKeyDescription")}</span></div></label>
      {mode==="oauth"&&<label className="client-id-field"><span>OAuth Client ID</span><input value={clientId} onChange={e=>setClientId(e.target.value.trim())} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"/></label>}
      <div className="settings-actions"><button className="import-action" disabled={busy} onClick={saveAuth}>{t("saveAuth")}</button>{mode==="oauth"&&<button className="primary-action" disabled={busy||!clientId} onClick={authorize}>{busy?t("processing"):t("authorizeOAuth")}</button>}</div>
    </section>
    <section className="settings-card proxy-card">
      <div className="proxy-heading"><div><h3>{t("networkProxy")}</h3><p>{t("proxyDescription")}</p></div><label className="proxy-switch"><input type="checkbox" checked={proxyEnabled} onChange={e=>setProxyEnabled(e.target.checked)}/><span>{proxyEnabled?t("enabled"):t("disabled")}</span></label></div>
      <div className="proxy-fields">
        <label><span>{t("proxyHost")}</span><input disabled={!proxyEnabled} value={proxyHost} onChange={e=>setProxyHost(e.target.value.trim())} placeholder="127.0.0.1"/></label>
        <label><span>{t("proxyPort")}</span><input disabled={!proxyEnabled} type="number" min="1" max="65535" value={proxyPort} onChange={e=>setProxyPort(Number(e.target.value))}/></label>
      </div>
      {proxyEnabled&&<p className="proxy-preview">{t("currentProxy",{url:`http://${proxyHost||"127.0.0.1"}:${proxyPort}`})}</p>}
      <div className="settings-actions"><button className="primary-action" disabled={busy||(proxyEnabled&&(!proxyHost||proxyPort<1||proxyPort>65535))} onClick={saveProxy}>{t("saveProxy")}</button></div>
      <p className="proxy-note">{t("proxyBrowserNote")}</p>
    </section>
    {error&&<p className="settings-error">{error}</p>}{message&&<p className="settings-success">{message}</p>}
    <section className="settings-security"><strong>{t("securityNote")}</strong><span>{t("securityText")}</span></section>
  </div>
}
