/* SpreadGuard live market data layer.
 *
 * Fetches the real BTC price from a public no-key API and drives the live
 * figures on the desk page. Runs in the browser, so it works on a static
 * Vercel deploy. If the fetch fails the page keeps the last known value and
 * shows the source as offline, rather than faking a price.
 *
 * CoinGecko is used because it needs no key and has a generous free tier.
 * Venue: spot BTC/USD, labeled honestly in the UI.
 */

const SG = (() => {
  const ENDPOINTS = [
    "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true&include_24hr_vol=true",
  ];

  const state = {
    price: null,
    change24h: null,
    volume24h: null,
    status: "connecting", // connecting | live | offline
    updatedMs: null,
    lastGoodMs: null,
  };

  const listeners = new Set();
  const subscribe = (fn) => {
    listeners.add(fn);
    fn(state);
    return () => listeners.delete(fn);
  };
  const emit = () => listeners.forEach((fn) => fn(state));

  async function fetchOnce() {
    for (const url of ENDPOINTS) {
      try {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), 8000);
        const res = await fetch(url, { signal: ctrl.signal, cache: "no-store" });
        clearTimeout(t);
        if (!res.ok) continue;
        const j = await res.json();
        const btc = j && j.bitcoin;
        if (!btc || typeof btc.usd !== "number") continue;

        state.price = btc.usd;
        state.change24h =
          typeof btc.usd_24h_change === "number" ? btc.usd_24h_change : null;
        state.volume24h =
          typeof btc.usd_24h_vol === "number" ? btc.usd_24h_vol : null;
        state.status = "live";
        state.updatedMs = Date.now();
        state.lastGoodMs = Date.now();
        emit();
        return true;
      } catch (e) {
        // try the next endpoint
      }
    }
    // no endpoint answered
    if (state.status !== "live") state.status = "offline";
    emit();
    return false;
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

  return { state, subscribe, start, fmtUsd };
})();
