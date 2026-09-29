import { parquetReadObjects, parquetMetadata } from "hyparquet";
import { compressors } from "hyparquet-compressors";
import https from "https";
import fs from "fs";

const arg = (n, d) => {
  const i = process.argv.indexOf("--" + n);
  return i >= 0 && process.argv[i + 1] !== undefined ? process.argv[i + 1] : d;
};

const FILE = arg("file", "2026-03-07_BTCUSDT_depth20.parquet");
const URL = `https://huggingface.co/datasets/predict-quant/binance-future-orderbook/resolve/main/BTCUSDT/${FILE}`;
const TICK_MS = Number(arg("tick-ms", "250"));
const OUT_A = arg("out-a", "tape/real_halves_a.jsonl");
const OUT_B = arg("out-b", "tape/real_halves_b.jsonl");
const OUT = arg("out", null);
const MAXLEVELS = Number(arg("levels", "10"));

function fetchBuf(url, depth = 0) {
  return new Promise((resolve, reject) => {
    if (depth > 5) return reject(new Error("too many redirects"));
    https.get(url, { headers: { "user-agent": "Mozilla/5.0" } }, (res) => {
      if ([301, 302, 307, 308].includes(res.statusCode) && res.headers.location) {
        const loc = res.headers.location.startsWith("http")
          ? res.headers.location
          : new URL(res.headers.location, url).toString();
        res.resume();
        return resolve(fetchBuf(loc, depth + 1));
      }
      const chunks = [];
      res.on("data", (d) => chunks.push(d));
      res.on("end", () => resolve(Buffer.concat(chunks)));
    }).on("error", reject);
  });
}

function levels(raw) {
  let arr;
  try { arr = typeof raw === "string" ? JSON.parse(raw) : raw; } catch { return []; }
  const out = [];
  for (const pair of arr) {
    const p = Number(pair[0]);
    const s = Number(pair[1]);
    if (isFinite(p) && isFinite(s) && s > 0) out.push([p, s]);
  }
  return out;
}

console.log("downloading", FILE);
const buf = await fetchBuf(URL);
console.log("bytes", buf.length);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
const meta = parquetMetadata(ab);
const n = Number(meta.num_rows);
console.log("rows", n);

const rows = await parquetReadObjects({ file: ab, compressors });
console.log("loaded", rows.length);

// two contiguous halves for out-of-sample
const mid = Math.floor(rows.length / 2);
const targets = OUT
  ? [{ path: OUT, rows }]
  : [{ path: OUT_A, rows: rows.slice(0, mid) }, { path: OUT_B, rows: rows.slice(mid) }];

function writeTape(path, rr) {
  fs.mkdirSync(path.split("/").slice(0, -1).join("/") || ".", { recursive: true });
  const f = fs.createWriteStream(path);
  let books = 0, trades = 0;
  let pb = null, ps = null, pa = null, pas = null;
  let bucket = null, pending = null;
  const first = Number(rr[0].T), last = Number(rr[rr.length - 1].T);
  for (const r of rr) {
    const ts = Number(r.T);
    const bids = levels(r.bids).sort((x, y) => y[0] - x[0]);
    const asks = levels(r.asks).sort((x, y) => x[0] - y[0]);
    if (!bids.length || !asks.length) continue;
    const [bb, bs] = bids[0];
    const [ba, as] = asks[0];

    // infer taker flow from touch-size drops at the same price
    if (pb !== null) {
      if (bb === pb && bs < ps) { f.write(JSON.stringify({ kind: "trade", ts, price: +bb.toFixed(2), size: +(ps - bs).toFixed(4), side: "sell" }) + "\n"); trades++; }
      if (ba === pa && as < pas) { f.write(JSON.stringify({ kind: "trade", ts, price: +ba.toFixed(2), size: +(pas - as).toFixed(4), side: "buy" }) + "\n"); trades++; }
    }
    pb = bb; ps = bs; pa = ba; pas = as;

    const bkt = Math.floor(ts / TICK_MS);
    if (bucket === null) bucket = bkt;
    if (bkt !== bucket) {
      if (pending) { f.write(JSON.stringify(pending) + "\n"); books++; }
      bucket = bkt;
    }
    pending = {
      kind: "book", ts,
      bids: bids.slice(0, MAXLEVELS).map(([p, s]) => [+p.toFixed(2), +s.toFixed(4)]),
      asks: asks.slice(0, MAXLEVELS).map(([p, s]) => [+p.toFixed(2), +s.toFixed(4)]),
    };
  }
  if (pending) { f.write(JSON.stringify(pending) + "\n"); books++; }
  f.end();
  const span = (last - first) / 1000;
  console.log(`${path}: books=${books} trades=${trades} span=${span.toFixed(0)}s`);
}

for (const t of targets) writeTape(t.path, t.rows);
console.log("done");