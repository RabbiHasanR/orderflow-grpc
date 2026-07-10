"""Static HTML for the /inventory/stock-watch/demo page.

A single self-contained page (inline CSS + JS, no assets) so you can exercise the
bidirectional ``WatchStock`` RPC from a browser: subscribe to product ids, then
watch live ``snapshot``/``changed`` updates arrive while the socket stays open —
e.g. run a ``POST /orders`` that consumes a watched product and see the new
quantity appear. Kept out of the router module to keep that file a thin adapter.
"""

STOCK_WATCH_DEMO_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OrderFlow — live stock watch (bidi gRPC demo)</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, sans-serif; margin: 0; padding: 1.5rem;
           max-width: 760px; margin-inline: auto; line-height: 1.5; }
    h1 { font-size: 1.25rem; margin: 0 0 .25rem; }
    p.sub { margin: 0 0 1rem; opacity: .75; }
    .bar { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center;
           margin-bottom: 1rem; }
    input { padding: .4rem .6rem; font-size: 1rem; width: 12rem;
            border: 1px solid #8888; border-radius: 6px; background: transparent;
            color: inherit; }
    button { padding: .4rem .8rem; font-size: 1rem; border: 1px solid #8888;
             border-radius: 6px; cursor: pointer; background: #8882; color: inherit; }
    button:hover { background: #8884; }
    #status { font-weight: 600; }
    #status.open { color: #1a9d4b; }
    #status.closed { color: #c0392b; }
    #log { border: 1px solid #8884; border-radius: 8px; padding: .5rem;
           height: 22rem; overflow-y: auto; font-family: ui-monospace, monospace;
           font-size: .85rem; white-space: pre-wrap; }
    .snapshot { color: #2b7de9; }
    .changed  { color: #e67e22; font-weight: 600; }
    .meta { opacity: .6; }
  </style>
</head>
<body>
  <h1>Live stock watch — bidirectional gRPC</h1>
  <p class="sub">
    WebSocket <code>/inventory/stock-watch</code> ⇆ gRPC
    <code>WatchStock(stream WatchCommand) → stream StockUpdate</code>.
    Subscribe to a product, then reserve it via <code>POST /orders</code> in another
    terminal — a <b>changed</b> update arrives here without reconnecting.
  </p>

  <div class="bar">
    <span>status: <span id="status" class="closed">connecting…</span></span>
  </div>
  <div class="bar">
    <input id="ids" placeholder="product ids e.g. 1,2,3" />
    <button id="sub">Subscribe</button>
    <button id="unsub">Unsubscribe</button>
    <button id="clear">Clear log</button>
  </div>

  <div id="log"></div>

  <script>
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/inventory/stock-watch`);
    const statusEl = document.getElementById("status");
    const logEl = document.getElementById("log");

    function log(html) {
      const line = document.createElement("div");
      const t = new Date().toLocaleTimeString();
      line.innerHTML = `<span class="meta">${t}</span> ${html}`;
      logEl.appendChild(line);
      logEl.scrollTop = logEl.scrollHeight;
    }

    ws.onopen = () => { statusEl.textContent = "open"; statusEl.className = "open"; };
    ws.onclose = () => { statusEl.textContent = "closed"; statusEl.className = "closed"; };
    ws.onerror = () => log('<span class="changed">socket error</span>');
    ws.onmessage = (ev) => {
      const u = JSON.parse(ev.data);
      log(`<span class="${u.kind}">[${u.kind}]</span> #${u.product_id} ` +
          `${u.sku} (${u.name}) → <b>${u.available_quantity}</b>`);
    };

    function parseIds() {
      return document.getElementById("ids").value
        .split(",").map(s => parseInt(s.trim(), 10)).filter(n => !Number.isNaN(n));
    }
    function send(action) {
      const product_ids = parseIds();
      if (!product_ids.length) { log("enter one or more product ids first"); return; }
      ws.send(JSON.stringify({ action, product_ids }));
      log(`<span class="meta">→ sent ${action} ${JSON.stringify(product_ids)}</span>`);
    }

    document.getElementById("sub").onclick = () => send("subscribe");
    document.getElementById("unsub").onclick = () => send("unsubscribe");
    document.getElementById("clear").onclick = () => { logEl.innerHTML = ""; };
  </script>
</body>
</html>
"""
