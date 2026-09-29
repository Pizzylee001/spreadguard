import { parquetReadObjects, parquetMetadata } from "hyparquet";
import { compressors } from "hyparquet-compressors";
import https from "https";

const URL = "https://huggingface.co/datasets/predict-quant/binance-future-orderbook/resolve/main/BTCUSDT/2026-03-07_BTCUSDT_depth20.parquet";

function fetchBuf(url, depth = 0) {
  return new Promise((resolve, reject) => {
    if (depth > 5) return reject(new Error("too many redirects"));
    https.get(url, { headers: { "user-agent": "Mozilla/5.0" } }, (res) => {
      if ([301, 302, 307, 308].includes(res.statusCode) && res.headers.location) {
        const loc = res.headers.location.startsWith("http") ? res.headers.location : new URL(res.headers.location, url).toString();
        res.resume();
        return resolve(fetchBuf(loc, depth + 1));
      }
      const chunks = [];
      res.on("data", (d) => chunks.push(d));
      res.on("end", () => resolve(Buffer.concat(chunks)));
    }).on("error", reject);
  });
}

const buf = await fetchBuf(URL);
console.log("downloaded bytes:", buf.length);
const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
const meta = parquetMetadata(ab);
console.log("rows:", meta.num_rows, "row_groups:", meta.row_groups.length);
console.log("schema:", meta.schema.slice(0, 12).map((s) => s.name).join(","));

const rows = await parquetReadObjects({ file: ab, compressors, rowStart: 0, rowEnd: 3 });
console.log("row count sample:", rows.length);
console.log("keys:", Object.keys(rows[0]).join(","));
console.log("row0 e:", rows[0].e, "T:", rows[0].T);
console.log("row0 bids:", String(rows[0].bids).slice(0, 160));
console.log("row0 asks:", String(rows[0].asks).slice(0, 160));