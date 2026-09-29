/* SpreadGuard live market data layer.
 *
 * Fetches the real BTC price from public no-key APIs and drives the live
 * figures on the desk page. Runs in the browser, so it works on a static
 * Vercel deploy. Several providers are tried in order so one blocked or
 * rate-limited host does not blank the demo. If every provider fails the page
 * says "offline" and keeps the last known value rather than faking a price.
 *
 * Providers are spot BTC/USD unless noted, and the venue is labeled in the UI.
 */

const SG = (() => {
  // Each provider: url, and a function that pulls price and optional 24h change.
  const PROVIDERS = [
    {
      name: "Coinbase spot",
      url: "https://api.coinbase.com/v2/prices/BTC-USD/spot",
      parse: (j) => ({ price: Number(j?.data?.amount), change: null }),
    },
    {
      name: "Binance spot",
      url: "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT",
      parse: (j) => ({
        price: Number(j?.lastPrice),
        change: Number(j?.priceChangePercent),
      }),
    },
    {
      name: "Gemini spot",
      url: "https://api.gemini.com/v1/pubticker/btcusd",
      parse: (j) => ({ price: Number(j?.last), change: null }),
    },
    {
      name: "Kraken spot",
      url: "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
      parse: (j) => {
        const r = j?.result?.XBTUSD || Object.values(j?.result || {})[0];
        const price = Number(r?.c?.[0]);
        const open = Number(r?.o);
        const change =
          isFinite(price) && isFinite(open) && open !== 0
            ? ((price - open) / open) * 100
            : null;
        return { price, change };
      },
    },
  ];

  const state = {
    price: null,
    change24h: null,
    status: "connecting", // connecting | live | offline
    source: null,
    lastGoodMs: null,
  };

  const listeners = new Set();
  const subscribe = (fn) => {
    listeners.add(fn);
    fn(state);
    return () => listeners.delete(fn);
  };
  const emit = () => listeners.forEach((fn) => fn(state));

  async function tryProvider(p) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 7000);
    try {
      const res = await fetch(p.url, { signal: ctrl.signal, cache: "no-store" });
      if (!res.ok) return null;
      const j = await res.json();
      const { price, change } = p.parse(j);
      if (!isFinite(price) || price <= 0) return null;
      return { price, change: isFinite(change) ? change : null };
    } catch (e) {
      return null;
    } finally {
      clearTimeout(t);
    }
  }

  async function fetchOnce() {
    for (const p of PROVIDERS) {
      const got = await tryProvider(p);
      if (got) {
        state.price = got.price;
        state.change24h = got.change;
        state.status = "live";
        state.source = p.name;
        state.lastGoodMs = Date.now();
        emit();
        return;
      }
    }
    if (state.status !== "live") state.status = "offline";
    emit();
  }

  function start(intervalMs = 30000) {
    fetchOnce();
    setInterval(fetchOnce, intervalMs);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) fetchOnce();
    });
  }

  const fmtUsd = (n, dp = 0) =>
    n == null
      ? "--"
      : n.toLocaleString("en-US", {
          minimumFractionDigits: dp,
          maximumFractionDigits: dp,
        });

  return { state, subscribe, start, fmtUsd, providers: PROVIDERS.map((p) => p.name) };
})();

// Expose on window explicitly. A top-level `const` in a classic script does
// not become a window property, so a separate inline script cannot see it.
window.SG = SG;
