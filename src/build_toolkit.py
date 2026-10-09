import html, json, re, textwrap
from kql_library import *

import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "index.html")
VERSION = "2.0.1"
UPDATED = "October 8, 2026"
esc = html.escape

FN_KEYS = [k for k, _, _ in FUNCS]            # traces, tools, tokens, cost
FN_NAME = {k: n for k, n, _ in FUNCS}
FN_TEXT = {k: t for k, _, t in FUNCS}


def highlight(code_escaped):
    return re.sub(r'(^|\s)(//[^\n]*)', lambda m: m.group(1) + '<span class="cm">' + m.group(2) + '</span>', code_escaped, flags=re.M)


def uses_of(text):
    """Helper functions a query calls, closed over function-to-function dependencies, in definition order."""
    found = {k for k in FN_KEYS if re.search(r'\b' + FN_NAME[k] + r'\(', text)}
    if "cost" in found:
        found.add("tokens")
    return [k for k in FN_KEYS if k in found]


def code_variants(text, uses=(), runnable=False, needs=()):
    blocks = ""
    for v in ("classic", "ws"):
        blocks += f'<pre class="code" data-variant="{v}"><code>{highlight(esc(render(text, v)))}</code></pre>'
    btns = '<button class="btn copy" data-copy="plain" type="button">Copy</button>'
    if uses:
        btns += f'<button class="btn copy ghost" data-copy="inline" data-uses="{",".join(uses)}" type="button" title="Prepends the helper function definitions so the query runs on its own">Copy with functions inlined</button>'
    if runnable:
        btns += f'<button class="btn copy ghost" data-copy="runnable" data-needs="{",".join(needs)}" type="button" title="Adds let lookback = 7d; so the body runs as a normal query without saving it as a function">Copy as runnable query (lookback = 7d)</button>'
    return f'<div class="codewrap">{blocks}<div class="codebar">{btns}</div></div>'


def fn_body(text):
    lines = text.split("\n")
    return textwrap.dedent("\n".join(lines[1:-1]))


def platform_pills(pl):
    return "".join(f'<span class="pill">{esc(p)}</span>' for p in pl)


def schema_note(q, uses):
    if render(q, "classic") != render(q, "ws"):
        return '<div class="schema-note differs"><b>Changes with the table-name switch.</b> This query reads the tables directly, so the names differ between Application Insights and Log Analytics.</div>'
    if uses:
        return '<div class="schema-note same"><b>Identical in both schemas.</b> It only calls the helper functions, which hold all the table names. The switch matters when you save or inline the functions.</div>'
    return '<div class="schema-note same"><b>Identical in both schemas.</b></div>'


def card(h, alert=False):
    uses = uses_of(h["q"])
    notes = "".join(f"<li>{esc(t)}</li>" for t in h["notes"])
    notes = f'<div class="notes"><div class="lbl">Notes</div><ul>{notes}</ul></div>' if notes else ""
    rule = ""
    if alert:
        r = h["rule"]
        rule = f'''<div class="rule-grid">
    <div><span class="lbl">Run every</span>{esc(r['freq'].replace('Every ', ''))}</div>
    <div><span class="lbl">Query window (lookback)</span>{esc(r['window'])}</div>
    <div><span class="lbl">Alert when</span>Number of results greater than 0</div>
    <div><span class="lbl">Split by dimension</span>{esc(r['split'])}</div>
    <div><span class="lbl">Suggested severity</span>{esc(r['sev'])}</div>
  </div>'''
    search = esc((h["title"] + " " + h["desc"]).lower())
    return f'''<article class="q-card" id="q-{h['id']}" data-search="{search}">
  <header><span class="qid">{h['id']}</span><h4>{esc(h['title'])}</h4></header>
  <div class="pills">{platform_pills(h['platforms'])}</div>
  <p>{esc(h['desc'])}</p>
  {schema_note(h['q'], uses)}
  {code_variants(h['q'], uses)}
  {rule}
  {notes}
</article>'''


def cards(lst, alert=False):
    return "".join(card(h, alert) for h in lst)


def fn_block(key):
    needs = ["tokens"] if key == "cost" else []
    return code_variants(fn_body(FN_TEXT[key]), (), runnable=True, needs=needs)


def plain_code(text):
    return f'<div class="codewrap static"><pre class="code static"><code>{highlight(esc(text))}</code></pre><div class="codebar"><button class="btn copy" data-copy="static" type="button">Copy</button></div></div>'


foundry_code = '''# pip install azure-ai-projects azure-identity opentelemetry-sdk azure-core-tracing-opentelemetry azure-monitor-opentelemetry
import os
os.environ["AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING"] = "true"
# Records prompts, responses and tool arguments. Only enable where your privacy and compliance rules allow it.
os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "true"

from azure.ai.projects import AIProjectClient
from azure.ai.projects.telemetry import AIProjectInstrumentor
from azure.monitor.opentelemetry import configure_azure_monitor

AIProjectInstrumentor().instrument()
connection_string = project.telemetry.get_application_insights_connection_string()   # 'project' is your AIProjectClient
configure_azure_monitor(connection_string=connection_string)
# Pass agent_reference with both name and id in responses.create() so spans carry the agent identity.'''

custom_code = '''# pip install azure-monitor-opentelemetry
import json, os
os.environ["OTEL_SERVICE_NAME"] = "my-custom-agent-app"       # becomes cloud_RoleName / AppRoleName
os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "true"   # only where policy allows
from azure.monitor.opentelemetry import configure_azure_monitor   # reads APPLICATIONINSIGHTS_CONNECTION_STRING
from opentelemetry import trace

configure_azure_monitor()
tracer = trace.get_tracer("my-custom-agent")

def handle_turn(conversation_id, user_id, user_text):
    # Default span kind (INTERNAL) lands in the dependencies table. SERVER spans would land in requests instead.
    with tracer.start_as_current_span("invoke_agent my-agent") as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.provider.name", "azure.ai.openai")
        span.set_attribute("gen_ai.agent.name", "my-agent")
        span.set_attribute("gen_ai.conversation.id", conversation_id)
        span.set_attribute("enduser.id", user_id)                 # typically surfaces as user_AuthenticatedId
        span.set_attribute("gen_ai.input.messages",
            json.dumps([{"role": "user", "parts": [{"type": "text", "content": user_text}]}]))

        with tracer.start_as_current_span("execute_tool lookup_order") as tool:
            tool.set_attribute("gen_ai.operation.name", "execute_tool")
            tool.set_attribute("gen_ai.tool.name", "lookup_order")
            tool.set_attribute("gen_ai.tool.type", "function")
            tool.set_attribute("gen_ai.tool.call.arguments", json.dumps({"order_id": "123"}))
            result = {"status": "shipped"}                         # call your tool here
            tool.set_attribute("gen_ai.tool.call.result", json.dumps(result))

        with tracer.start_as_current_span("chat gpt-4o") as llm:   # one span per model call: this is what AgentTokens reads
            llm.set_attribute("gen_ai.operation.name", "chat")
            llm.set_attribute("gen_ai.request.model", "gpt-4o")
            answer = "Your order has shipped."                     # call your model here
            llm.set_attribute("gen_ai.usage.input_tokens", 812)    # take both from the model response's usage object
            llm.set_attribute("gen_ai.usage.output_tokens", 24)
        span.set_attribute("gen_ai.output.messages",
            json.dumps([{"role": "assistant", "parts": [{"type": "text", "content": answer}]}]))
        return answer'''


def plain_code(text):
    return f'<div class="codewrap static"><pre class="code static"><code>{highlight(esc(text))}</code></pre><div class="codebar"><button class="btn copy" data-copy="static" type="button">Copy</button></div></div>'

CSS = r'''
:root{
  --bg:#0a0e1a; --surface:#131a2c; --surface-2:#0e1420; --border:#242c42; --border-strong:#39415e;
  --text:#eef1fb; --text-muted:#a3acc9; --text-faint:#6d7593;
  --observe:#5ea1ff; --govern:#b98bff; --secure:#ff9382;
  --accent:linear-gradient(135deg,#5b7cff 0%,#9a5bff 100%);
  --code-bg:#0b1020; --cm:#6d7593;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
}
:root[data-theme="light"]{
  --bg:#f6f7fb; --surface:#ffffff; --surface-2:#f0f2f8; --border:#dde1ee; --border-strong:#c4cadf;
  --text:#161a2b; --text-muted:#4a5270; --text-faint:#7b84a3; --code-bg:#f3f5fb; --cm:#7b84a3;
}
*{box-sizing:border-box} html{scroll-behavior:smooth;scroll-padding-top:110px}
body{margin:0;background:var(--bg);color:var(--text);font-size:14.5px;line-height:1.6;
  background-image:radial-gradient(1100px 480px at 12% -10%,rgba(91,124,255,.18),transparent 60%),radial-gradient(900px 480px at 100% 0%,rgba(154,91,255,.14),transparent 55%);background-attachment:fixed}
a{color:#8fb8ff} :root[data-theme="light"] a{color:#2a5bd7}
h1,h2,h3,h4{margin:0;font-weight:600;line-height:1.25}
header.top{padding:26px 28px 18px;border-bottom:1px solid var(--border);background:rgba(16,21,36,.55);backdrop-filter:blur(8px)}
:root[data-theme="light"] header.top{background:rgba(255,255,255,.7)}
header.top h1{font-size:26px;background:var(--accent);-webkit-background-clip:text;background-clip:text;color:transparent;display:inline-block}
header.top .sub{color:var(--text-muted);margin-top:6px;font-size:13.5px}
.notice{margin:16px 28px 0;padding:12px 16px;border:1px solid var(--border-strong);border-left:3px solid #9a5bff;border-radius:9px;background:var(--surface);color:var(--text-muted);font-size:13px}
.notice b{color:var(--text)}
.bar{position:sticky;top:0;z-index:20;display:flex;gap:14px;flex-wrap:wrap;align-items:center;padding:10px 28px;background:rgba(10,14,26,.92);backdrop-filter:blur(10px);border-bottom:1px solid var(--border)}
:root[data-theme="light"] .bar{background:rgba(246,247,251,.94)}
.bar .lbl{font-size:12px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.05em}
.seg{display:inline-flex;border:1px solid var(--border-strong);border-radius:8px;overflow:hidden}
.seg button{background:transparent;color:var(--text-muted);border:0;padding:6px 12px;font-size:13px;cursor:pointer;font-family:inherit}
.seg button.on{background:var(--accent);color:#fff}
.bar input[type=search]{flex:1;min-width:180px;max-width:340px;margin-left:auto;background:var(--surface-2);color:var(--text);border:1px solid var(--border-strong);border-radius:8px;padding:7px 10px;font-family:inherit;font-size:13px}
.bar .theme{background:transparent;color:var(--text-muted);border:1px solid var(--border-strong);border-radius:8px;padding:6px 10px;cursor:pointer;font-family:inherit;font-size:13px}
.layout{display:grid;grid-template-columns:250px minmax(0,1fr);gap:0;max-width:1400px;margin:0 auto}
nav.toc{position:sticky;top:58px;align-self:start;max-height:calc(100vh - 66px);overflow:auto;padding:22px 18px 40px 28px;font-size:13px}
nav.toc .g{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint);margin:16px 0 6px}
nav.toc a{display:block;color:var(--text-muted);text-decoration:none;padding:3px 0}
nav.toc a:hover{color:var(--text)}
nav.toc a.sub{padding-left:12px;font-size:12.5px}
main{padding:24px 28px 80px;min-width:0}
section.sec{margin-bottom:46px}
section.sec>h2{font-size:21px;margin-bottom:6px;padding-bottom:10px;border-bottom:1px solid var(--border)}
section.sec>.lead{color:var(--text-muted);margin:10px 0 18px}
h3{font-size:16.5px;margin:26px 0 8px} h3.cat{margin-top:34px;color:var(--text)} .cat-blurb{margin:0 0 14px;color:var(--text-muted)}
h4{font-size:15px}
p{margin:8px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px;margin:14px 0}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.card h4{margin-bottom:6px}.card p,.card li{color:var(--text-muted);font-size:13.5px}
.card ul{margin:6px 0 0;padding-left:18px}
.flow{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch;margin:16px 0}
.flow .node{flex:1 1 150px;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font-size:13px}
.flow .node b{display:block;margin-bottom:3px}.flow .node span{color:var(--text-muted);font-size:12.5px}
.flow .arrow{align-self:center;color:var(--text-faint)}
.steps{counter-reset:s;list-style:none;padding:0;margin:12px 0}
.steps>li{counter-increment:s;position:relative;padding:8px 0 8px 40px;color:var(--text-muted)}
.steps>li::before{content:counter(s);position:absolute;left:0;top:8px;width:26px;height:26px;border-radius:50%;background:var(--surface);border:1px solid var(--border-strong);color:var(--text);font-size:12.5px;display:flex;align-items:center;justify-content:center}
.steps b{color:var(--text)}
code.i{background:var(--surface-2);border:1px solid var(--border);border-radius:5px;padding:1px 5px;font-size:12.5px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
table.t{width:100%;border-collapse:collapse;margin:12px 0;font-size:13px}
table.t th{text-align:left;color:var(--text-faint);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;padding:8px 10px;border-bottom:1px solid var(--border-strong)}
table.t td{padding:8px 10px;border-bottom:1px solid var(--border);vertical-align:top;color:var(--text-muted)}
table.t td:first-child{color:var(--text)}
.scroll{overflow-x:auto}
.warn{border:1px solid #5a4630;border-left:3px solid #f0a35c;background:rgba(240,163,92,.07);border-radius:9px;padding:10px 14px;margin:14px 0;color:var(--text-muted);font-size:13.5px}
.warn b{color:var(--text)}
.info{border:1px solid var(--border-strong);border-left:3px solid var(--observe);background:var(--surface);border-radius:9px;padding:10px 14px;margin:14px 0;color:var(--text-muted);font-size:13.5px}
.info b{color:var(--text)}
.q-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin:14px 0}
.q-card header{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.q-card header h4{flex:1 1 240px}
.qid{font-size:12px;font-weight:600;color:var(--text-faint);background:var(--surface-2);border:1px solid var(--border);border-radius:6px;padding:2px 8px}
.pills{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px}
.pill{font-size:11.5px;color:var(--text-muted);border:1px solid var(--border);background:var(--surface-2);border-radius:999px;padding:1px 9px}
.sev{font-size:11.5px;font-weight:600;border-radius:999px;padding:2px 10px;border:1px solid}
.sev-high{color:#ffb7ab;border-color:#7a3a36;background:rgba(255,147,130,.1)}
.sev-med{color:#ffd28a;border-color:#6b5226;background:rgba(240,163,92,.1)}
.sev-low{color:#9cc7ff;border-color:#27456b;background:rgba(94,161,255,.1)}
.sev-info{color:var(--text-muted);border-color:var(--border-strong);background:var(--surface-2)}
:root[data-theme="light"] .sev-high{color:#a3261a}:root[data-theme="light"] .sev-med{color:#8a5a00}:root[data-theme="light"] .sev-low{color:#1f56b8}
.codewrap{position:relative;margin:12px 0}
.codewrap .code{display:none;margin:0;background:var(--code-bg);border:1px solid var(--border);border-radius:9px;padding:14px 16px;overflow:auto;max-height:520px;font-size:12.5px;line-height:1.55;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--text)}
body[data-schema="classic"] .codewrap .code[data-variant="classic"]{display:block}
body[data-schema="ws"] .codewrap .code[data-variant="ws"]{display:block}
.codewrap.static .code{display:block}
.code .cm{color:var(--cm)}
.codebar{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.btn{background:var(--accent);color:#fff;border:0;border-radius:7px;padding:6px 12px;font-size:12.5px;cursor:pointer;font-family:inherit}
.btn.ghost{background:transparent;color:var(--text-muted);border:1px solid var(--border-strong)}
.btn.done{background:#1f7a4d !important;color:#fff !important;border-color:transparent !important}
.notes{margin-top:10px}.notes ul{margin:4px 0 0;padding-left:18px;color:var(--text-muted);font-size:13px}.notes li{margin:2px 0}
.lbl{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--text-faint);margin-bottom:2px}
.rule-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px 18px;margin:12px 0 4px;padding:12px 14px;background:var(--surface-2);border:1px solid var(--border);border-radius:9px;font-size:13px;color:var(--text-muted)}
.schema-note{font-size:12.5px;border-radius:8px;padding:6px 10px;margin:8px 0 0;border:1px solid var(--border);background:var(--surface-2);color:var(--text-muted)}
.schema-note b{color:var(--text)}
.schema-note.differs{border-left:3px solid #f0a35c}
.schema-note.same{border-left:3px solid var(--border-strong)}
.hidden{display:none !important}
footer{max-width:1400px;margin:0 auto;padding:10px 28px 50px;color:var(--text-faint);font-size:12.5px}
@media (max-width:980px){.layout{grid-template-columns:1fr}nav.toc{display:none}.bar input[type=search]{margin-left:0;max-width:none}}
'''

JS = r'''
(function(){
  var FUNCS = __FUNCS__;
  var ORDER = ['traces','tools','tokens','cost'];
  var body = document.body;
  function setSchema(s){
    body.setAttribute('data-schema', s);
    document.querySelectorAll('.seg button').forEach(function(b){ b.classList.toggle('on', b.getAttribute('data-schema')===s); });
    try{ localStorage.setItem('obs_toolkit_schema', s); }catch(e){}
  }
  var saved = 'classic'; try{ saved = localStorage.getItem('obs_toolkit_schema') || 'classic'; }catch(e){}
  setSchema(saved==='ws' ? 'ws' : 'classic');
  document.querySelectorAll('.seg button').forEach(function(b){ b.addEventListener('click', function(){ setSchema(b.getAttribute('data-schema')); }); });

  var themeBtn = document.getElementById('themeBtn');
  function setTheme(t){ document.documentElement.setAttribute('data-theme', t); themeBtn.textContent = t==='light' ? 'Dark mode' : 'Light mode'; try{ localStorage.setItem('obs_toolkit_theme', t); }catch(e){} }
  var th = 'dark'; try{ th = localStorage.getItem('obs_toolkit_theme') || 'dark'; }catch(e){}
  setTheme(th);
  themeBtn.addEventListener('click', function(){ setTheme(document.documentElement.getAttribute('data-theme')==='light' ? 'dark' : 'light'); });

  function visibleCode(wrap){
    var s = body.getAttribute('data-schema');
    var el = wrap.querySelector('.code[data-variant="'+s+'"]') || wrap.querySelector('.code');
    return el.innerText;
  }
  function defs(list){
    var s = body.getAttribute('data-schema');
    return ORDER.filter(function(k){ return list.indexOf(k) !== -1; }).map(function(k){ return FUNCS[s][k]; }).join('\n');
  }
  function copyText(text, btn){
    function ok(){ var old = btn.textContent; btn.textContent = 'Copied'; btn.classList.add('done'); setTimeout(function(){ btn.textContent = old; btn.classList.remove('done'); }, 1400); }
    function fallback(){
      var ta = document.createElement('textarea'); ta.value = text; ta.style.position='fixed'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.select();
      try{ document.execCommand('copy'); ok(); }catch(e){ btn.textContent = 'Press Ctrl+C'; }
      document.body.removeChild(ta);
    }
    if(navigator.clipboard && window.isSecureContext){ navigator.clipboard.writeText(text).then(ok, fallback); } else { fallback(); }
  }
  document.querySelectorAll('button.copy').forEach(function(btn){
    btn.addEventListener('click', function(){
      var wrap = btn.closest('.codewrap');
      var text = visibleCode(wrap), mode = btn.getAttribute('data-copy');
      if(mode==='inline'){
        text = defs(btn.getAttribute('data-uses').split(',')) + '\n' + text;
      }
      if(mode==='runnable'){
        var needs = (btn.getAttribute('data-needs')||'').split(',').filter(Boolean);
        text = (needs.length ? defs(needs) + '\n' : '') + 'let lookback = 7d;\n' + text;
      }
      window.__lastCopy = text;
      copyText(text, btn);
    });
  });

  var search = document.getElementById('q');
  search.addEventListener('input', function(){
    var term = search.value.trim().toLowerCase();
    document.querySelectorAll('.q-card').forEach(function(c){
      c.classList.toggle('hidden', term && c.getAttribute('data-search').indexOf(term) === -1);
    });
  });
})();
'''

funcs_js = {v: {k: render(FN_TEXT[k], v) for k in FN_KEYS} for v in ("classic", "ws")}
JS = JS.replace("__FUNCS__", json.dumps(funcs_js))

toc = '''
<div class="g">Start here</div>
<a href="#overview">Overview</a><a href="#connect">1. Connect telemetry</a><a href="#functions">2. Save the helper functions</a><a class="sub" href="#troubleshoot">If a query fails</a><a href="#verify">3. Verify your data</a>
<div class="g">Queries</div>
<a href="#prompts">4. Prompts</a><a href="#tools">5. Tool calls</a><a href="#cost">6. Token usage and cost</a>
<div class="g">Operate</div>
<a href="#alerts">7. Operational alerts</a><a href="#privacy">8. Privacy and access</a><a href="#reference">9. Reference</a>
'''

fn_cards = "".join(
    f'<h3>{FN_NAME[k]} (function body)</h3>{fn_block(k)}' for k in FN_KEYS)

page = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Observability Toolkit</title>
<style>{CSS}</style></head>
<body data-schema="classic">
<header class="top">
  <h1>Microsoft Agent 365 · Agent Observability Toolkit</h1>
  <div class="sub">KQL for prompts, tool calls and token cost across your agents in Azure Application Insights and Log Analytics · Version {VERSION} · Last updated {UPDATED}</div>
</header>
<div class="notice"><b>About this toolkit.</b> It is built from official Microsoft documentation and the OpenTelemetry GenAI semantic conventions. The queries have been reviewed against the documented schemas but have <b>not been run against a live tenant</b>. This toolkit is a proof-of-concept aid and is not production-ready: you may encounter errors. Validate every query in a test workspace and tune thresholds on your own data before enabling any alert.</div>
<div class="bar">
  <span class="lbl">Table names</span>
  <div class="seg" role="group" aria-label="Table names">
    <button type="button" data-schema="classic" class="on">Application Insights</button>
    <button type="button" data-schema="ws">Log Analytics workspace</button>
  </div>
  <input id="q" type="search" placeholder="Filter queries" aria-label="Filter queries">
  <button id="themeBtn" class="theme" type="button">Light mode</button>
</div>
<div class="layout">
<nav class="toc">{toc}</nav>
<main>

<section class="sec" id="overview">
  <h2>Overview</h2>
  <p class="lead">The Agent 365 admin center gives you inventory and governance. The traces themselves (what users asked, which tools the agents called, how many tokens each answer cost) live in Azure Application Insights. This toolkit turns them into answers for platform and FinOps teams.</p>
  <div class="cards">
    <div class="card"><h4>What was asked</h4><ul><li>Recent and most common prompts</li><li>Full conversation transcripts</li><li>Conversation length, prompt size, unanswered turns</li></ul></div>
    <div class="card"><h4>What the agents did</h4><ul><li>Which tools each agent calls, and in what order</li><li>Arguments, results, latency and failures</li><li>Tool calls per prompt, new and vanished tools</li></ul></div>
    <div class="card"><h4>What it cost</h4><ul><li>Tokens per agent, model, user and day</li><li>Estimated cost per prompt and most expensive prompts</li><li>Budget and spike alerts</li></ul></div>
  </div>
  <div class="info"><b>Scope.</b> This toolkit is for observability and cost. Security detections such as prompt injection and sensitive data in prompts are deliberately out of scope: use Microsoft Defender and Microsoft Purview DSPM for those.</div>
  <div class="flow">
    <div class="node"><b>Agents</b><span>Copilot Studio, Foundry, custom on Web App or Container</span></div><div class="arrow">&rarr;</div>
    <div class="node"><b>Application Insights</b><span>Workspace-based, in a Log Analytics workspace</span></div><div class="arrow">&rarr;</div>
    <div class="node"><b>Four functions</b><span>AgentTraces, AgentToolCalls, AgentTokens, AgentCost normalise every platform</span></div><div class="arrow">&rarr;</div>
    <div class="node"><b>Queries and alerts</b><span>Explore, report, then alert with Azure Monitor</span></div>
  </div>
  <ol class="steps">
    <li><b>Connect telemetry</b> from each platform to a workspace-based Application Insights resource (section 1).</li>
    <li><b>Save the helper functions</b> once. Every other query builds on them (section 2).</li>
    <li><b>Verify what your telemetry contains</b> (section 3). It tells you which questions your setup can answer.</li>
    <li><b>Use the prompt, tool and cost queries</b> (sections 4 to 6), and turn the useful ones into alerts (section 7).</li>
  </ol>
  <div class="info"><b>Choose your table names with the switch above.</b> The Application Insights Logs blade uses <code class="i">customEvents</code>, <code class="i">dependencies</code> and <code class="i">customDimensions</code>. A Log Analytics workspace uses <code class="i">AppEvents</code>, <code class="i">AppDependencies</code> and <code class="i">Properties</code>. Every query is rendered for both and the copy buttons copy the one you selected. <b>Run each version in the matching place.</b> Most queries only call the helper functions, so they are identical in both schemas; the table names live in the functions.</div>
  <div class="warn"><b>What each platform can answer.</b> Prompts and responses: all three, when content capture is on. Tool calls: Foundry, custom agents and Copilot Studio environment-level telemetry (preview), not Copilot Studio agent-level telemetry. Tokens: Foundry and custom agents that emit <code class="i">gen_ai.usage.*</code>. Copilot Studio is licensed on messages or credits rather than tokens, so token cost is generally not available for it. Run V3 to see exactly what you have.</div>
</section>

<section class="sec" id="connect">
  <h2>1. Connect telemetry</h2>
  <p class="lead">Use one workspace-based Application Insights resource for all agents in an environment, so a single set of functions covers everything. Prompt and tool content is only captured if you enable it. It is sensitive, so read section 8 first.</p>

  <h3>Copilot Studio</h3>
  <div class="cards">
    <div class="card"><h4>Agent-level telemetry (per agent)</h4>
      <ul><li>Agent <b>Settings &rarr; Advanced &rarr; Application Insights</b>, paste the connection string.</li>
      <li><b>Enable logging</b> records incoming and outgoing messages and events.</li>
      <li><b>Log conversation details</b> adds user ID, user name and the message text.</li>
      <li><b>Log sensitive Activity properties</b> and <b>Node execution events</b> are optional.</li>
      <li>Lands in <code class="i">customEvents</code> as BotMessageReceived and BotMessageSend. Gives prompts and responses, not tool calls or tokens.</li></ul></div>
    <div class="card"><h4>Environment-level telemetry (preview)</h4>
      <ul><li>Configured centrally by an administrator, span-based and aligned with OpenTelemetry.</li>
      <li>Lands in <code class="i">dependencies</code> as invoke_agent, execute_tool and output_messages spans with <code class="i">gen_ai.*</code> attributes, including tool arguments and results.</li>
      <li>Managed environments only. Declarative agents are not included. Large content can be truncated.</li>
      <li>This is the source for the Copilot Studio tool-call queries.</li></ul></div>
  </div>

  <h3>Microsoft Foundry agents</h3>
  {plain_code(foundry_code)}
  <div class="warn"><b>Content recording captures sensitive data.</b> Microsoft's guidance is to enable it in development, and in production only where your compliance and privacy requirements allow it. Without it you still get spans, timings, tool names and token counts, which is enough for the tool and cost queries, but not the prompt text.</div>

  <h3>Custom agents on Azure Web Apps or Containers</h3>
  <p>Use the Azure Monitor OpenTelemetry distro and emit GenAI spans. Agent frameworks that already emit them (for example Semantic Kernel or Microsoft Agent Framework) only need <code class="i">configure_azure_monitor()</code> and the connection string. For hand-built agents, this minimal pattern produces the spans the toolkit expects. Add a <code class="i">chat</code> span per model call with <code class="i">gen_ai.usage.input_tokens</code> and <code class="i">gen_ai.usage.output_tokens</code> if you want token queries to work:</p>
  {plain_code(custom_code)}
  <p>Set <code class="i">OTEL_SERVICE_NAME</code> (or the role name) per agent. Add the names of agents you host yourself to <code class="i">customAgentRoles</code> in the AgentTraces function so they are labelled Custom rather than Foundry.</p>

  <h3>What each source gives you</h3>
  <div class="scroll"><table class="t"><thead><tr><th>Source</th><th>Table</th><th>Prompts and responses</th><th>Tool calls</th><th>Tokens</th></tr></thead><tbody>
    <tr><td>Copilot Studio, agent-level</td><td>customEvents</td><td>Yes, when conversation details are logged</td><td>No</td><td>No</td></tr>
    <tr><td>Copilot Studio, environment-level (preview)</td><td>dependencies</td><td>Yes (gen_ai.input.messages / output.messages)</td><td>Yes, with arguments and results</td><td>If emitted</td></tr>
    <tr><td>Foundry agents</td><td>dependencies</td><td>Yes, with content recording</td><td>Yes</td><td>Yes</td></tr>
    <tr><td>Custom agents</td><td>dependencies</td><td>Yes, if you emit it</td><td>Yes, if you emit it</td><td>Yes, if you emit it</td></tr>
  </tbody></table></div>
</section>

<section class="sec" id="functions">
  <h2>2. Save the helper functions</h2>
  <p class="lead">Four functions normalise every platform into one shape. <b>AgentTraces</b>: one row per prompt or response. <b>AgentToolCalls</b>: one row per tool call. <b>AgentTokens</b>: one row per model call with input and output tokens. <b>AgentCost</b>: AgentTokens priced with your own price table.</p>
  <ol class="steps">
    <li>Open <b>Logs</b> in the place that matches the table-name switch: the <b>Application Insights resource</b> for Application Insights names, or the <b>Log Analytics workspace</b> for Log Analytics names.</li>
    <li>Paste a <b>function body</b> below (it needs the <code class="i">lookback</code> parameter, so it is not a runnable query by itself), choose <b>Save &rarr; Save as function</b>, name it exactly as shown, and add one parameter <code class="i">lookback</code> of type <code class="i">timespan</code>.</li>
    <li>Repeat for all four. Save <b>AgentTokens before AgentCost</b>, because AgentCost calls it.</li>
    <li>Edit the settings at the top of each: <code class="i">traceOps</code> and <code class="i">customAgentRoles</code> in AgentTraces, <code class="i">tokenOps</code> in AgentTokens, and <b>the price table in AgentCost</b>.</li>
  </ol>
  <div class="schema-note differs"><b>Changes with the table-name switch.</b> The helper functions are where the table names live. Use the version that matches where you save them.</div>
  {fn_cards}
  <div class="info"><b>Prefer not to save functions?</b> Every query that uses them has a <b>Copy with functions inlined</b> button that prepends the definitions so the query runs on its own. In the AgentCost price table, set the real prices before relying on any cost figure.</div>
  <h3 id="troubleshoot">If a query fails</h3>
  <div class="scroll"><table class="t"><thead><tr><th>Message or symptom</th><th>Likely cause and fix</th></tr></thead><tbody>
    <tr><td>Failed to resolve table or column expression named <code class="i">AppEvents</code> (or <code class="i">AppDependencies</code>)</td><td>The names do not match where you ran the query. <b>The Logs blade of an Application Insights resource always uses the classic names</b> (customEvents, dependencies), even when the resource is workspace-based. The App* names only exist in the Log Analytics workspace Logs. Run <b>V1</b> in section 3: it works in either window and shows which table names exist where you are. Then switch the toggle to match.</td></tr>
    <tr><td>Failed to resolve table or column expression named <code class="i">customEvents</code> or <code class="i">dependencies</code></td><td>The reverse: you are in the Log Analytics workspace. Switch the toggle to Log Analytics workspace.</td></tr>
    <tr><td>Failed to resolve scalar expression named <code class="i">lookback</code></td><td>You ran a function body directly. Save it as a function with a <code class="i">lookback</code> timespan parameter, or use <b>Copy as runnable query</b>.</td></tr>
    <tr><td>Failed to resolve table expression named <code class="i">AgentTraces</code>, <code class="i">AgentToolCalls</code>, <code class="i">AgentTokens</code> or <code class="i">AgentCost</code></td><td>The function was saved in a different place. Functions saved in an Application Insights resource are not visible from the workspace, and the reverse. Save it where you run the query, or use <b>Copy with functions inlined</b>.</td></tr>
    <tr><td>Unknown function: <code class="i">AgentTokens</code> (or another helper) even though the definition is pasted above the query</td><td>A <b>blank line splits a query</b> in the Logs editor, and only the block under the cursor runs. Remove any blank lines between the <code class="i">let</code> definitions and the query, or select everything before pressing Run. The copy buttons on this page (v2.0.1 and later) produce no blank lines.</td></tr>
    <tr><td>Query runs but returns no rows</td><td>No telemetry in the window, content capture is off, the platform does not emit that data, or <code class="i">tokenOps</code> points at the wrong span type. Run V2 and V3.</td></tr>
    <tr><td>Costs are all 0</td><td>The price table in AgentCost still has placeholder prices, or the model names do not match. Run K8 and copy the names.</td></tr>
  </tbody></table></div>
</section>

<section class="sec" id="verify">
  <h2>3. Verify your data</h2>
  <p class="lead">Run these first. They confirm which tables exist, what is flowing, and which of the questions below your current setup can answer.</p>
  {cards(VERIFY)}
</section>

<section class="sec" id="prompts">
  <h2>4. Prompts</h2>
  <p class="lead">What users ask, what agents answer, and how conversations unfold. Needs content capture for the text itself.</p>
  {cards(PROMPTS)}
</section>

<section class="sec" id="tools">
  <h2>5. Tool calls</h2>
  <p class="lead">Which tools agents call, with what arguments, how reliably and in what order. Needs span-based telemetry.</p>
  {cards(TOOLS)}
</section>

<section class="sec" id="cost">
  <h2>6. Token usage and cost</h2>
  <p class="lead">Tokens come from <code class="i">gen_ai.usage.input_tokens</code> and <code class="i">gen_ai.usage.output_tokens</code> on model-call spans. Cost is tokens multiplied by the prices you enter in AgentCost.</p>
  <div class="warn"><b>Cost is an estimate.</b> It reflects only what token counts and your price table can tell. Copilot Studio credits, provisioned throughput, cached-token discounts and negotiated rates are not reflected. Check it against your Azure Cost Management data before using it for chargeback. To avoid double counting, AgentTokens reads one span type only (<code class="i">tokenOps</code>).</div>
  {cards(COST)}
</section>

<section class="sec" id="alerts">
  <h2>7. Operational alerts</h2>
  <p class="lead">These queries return rows only when something needs attention, so each one works as an Azure Monitor log search alert rule that fires when the number of results is greater than 0.</p>
  <ol class="steps">
    <li><b>Save the functions in the Log Analytics workspace</b> that holds the Application Insights data and choose the <b>Log Analytics workspace</b> table names above. (Alerts on a workspace-based Application Insights resource itself can use the Application Insights names with functions saved there.)</li>
    <li>In the Azure portal open the workspace, then <b>Alerts &rarr; Create &rarr; Alert rule</b>. Choose the signal <b>Custom log search</b> and paste the query.</li>
    <li>Set <b>Measurement</b> to Table rows, <b>Aggregation granularity</b> equal to the frequency shown, and <b>Alert logic</b> to greater than 0. Add the <b>split by dimensions</b> shown on the card so you get one alert per agent.</li>
    <li>Set <b>Evaluation frequency</b> and the query window from the card. Azure Monitor limits how far back a log search alert can look, so the baselines here are deliberately short.</li>
    <li>Attach an action group (email, Teams, webhook), name the rule, and test with a lower threshold first.</li>
  </ol>
  {cards(ALERTS, alert=True)}
</section>

<section class="sec" id="privacy">
  <h2>8. Privacy and access</h2>
  <p class="lead">Observability traces can contain what employees and customers type into an agent. Treat the telemetry store as a sensitive data store, even though this toolkit does not look for sensitive content.</p>
  <div class="cards">
    <div class="card"><h4>Content capture is optional</h4><ul><li>Without it you keep spans, timings, tool names and tokens, which is enough for sections 5 and 6 and the alerts.</li><li>Enable prompt and tool content in development and where policy allows in production.</li></ul></div>
    <div class="card"><h4>Control who can read it</h4><ul><li>Anyone with read access to the workspace can read prompts. Use a dedicated workspace and restrict access with RBAC.</li></ul></div>
    <div class="card"><h4>Retention and region</h4><ul><li>Choose a retention period per table that matches policy, and keep the resource in the region your data-residency rules require.</li></ul></div>
    <div class="card"><h4>Involve the right people</h4><ul><li>Monitoring employee prompts can fall under works council, privacy or HR rules in some countries. Agree scope with your data protection officer first.</li><li>Per-user views (K6) are sensitive. Use them for chargeback only where agreed.</li></ul></div>
  </div>
</section>

<section class="sec" id="reference">
  <h2>9. Reference</h2>
  <h3>Table and column names by schema</h3>
  <div class="scroll"><table class="t"><thead><tr><th>Concept</th><th>Application Insights</th><th>Log Analytics workspace</th></tr></thead><tbody>
    <tr><td>Custom events</td><td>customEvents</td><td>AppEvents</td></tr>
    <tr><td>Spans (dependencies)</td><td>dependencies</td><td>AppDependencies</td></tr>
    <tr><td>Time</td><td>timestamp</td><td>TimeGenerated</td></tr>
    <tr><td>Attributes bag</td><td>customDimensions</td><td>Properties</td></tr>
    <tr><td>Event / span name</td><td>name</td><td>Name</td></tr>
    <tr><td>Role name / instance</td><td>cloud_RoleName / cloud_RoleInstance</td><td>AppRoleName / AppRoleInstance</td></tr>
    <tr><td>Operation (trace) ID</td><td>operation_Id</td><td>OperationId</td></tr>
    <tr><td>User / authenticated user</td><td>user_Id / user_AuthenticatedId</td><td>UserId / UserAuthenticatedId</td></tr>
    <tr><td>Duration / success / result code</td><td>duration / success / resultCode</td><td>DurationMs / Success / ResultCode</td></tr>
  </tbody></table></div>
  <h3>Output columns of the helper functions</h3>
  <div class="scroll"><table class="t"><thead><tr><th>Function</th><th>Columns</th></tr></thead><tbody>
    <tr><td>AgentTraces</td><td>timestamp, Source, Agent, Direction, Text, ConversationId, Channel, Model, User, OperationId</td></tr>
    <tr><td>AgentToolCalls</td><td>timestamp, Agent, Tool, ToolType, Arguments, Result, Succeeded, DurationMs, ResultCode, ConversationId, User, OperationId</td></tr>
    <tr><td>AgentTokens</td><td>timestamp, Agent, Model, Operation, InputTokens, OutputTokens, TotalTokens, DurationMs, Succeeded, ConversationId, User, OperationId</td></tr>
    <tr><td>AgentCost</td><td>all AgentTokens columns plus ModelKey, PriceMissing, InputCost, OutputCost, Cost</td></tr>
  </tbody></table></div>
  <h3>GenAI attributes used</h3>
  <div class="scroll"><table class="t"><thead><tr><th>Attribute</th><th>Used for</th></tr></thead><tbody>
    <tr><td>gen_ai.operation.name</td><td>invoke_agent, execute_tool, output_messages (Copilot Studio) and chat</td></tr>
    <tr><td>gen_ai.input.messages / gen_ai.output.messages</td><td>Prompt and response text (opt-in, sensitive)</td></tr>
    <tr><td>gen_ai.agent.name / gen_ai.agent.id</td><td>Agent identity</td></tr>
    <tr><td>gen_ai.conversation.id</td><td>Grouping turns into a conversation</td></tr>
    <tr><td>gen_ai.tool.name / .type / .call.arguments / .call.result</td><td>Tool-call analysis (arguments and result are opt-in)</td></tr>
    <tr><td>gen_ai.request.model, gen_ai.response.model</td><td>Model name for pricing</td></tr>
    <tr><td>gen_ai.usage.input_tokens / gen_ai.usage.output_tokens</td><td>Token and cost analysis</td></tr>
    <tr><td>microsoft.channel.name, user.email</td><td>Copilot Studio span telemetry: channel and user</td></tr>
  </tbody></table></div>
  <h3>Official documentation</h3>
  <ul>
    <li><a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-bot-framework-composer-capture-telemetry" target="_blank" rel="noopener">Copilot Studio: capture telemetry with Application Insights (agent-level)</a></li>
    <li><a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-environment-level-agent-telemetry" target="_blank" rel="noopener">Copilot Studio: environment-level telemetry with Application Insights (preview)</a></li>
    <li><a href="https://learn.microsoft.com/en-us/microsoft-copilot-studio/telemetry-overview" target="_blank" rel="noopener">Copilot Studio: telemetry with Application Insights overview</a></li>
    <li><a href="https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-client-side" target="_blank" rel="noopener">Microsoft Foundry: add client-side tracing to agents</a></li>
    <li><a href="https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md" target="_blank" rel="noopener">OpenTelemetry GenAI semantic conventions: agent spans</a></li>
    <li><a href="https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-log-alert-rule" target="_blank" rel="noopener">Azure Monitor: create or edit a log search alert rule</a></li>
  </ul>
</section>
</main></div>
<footer>Agent Observability Toolkit · Version {VERSION} · Last updated {UPDATED}. Companion to the Agent 365 PoC Planner. Built from Microsoft documentation and OpenTelemetry conventions. Proof of concept, not production-ready: validate in a test workspace first.</footer>
<script>{JS}</script>
</body></html>'''

open(OUT, "w", encoding="utf-8").write(page)
print("written", OUT, len(page), "bytes")

# ---- also export every query as a .kql file, one folder per schema
for v, folder in (("classic", "appinsights"), ("ws", "loganalytics")):
    base = os.path.join(ROOT, "queries", folder)
    os.makedirs(base, exist_ok=True)
    for sec, name in ((VERIFY, "verify"), (PROMPTS, "prompts"), (TOOLS, "tools"), (COST, "cost"), (ALERTS, "alerts")):
        for h in sec:
            slug = re.sub(r"[^a-z0-9]+", "-", h["title"].lower()).strip("-")[:60]
            open(os.path.join(base, f"{h['id']}-{slug}.kql"), "w", encoding="utf-8").write(
                f"// {h['id']}: {h['title']}\n// {h['desc']}\n" + render(h["q"], v) + "\n")
    fdir = os.path.join(base, "functions")
    os.makedirs(fdir, exist_ok=True)
    for k, n, t in FUNCS:
        open(os.path.join(fdir, f"{n}.kql"), "w", encoding="utf-8").write(render(t, v) + "\n")
print("exported .kql files")
