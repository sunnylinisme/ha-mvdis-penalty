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
    :root { color-scheme: light dark; --bg:#f4f6f8; --card:#fff; --text:#17202a;
      --muted:#657786; --line:#d9e0e6; --accent:#1675d1; --ok:#198754;
      --warn:#c77700; --bad:#c62828; }
    @media (prefers-color-scheme:dark) { :root { --bg:#101315; --card:#1b1f22;
      --text:#edf2f5; --muted:#a8b3bb; --line:#333b40; --accent:#63aef2; } }
    * { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--text);
      font:15px/1.5 system-ui,-apple-system,"Noto Sans TC",sans-serif }
    main { max-width:1100px; margin:auto; padding:24px }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px;
      margin-bottom:20px } h1 { font-size:24px; margin:0 } .actions { display:flex; gap:8px }
    button { border:0; border-radius:10px; padding:10px 15px; font-weight:650;
      cursor:pointer; background:var(--accent); color:white } button.secondary {
      background:transparent; color:var(--accent); border:1px solid var(--accent) }
    button:disabled { opacity:.5; cursor:wait } #notice { min-height:24px; color:var(--muted) }
    .grid { display:grid; gap:16px } .card { background:var(--card); border:1px solid var(--line);
      border-radius:14px; padding:18px; box-shadow:0 2px 8px #0000000d }
    .card-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px }
    h2 { margin:0; font-size:20px } .badge { border-radius:999px; padding:4px 9px;
      font-size:12px; font-weight:700; background:#6b728020 } .ok { color:var(--ok) }
    .error { color:var(--bad) } .waiting { color:var(--warn) }
    .metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px;
      margin:16px 0 } .metric { padding:12px; border-radius:10px; background:var(--bg) }
    .metric strong { display:block; font-size:22px } .metric span,.muted { color:var(--muted) }
    table { width:100%; border-collapse:collapse; margin-top:12px; font-size:14px }
    th,td { text-align:left; padding:9px 7px; border-bottom:1px solid var(--line);
      vertical-align:top } th { color:var(--muted) } .empty { padding:18px 0; color:var(--muted) }
    .error-box { margin-top:12px; padding:10px; border-radius:8px; background:#c6282815 }
    @media(max-width:650px) { main{padding:15px} header{align-items:flex-start;flex-direction:column}
      .metrics{grid-template-columns:1fr} .table-wrap{overflow:auto} }
  </style>
</head>
<body><main>
  <header><div><h1>監理站罰單通知</h1><div id="notice">載入中…</div></div>
    <div class="actions"><button id="refresh">立即查詢</button></div></header>
  <section id="people" class="grid"></section>
</main>
<script>
const errorNames={waiting:"等待第一次查詢",captcha:"驗證碼辨識失敗",identity:"身分資料遭拒",
 response_format:"監理站回傳格式無法辨識",timeout:"監理站連線逾時",network:"網路連線失敗",
 http:"監理站服務錯誤",unknown:"未知錯誤"};
const money=v=>new Intl.NumberFormat("zh-TW").format(v||0);
const time=v=>v?new Date(v).toLocaleString("zh-TW",{hour12:false}):"尚未查詢";
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;
 if(cls)n.className=cls;return n}
function metric(label,value){const box=el("div",undefined,"metric");box.append(el("strong",value),el("span",label));return box}
function render(data){
 const root=document.querySelector("#people");root.replaceChildren();
 document.querySelector("#refresh").disabled=data.refreshing;
 document.querySelector("#notice").textContent=data.configuration_error?"設定錯誤："+data.configuration_error:
  (data.refreshing?"正在查詢監理服務網…":"每頁會自動更新狀態");
 for(const person of data.people){
  const card=el("article",undefined,"card"),head=el("div",undefined,"card-head"),title=el("div");
  title.append(el("h2",person.name),el("div","最後查詢："+time(person.checked_at),"muted"));
  const status=person.error?errorNames[person.error_type]||"查詢錯誤":"查詢成功";
  head.append(title,el("span",status,"badge "+(person.error?(person.error_type==="waiting"?"waiting":"error"):"ok")));
  card.append(head);
  const penalties=Array.isArray(person.penalties)?person.penalties:[];
  const total=penalties.reduce((sum,p)=>sum+(Number(p.amount)||0),0),metrics=el("div",undefined,"metrics");
  metrics.append(metric("未繳筆數",String(penalties.length)),metric("辨識金額","NT$ "+money(total)),
    metric("目前狀態",penalties.length?"有未繳紀錄":"查無資料"));card.append(metrics);
  if(person.error){card.append(el("div",errorNames[person.error_type]||person.error,"error-box"))}
  if(!penalties.length){card.append(el("div","目前沒有可顯示的罰單內容。","empty"))}
  else {
   const wrap=el("div",undefined,"table-wrap"),table=el("table"),body=el("tbody");
   for(const [index,penalty] of penalties.entries()){
    const heading=el("tr"),headingCell=el("th","第 "+(index+1)+" 筆");headingCell.colSpan=2;
    heading.append(headingCell);body.append(heading);
    const details=Object.entries(penalty.details||{});
    if(!details.length)details.push(["內容",penalty.summary||"交通違規罰單"]);
    for(const [key,value] of details){
    const row=el("tr");row.append(el("th",key),el("td",String(value)));body.append(row)}
    const gap=el("tr");const cell=el("td","");cell.colSpan=2;gap.append(cell);body.append(gap)}
   table.append(body);wrap.append(table);card.append(wrap)
  }
  const test=el("button","傳送測試通知","secondary");test.addEventListener("click",()=>testNotification(person.key,test));card.append(test);root.append(card)
 }
}
async function api(path,options={}){const response=await fetch(path,{cache:"no-store",...options});
 if(!response.ok&&response.status!==409)throw new Error(String(response.status));return response.json()}
async function load(){try{render(await api("api/status"))}catch{document.querySelector("#notice").textContent="無法載入狀態"}}
document.querySelector("#refresh").addEventListener("click",async event=>{event.currentTarget.disabled=true;
 try{await api("api/refresh",{method:"POST",headers:{"X-Requested-With":"XMLHttpRequest"}});await load()}catch{document.querySelector("#notice").textContent="無法啟動查詢"}});
async function testNotification(key,button){button.disabled=true;try{await api("api/test-notification",{method:"POST",
 headers:{"Content-Type":"application/json","X-Requested-With":"XMLHttpRequest"},body:JSON.stringify({profile_key:key})});
 button.textContent="已傳送"}catch{button.textContent="傳送失敗"}finally{setTimeout(()=>{button.disabled=false;button.textContent="傳送測試通知"},1800)}}
load();setInterval(load,5000);
</script></body></html>"""
