"""Authenticated Home Assistant ingress dashboard for the add-on."""

# ruff: noqa: E501 -- Embedded HTML, CSS, and JavaScript remain readable as assets.

from __future__ import annotations

import json
import logging
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import urlsplit

_LOGGER = logging.getLogger("mvdis-penalty.web")


class DashboardAddon(Protocol):
    """Operations exposed by the scheduler to the ingress dashboard."""

    def public_status(self) -> dict[str, Any]: ...

    def trigger_refresh(self) -> bool: ...

    def send_test_notification(self, profile_key: str) -> bool: ...


class DashboardServer:
    """Small dependency-free HTTP server reachable only through ingress."""

    def __init__(self, addon: DashboardAddon, *, port: int) -> None:
        handler = _handler_for(addon)
        self._server = ThreadingHTTPServer(("0.0.0.0", port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mvdis-ingress",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()
        _LOGGER.info("Ingress dashboard started on port %s", self._server.server_port)

    @property
    def port(self) -> int:
        """Return the bound port, including an OS-assigned test port."""
        return self._server.server_port

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _handler_for(addon: DashboardAddon) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "MvdisPenalty/1"

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path.rstrip("/") or "/"
            if path == "/health":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
                return
            if not self._allow_ingress():
                return
            if path == "/":
                self._send(HTTPStatus.OK, DASHBOARD_HTML, "text/html; charset=utf-8")
                return
            if path == "/api/status":
                self._send_json(HTTPStatus.OK, addon.public_status())
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._allow_ingress():
                return
            if self.headers.get("X-Requested-With") != "XMLHttpRequest":
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "invalid_request"})
                return
            path = urlsplit(self.path).path.rstrip("/")
            if path == "/api/refresh":
                started = addon.trigger_refresh()
                self._send_json(
                    HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
                    {"started": started},
                )
                return
            if path == "/api/test-notification":
                body = self._read_json()
                profile_key = str(body.get("profile_key", ""))
                sent = addon.send_test_notification(profile_key)
                self._send_json(
                    HTTPStatus.ACCEPTED if sent else HTTPStatus.BAD_REQUEST,
                    {"sent": sent},
                )
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def _allow_ingress(self) -> bool:
            client = self.client_address[0]
            allow_local = os.environ.get("MVDIS_ALLOW_LOCAL_WEB") == "1"
            if client == "172.30.32.2" or (
                allow_local and client in {"127.0.0.1", "::1"}
            ):
                return True
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "ingress_only"})
            return False

        def _read_json(self) -> dict[str, Any]:
            try:
                length = max(
                    0,
                    min(int(self.headers.get("Content-Length", "0")), 4096),
                )
                value = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return {}
            return value if isinstance(value, dict) else {}

        def _send_json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
            self._send(
                status,
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                "application/json; charset=utf-8",
            )

        def _send(self, status: HTTPStatus, value: str, content_type: str) -> None:
            payload = value.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; img-src 'self' data:; "
                "frame-ancestors 'self'",
            )
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            _LOGGER.debug(format, *args)

    return DashboardHandler


DASHBOARD_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>監理站罰單通知</title>
  <style>
    :root { color-scheme:light dark; --bg:#f3f6f9; --surface:#fff; --surface-2:#f7f9fb;
      --text:#17212b; --muted:#687887; --line:#dbe3ea; --accent:#0876ce;
      --accent-soft:#0876ce16; --ok:#16834f; --ok-soft:#16834f16;
      --warn:#b66b00; --warn-soft:#b66b0018; --bad:#c93636; --bad-soft:#c9363615;
      --shadow:0 10px 30px #17324d0d; }
    @media(prefers-color-scheme:dark){:root{--bg:#0e1317;--surface:#181e22;--surface-2:#101518;
      --text:#f0f4f7;--muted:#9cabb5;--line:#303a40;--accent:#63b2f4;
      --accent-soft:#63b2f418;--ok:#59cb8b;--warn:#f0ac4d;--bad:#ff7777;
      --shadow:0 14px 34px #0004}}
    *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);
      font:15px/1.5 system-ui,-apple-system,"Noto Sans TC",sans-serif}
    button,input,select{font:inherit} main{max-width:1280px;margin:auto;padding:28px}
    .hero{position:relative;overflow:hidden;display:flex;justify-content:space-between;gap:24px;
      padding:26px 28px;border:1px solid var(--line);border-radius:22px;background:var(--surface);
      box-shadow:var(--shadow)} .hero:after{content:"";position:absolute;right:-70px;top:-110px;
      width:260px;height:260px;border-radius:50%;background:var(--accent-soft);pointer-events:none}
    .eyebrow{margin-bottom:6px;color:var(--accent);font-size:12px;font-weight:800;
      letter-spacing:.12em} h1{margin:0;font-size:clamp(25px,4vw,34px);line-height:1.2}
    #notice{min-height:23px;margin-top:9px;color:var(--muted)} .actions{z-index:1;align-self:center}
    button{border:0;border-radius:12px;padding:11px 17px;background:var(--accent);color:#fff;
      font-weight:750;white-space:nowrap;cursor:pointer;box-shadow:0 5px 16px var(--accent-soft)}
    button:hover{filter:brightness(1.06)} button:disabled{opacity:.5;cursor:wait;filter:none}
    button.secondary{padding:8px 12px;border:1px solid var(--line);background:transparent;
      color:var(--accent);box-shadow:none}
    .overview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:18px 0}
    .overview-group{min-height:96px;padding:16px 18px;border:1px solid var(--line);border-radius:16px;
      background:var(--surface);box-shadow:var(--shadow)}.overview-label{display:block;margin-bottom:10px;
      color:var(--muted);font-size:13px}.profile-chips{display:flex;flex-wrap:wrap;gap:8px}
    button.profile-chip{padding:7px 11px;border:1px solid var(--line);border-radius:999px;
      background:var(--surface-2);color:var(--text);font-size:13px;box-shadow:none}
    button.profile-chip:hover{border-color:var(--accent);color:var(--accent);filter:none}
    button.profile-chip.attention{border-color:var(--bad);background:var(--bad-soft);color:var(--bad)}
    .overview-empty{display:flex;align-items:center;gap:8px;width:100%;padding:9px 11px;
      border:1px solid var(--line);border-radius:12px;background:var(--surface-2);color:var(--muted);
      font-size:13px;font-weight:700}.overview-empty:before{content:"\\2713";display:grid;width:20px;height:20px;
      place-items:center;border-radius:50%;background:var(--line);color:var(--surface);font-size:12px}
    .overview-empty.clear{border-color:var(--ok);background:var(--ok-soft);color:var(--ok)}
    .overview-empty.clear:before{background:var(--ok);color:var(--surface)}
    .schedule{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;overflow:hidden;
      margin-bottom:18px;border:1px solid var(--line);border-radius:16px;background:var(--line)}
    .schedule-item{padding:13px 16px;background:var(--surface)} .schedule-item span{display:block;
      color:var(--muted);font-size:12px}.schedule-item strong{display:block;margin-top:2px;font-size:14px}
    .toolbar{display:flex;align-items:center;gap:10px;margin:24px 0 14px}.toolbar h2{margin:0 auto 0 0;
      font-size:20px}.control{height:42px;border:1px solid var(--line);border-radius:12px;
      background:var(--surface);color:var(--text);outline:none}.control:focus{border-color:var(--accent);
      box-shadow:0 0 0 3px var(--accent-soft)} input.control{width:min(280px,100%);padding:0 13px}
    select.control{padding:0 34px 0 12px}.result-count{min-width:84px;color:var(--muted);text-align:right}
    .people-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
    .person-card{display:flex;min-width:0;flex-direction:column;padding:20px;border:1px solid var(--line);
      border-radius:18px;background:var(--surface);box-shadow:var(--shadow)}
    .person-card:focus{outline:2px solid var(--accent);outline-offset:3px}.person-card.highlight{
      box-shadow:0 0 0 3px var(--accent-soft),var(--shadow)}
    .person-head{display:flex;align-items:flex-start;gap:12px}.avatar{display:grid;flex:0 0 42px;
      width:42px;height:42px;place-items:center;border-radius:13px;background:var(--accent-soft);
      color:var(--accent);font-size:18px;font-weight:850}.person-title{min-width:0;flex:1}
    .person-title h3{overflow:hidden;margin:0;text-overflow:ellipsis;white-space:nowrap;font-size:20px}
    .muted{color:var(--muted)}.checked{font-size:13px}.badge{flex:0 0 auto;border-radius:999px;
      padding:5px 9px;background:var(--surface-2);font-size:12px;font-weight:800}
    .badge.ok{color:var(--ok);background:var(--ok-soft)}.badge.unpaid{color:var(--warn);background:var(--warn-soft)}
    .badge.error{color:var(--bad);background:var(--bad-soft)}.badge.waiting{color:var(--warn);background:var(--warn-soft)}
    .metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:17px 0}
    .metric{min-width:0;padding:12px;border-radius:12px;background:var(--surface-2)}
    .metric strong{display:block;overflow:hidden;font-size:20px;text-overflow:ellipsis;white-space:nowrap}
    .metric span{color:var(--muted);font-size:12px}.error-box{margin-bottom:14px;padding:11px 12px;
      border-radius:10px;background:var(--bad-soft);color:var(--bad)}
    .penalty-list{display:grid;gap:8px;margin-bottom:16px} details{overflow:hidden;border:1px solid var(--line);
      border-radius:12px;background:var(--surface-2)} summary{display:flex;align-items:center;gap:8px;
      padding:11px 13px;cursor:pointer;font-weight:750;list-style:none} summary::-webkit-details-marker{display:none}
    summary:after{content:"＋";margin-left:auto;color:var(--accent)}details[open] summary:after{content:"−"}
    .penalty-amount{color:var(--warn);font-weight:800}.detail-grid{display:grid;
      grid-template-columns:minmax(110px,.8fr) minmax(0,2fr);border-top:1px solid var(--line)}
    .detail-grid dt,.detail-grid dd{margin:0;padding:9px 12px;border-bottom:1px solid var(--line)}
    .detail-grid dt{color:var(--muted)}.detail-grid dd{overflow-wrap:anywhere}
    .detail-grid dt:last-of-type,.detail-grid dd:last-of-type{border-bottom:0}
    .empty{display:grid;min-height:82px;place-items:center;margin-bottom:14px;padding:16px;
      border:1px dashed var(--line);border-radius:12px;color:var(--muted);text-align:center}
    .card-footer{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:auto;
      padding-top:4px}.card-footer span{color:var(--muted);font-size:12px}
    .full-width{grid-column:1/-1}.no-results{grid-column:1/-1;padding:48px 20px;border:1px dashed var(--line);
      border-radius:16px;color:var(--muted);text-align:center}
    @media(max-width:850px){main{padding:18px}.overview{grid-template-columns:repeat(2,minmax(0,1fr))}
      .people-grid{grid-template-columns:1fr}.toolbar{flex-wrap:wrap}.toolbar h2{width:100%}
      .result-count{margin-left:auto}}
    @media(max-width:650px){.schedule{grid-template-columns:1fr}.schedule-item{display:flex;
      align-items:center;justify-content:space-between;gap:14px}.schedule-item strong{text-align:right}}
    @media(max-width:560px){main{padding:12px}.hero{align-items:flex-start;padding:20px;flex-direction:column}
      .actions,.actions button{width:100%}.overview{gap:8px;grid-template-columns:1fr}
      .schedule{grid-template-columns:1fr}.toolbar{gap:8px}
      input.control{width:100%}select.control{flex:1}.person-card{padding:16px}.metrics{grid-template-columns:1fr 1fr}
      .metric:last-child{grid-column:1/-1}.detail-grid{grid-template-columns:1fr}.detail-grid dt{padding-bottom:2px;
      border-bottom:0}.detail-grid dd{padding-top:2px}.badge{max-width:110px;text-align:center}}
  </style>
</head>
<body><main>
  <header class="hero">
    <div><div class="eyebrow">TAIWAN MVDIS</div><h1>監理站罰單通知</h1>
      <div id="notice" aria-live="polite">載入中…</div></div>
    <div class="actions"><button id="refresh">立即查詢</button></div>
  </header>
  <section id="overview" class="overview" aria-label="多人總覽"></section>
  <section class="schedule" aria-label="查詢排程">
    <div class="schedule-item"><span>最近更新</span><strong id="last-refresh">尚未查詢</strong></div>
    <div class="schedule-item"><span>下次更新</span><strong id="next-refresh">尚未排程</strong></div>
    <div class="schedule-item"><span>查詢限制</span><strong id="query-guard">可立即查詢</strong></div>
  </section>
  <section class="toolbar" aria-label="查詢人篩選">
    <h2>查詢人</h2>
    <input id="search" class="control" type="search" placeholder="搜尋姓名" aria-label="搜尋查詢人姓名">
    <select id="status-filter" class="control" aria-label="依狀態篩選">
      <option value="all">全部狀態</option><option value="unpaid">有未繳紀錄</option>
      <option value="clear">查無資料</option><option value="error">需要注意</option>
    </select>
    <span id="result-count" class="result-count">顯示 0 / 0 人</span>
  </section>
  <section id="people" class="people-grid"></section>
</main>
<script>
const errorNames={waiting:"等待第一次查詢",captcha:"驗證碼辨識失敗",identity:"身分資料遭拒",
 response_format:"監理站回傳格式無法辨識",timeout:"監理站連線逾時",network:"網路連線失敗",
 http:"監理站服務錯誤",unknown:"未知錯誤"};
const money=v=>new Intl.NumberFormat("zh-TW").format(v||0);
const time=v=>v?new Date(v).toLocaleString("zh-TW",{hour12:false}):"尚未查詢";
const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;
 if(cls)node.className=cls;return node};
let latestData=null;const openPenalties=new Set();
function penaltiesOf(person){return Array.isArray(person.penalties)?person.penalties:[]}
function category(person){if(person.error)return "error";return penaltiesOf(person).length?"unpaid":"clear"}
function metric(label,value){const box=el("div",undefined,"metric");box.append(el("strong",value),el("span",label));return box}
function jumpToPerson(index){if(!latestData)return;document.querySelector("#search").value="";
 document.querySelector("#status-filter").value="all";renderPeople(latestData);requestAnimationFrame(()=>{
  const card=document.querySelector("#person-"+(index+1));if(!card)return;card.focus({preventScroll:true});
  card.scrollIntoView({behavior:"smooth",block:"start"});card.classList.add("highlight");
  setTimeout(()=>card.classList.remove("highlight"),1600)})}
function profileChip(person,index,attention=false){const chip=el("button",person.name,"profile-chip"+(attention?" attention":""));
 chip.type="button";chip.addEventListener("click",()=>jumpToPerson(index));return chip}
function overviewGroup(label,chips,emptyText,emptyKind="neutral"){const group=el("div",undefined,"overview-group"),list=el("div",undefined,"profile-chips");
 group.append(el("span",label,"overview-label"));if(chips.length)list.append(...chips);
 else list.append(el("span",emptyText,"overview-empty "+emptyKind));group.append(list);return group}
function renderOverview(data){const root=document.querySelector("#overview"),people=data.people||[];
 const all=people.map((person,index)=>profileChip(person,index));const attention=people.flatMap((person,index)=>
  person.error?[profileChip(person,index,true)]:[]);root.replaceChildren(overviewGroup("查詢人",all,"尚未設定"),
  overviewGroup("需要注意",attention,"目前無需注意","clear"))}
function renderSchedule(data){document.querySelector("#last-refresh").textContent=time(data.last_refresh_at);
 document.querySelector("#next-refresh").textContent=data.next_refresh_at?time(data.next_refresh_at):"尚未排程";
 const guard=data.cooldown_until?"冷卻至 "+time(data.cooldown_until):data.captcha_retry_at?
  "補查於 "+time(data.captcha_retry_at):data.next_allowed_query_at?
  "可再次立即查詢："+time(data.next_allowed_query_at):"可立即查詢";
 document.querySelector("#query-guard").textContent=guard}
function penaltyDetails(penalty,index,profileKey){const item=document.createElement("details"),heading=document.createElement("summary"),
 token=profileKey+":"+(penalty.key||index);item.open=openPenalties.has(token);
 item.addEventListener("toggle",()=>item.open?openPenalties.add(token):openPenalties.delete(token));
 heading.append(el("span","第 "+(index+1)+" 筆｜"+(penalty.summary||"交通違規罰單")));
 if(Number(penalty.amount))heading.append(el("span","NT$ "+money(Number(penalty.amount)),"penalty-amount"));
 item.append(heading);const list=el("dl",undefined,"detail-grid"),entries=Object.entries(penalty.details||{});
 if(!entries.length)entries.push(["內容",penalty.summary||"交通違規罰單"]);
 for(const [key,value] of entries)list.append(el("dt",key),el("dd",String(value)));item.append(list);return item}
function personCard(person,index){const penalties=penaltiesOf(person),card=el("article",undefined,"person-card");
 card.id="person-"+(index+1);card.tabIndex=-1;
 const head=el("div",undefined,"person-head"),avatar=el("div",(person.name||"?").trim().slice(0,1)||"?","avatar");
 const title=el("div",undefined,"person-title");title.append(el("h3",person.name),el("div","最後查詢："+time(person.checked_at),"muted checked"));
 const kind=category(person),status=person.error?(errorNames[person.error_type]||"查詢錯誤"):
  (penalties.length?"有未繳紀錄":"查詢成功");head.append(avatar,title,el("span",status,"badge "+(person.error?
  (person.error_type==="waiting"?"waiting":"error"):kind)));card.append(head);
 const total=penalties.reduce((sum,p)=>sum+(Number(p.amount)||0),0),metrics=el("div",undefined,"metrics");
 metrics.append(metric("未繳筆數",String(penalties.length)),metric("辨識金額","NT$ "+money(total)),
  metric("目前狀態",person.error?"需要注意":penalties.length?"有紀錄":"查無資料"));card.append(metrics);
 if(person.error){const retry=person.captcha_retry_at?"，將於 "+time(person.captcha_retry_at)+" 自動補查":"";
  card.append(el("div",(errorNames[person.error_type]||person.error)+retry,"error-box"))}
 if(penalties.length){const list=el("div",undefined,"penalty-list");penalties.forEach((p,i)=>list.append(penaltyDetails(p,i,person.key)));card.append(list)}
 else card.append(el("div",person.error?"保留上次成功資料；目前沒有可顯示的罰單內容。":"目前沒有可顯示的罰單內容。","empty"));
 const footer=el("div",undefined,"card-footer"),position=el("span","第 "+(index+1)+" 位查詢人");
 const test=el("button","傳送測試通知","secondary");test.addEventListener("click",()=>testNotification(person.key,test));
 footer.append(position,test);card.append(footer);return card}
function renderPeople(data){const root=document.querySelector("#people"),query=document.querySelector("#search").value.trim().toLocaleLowerCase("zh-TW"),
 filter=document.querySelector("#status-filter").value,people=data.people||[];const shown=people.filter(person=>
  (!query||person.name.toLocaleLowerCase("zh-TW").includes(query))&&(filter==="all"||category(person)===filter));
 root.replaceChildren();document.querySelector("#result-count").textContent="顯示 "+shown.length+" / "+people.length+" 人";
 if(!shown.length){root.append(el("div",people.length?"找不到符合條件的查詢人。":"尚未設定查詢人。","no-results"));return}
 shown.forEach(person=>root.append(personCard(person,people.indexOf(person))))}
function render(data){latestData=data;const cooling=Boolean(data.cooldown_until),guarded=Boolean(data.next_allowed_query_at),
 captchaRetry=Boolean(data.captcha_retry_at);document.querySelector("#refresh").disabled=data.refreshing||cooling||guarded||Boolean(data.configuration_error);
 document.querySelector("#notice").textContent=data.configuration_error?"設定尚未通過驗證":data.refreshing?"正在查詢監理服務網…":
  cooling?"監理服務網目前無法連線，將於 "+time(data.cooldown_until)+" 自動重試":
  captchaRetry?"驗證碼判讀未達可靠門檻，將於 "+time(data.captcha_retry_at)+" 自動補查":
  "最近更新："+time(data.last_refresh_at)+"　下次更新："+time(data.next_refresh_at);
 renderOverview(data);renderSchedule(data);const root=document.querySelector("#people");
 if(data.configuration_error){root.replaceChildren();const card=el("article",undefined,"person-card full-width");
  card.append(el("h2","請修正查詢人設定"),el("div",data.configuration_error,"error-box"),
   el("div","請到 Add-on 的「設定」修正後按儲存；系統會在約 5 秒內自動驗證並查詢，不必重新啟動。","empty"));
  root.append(card);document.querySelector("#result-count").textContent="顯示 0 / 0 人";return}renderPeople(data)}
async function api(path,options={}){const response=await fetch(path,{cache:"no-store",...options});
 if(!response.ok&&response.status!==409)throw new Error(String(response.status));return response.json()}
async function load(){try{render(await api("api/status"))}catch{document.querySelector("#notice").textContent="無法載入狀態"}}
document.querySelector("#search").addEventListener("input",()=>latestData&&renderPeople(latestData));
document.querySelector("#status-filter").addEventListener("change",()=>latestData&&renderPeople(latestData));
document.querySelector("#refresh").addEventListener("click",async event=>{event.currentTarget.disabled=true;
 try{await api("api/refresh",{method:"POST",headers:{"X-Requested-With":"XMLHttpRequest"}});await load()}
 catch{document.querySelector("#notice").textContent="無法啟動查詢"}});
async function testNotification(key,button){button.disabled=true;try{await api("api/test-notification",{method:"POST",
 headers:{"Content-Type":"application/json","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify({profile_key:key})});
 button.textContent="已傳送"}catch{button.textContent="傳送失敗"}finally{setTimeout(()=>{button.disabled=false;button.textContent="傳送測試通知"},1800)}}
load();setInterval(load,5000);
</script></body></html>"""
