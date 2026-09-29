#!/usr/bin/env node
/**
 * Bitget BTC-USDT perpetual tape recorder.
 *
 * Records public market data (no API key needed) into the exact schema the
 * Python replay harness reads: one JSON object per line.
 *
 *   {"kind":"book","ts":<ms>,"bids":[[price,size],...],"asks":[[price,size],...]}
 *   {"kind":"trade","ts":<ms>,"price":<p>,"size":<s>,"side":"buy"|"sell"}
 *
 * Run where the exchange is reachable (your Ubuntu box or a small VPS):
 *   node record_bitget.mjs --seconds 600 --out tape/btcusdt_perp.jsonl
 *
 * Node 22+ has a built in WebSocket, so there are no dependencies to install.
 * The script tries several channel spellings because exchanges rename them
 * between doc revisions, and it prints which one worked. It writes a short
 * diagnostic of the first messages so the channel can be confirmed on run one.
 */

import fs from "fs";
import path from "path";

const args = process.argv.slice(2);
function arg(name, def) {
  const i = args.indexOf("--" + name);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : def;
}
const SECONDS = Number(arg("seconds", "600"));
const OUT = arg("out", "tape/btcusdt_perp.jsonl");
const DIAG = arg("diag", "tape/first_messages.log");
const MAX_LEVELS = Number(arg("levels", "10"));

const ENDPOINTS = [
  "wss://ws.bitget.com/mix/v1/stream",
  "wss://ws.bitget.com/spot/v1/stream",
];
const CHANNEL_SETS = [
  ["btcusdt:open-api-v1:books", "btcusdt:open-api-v1:trade", "btcusdt:open-api-v1:book-ticker"],
  ["BTCUSDT:MC:books", "BTCUSDT:MC:trade", "BTCUSDT:MC:book-ticker"],
  ["btcusdt:open-api-v1:books5", "btcusdt:open-api-v1:trade"],
];

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.mkdirSync(path.dirname(DIAG), { recursive: true });

const out = fs.createWriteStream(OUT, { flags: "w" });
let diagLines = 0;
const diag = fs.createWriteStream(DIAG, { flags: "w" });

let books = 0;
let trades = 0;
let bookTicks = 0;
let lastBookTs = 0;
let lastTradeTs = 0;
let started = Date.now();
let connected = false;

function toLevels(arr, side) {
  if (!Array.isArray(arr)) return [];
  const rows = arr
    .map((r) => (Array.isArray(r) ? [Number(r[0]), Number(r[1])] : null))
    .filter((r) => r && isFinite(r[0]) && isFinite(r[1]) && r[1] > 0);
  rows.sort((a, b) => (side === "bid" ? b[0] - a[0] : a[0] - b[0]));
  return rows.slice(0, MAX_LEVELS);
}

function writeBook(d) {
  const bids = toLevels(d.bids || d.b, "bid");
  const asks = toLevels(d.asks || d.a, "ask");
  if (!bids.length || !asks.length) return;
  const ts = Number(d.ts || d.T || Date.now());
  if (ts < lastBookTs) return; // drop out of order snapshots
  lastBookTs = ts;
  out.write(JSON.stringify({ kind: "book", ts, bids, asks }) + "\n");
  books++;
  bookTicks++;
}

function writeTrade(d) {
  const price = Number(d.price || (d.data && d.data[0] && d.data[0].price) || d.p);
  const size = Number(d.size || (d.data && d.data[0] && d.data[0].size) || d.s);
  let side = String(d.side || d.S || (d.data && d.data[0] && d.data[0].side) || "").toLowerCase();
  if (side !== "buy" && side !== "sell") side = side === "b" ? "buy" : "sell";
  if (!isFinite(price) || !isFinite(size) || price <= 0 || size <= 0) return;
  const ts = Number(d.ts || d.T || Date.now());
  lastTradeTs = ts;
  out.write(JSON.stringify({ kind: "trade", ts, price, size, side }) + "\n");
  trades++;
}

function handleMessage(raw) {
  if (diagLines < 40) {
    diag.write(raw.slice(0, 300) + "\n");
    diagLines++;
  }
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch {
    return;
  }
  if (Array.isArray(msg)) {
    msg.forEach((m) => route(m));
    return;
  }
  route(msg);
}

function route(m) {
  if (!m || typeof m !== "object") return;
  const ch = String(m.arg || m.channel || m.instId || "");
  const d = m.data !== undefined ? m.data : m;
  if (ch.includes("trade")) {
    if (Array.isArray(d)) d.forEach(writeTrade);
    else writeTrade(d);
  } else if (ch.includes("book")) {
    if (Array.isArray(d)) d.forEach(writeBook);
    else writeBook(d);
  } else if (d && (d.bids || d.asks)) {
    writeBook(d);
  }
}

function tryConnect(endpoint, channels) {
  return new Promise((resolve) => {
    let settled = false;
    let ws;
    try {
      ws = new WebSocket(endpoint);
    } catch (e) {
      return resolve(false);
    }
    const done = (ok) => {
      if (settled) return;
      settled = true;
      resolve(ok);
    };
    ws.addEventListener("open", () => {
      ws.send(JSON.stringify({ op: "subscribe", args: channels }));
    });
    ws.addEventListener("message", (ev) => {
      handleMessage(typeof ev.data === "string" ? ev.data : String(ev.data));
      if (!connected && (books > 0 || trades > 0)) {
        connected = true;
        console.log(`STREAMING via ${endpoint} channels=${JSON.stringify(channels)}`);
        done(true);
      }
    });
    ws.addEventListener("error", () => done(false));
    ws.addEventListener("close", () => done(false));
    setTimeout(() => done(connected), 9000);
  });
}

async function main() {
  console.log(`Recording Bitget BTC-USDT perp for ${SECONDS}s -> ${OUT}`);
  let ok = false;
  for (const ep of ENDPOINTS) {
    for (const ch of CHANNEL_SETS) {
      ok = await tryConnect(ep, ch);
      if (ok) break;
    }
    if (ok) break;
  }
  if (!ok) {
    console.error("FAILED: no channel streamed data. See", DIAG, "for raw first messages.");
    out.end();
    diag.end();
    process.exit(2);
  }

  const timer = setInterval(() => {
    const elapsed = ((Date.now() - started) / 1000).toFixed(0);
    process.stdout.write(`\r  ${elapsed}s  books=${books} trades=${trades}   `);
  }, 2000);

  setTimeout(() => {
    clearInterval(timer);
    out.end();
    diag.end();
    const cov = bookTicks > 1 ? ((lastBookTs - started) / 1000).toFixed(0) : "0";
    console.log(`\nDone. books=${books} trades=${trades} span~${cov}s -> ${OUT}`);
    console.log(`Diagnostic first messages -> ${DIAG}`);
    process.exit(0);
  }, SECONDS * 1000);
}

started = Date.now();
main();