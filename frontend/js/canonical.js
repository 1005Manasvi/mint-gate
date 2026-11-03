// Report hash, recomputed in the browser: a byte-exact port of backend/detection/report_hash.py
//   sha256(json.dumps({k: report[k] for k in ATTESTED_FIELDS}, sort_keys=True, separators=(",", ":")))
// JSON.parse would lose number spellings (Python writes 1.0 and 1e-05, JavaScript would print 1 and
// 0.00001), so this parser keeps every number's source text and re-emits it unchanged.
// Pure module (no DOM, no ethers): also runs under `node --test`.
"use strict";

export const ATTESTED_FIELDS = [
  "report_id",
  "created_at",
  "image_sha256",
  "decision",
  "score",
  "policy_version",
  "policy_sha256",
  "models",
  "checks",
];

class Num {
  constructor(raw) {
    this.raw = raw;
  }
}

const NUMBER = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|NaN|-?Infinity/y;

/** JSON text -> values; numbers become Num(sourceText). Throws SyntaxError on invalid JSON. */
export function parseJson(text) {
  let i = 0;
  const ws = () => {
    while (i < text.length && " \t\n\r".includes(text[i])) i++;
  };
  const fail = (what) => {
    throw new SyntaxError(`invalid JSON at ${i}: ${what}`);
  };
  const str = () => {
    const start = i++;
    while (i < text.length && text[i] !== '"') i += text[i] === "\\" ? 2 : 1;
    if (i >= text.length) fail("unterminated string");
    i++;
    return JSON.parse(text.slice(start, i)); // decoding escapes: same rules as Python's json.loads
  };
  const value = () => {
    ws();
    const c = text[i];
    if (c === "{") {
      i++;
      const obj = new Map();
      ws();
      if (text[i] === "}") return (i++, obj);
      for (;;) {
        ws();
        if (text[i] !== '"') fail("expected a key");
        const k = str();
        ws();
        if (text[i++] !== ":") fail("expected ':'");
        obj.set(k, value());
        ws();
        if (text[i] === ",") i++;
        else if (text[i] === "}") return (i++, obj);
        else fail("expected ',' or '}'");
      }
    }
    if (c === "[") {
      i++;
      const arr = [];
      ws();
      if (text[i] === "]") return (i++, arr);
      for (;;) {
        arr.push(value());
        ws();
        if (text[i] === ",") i++;
        else if (text[i] === "]") return (i++, arr);
        else fail("expected ',' or ']'");
      }
    }
    if (c === '"') return str();
    for (const [word, v] of [
      ["true", true],
      ["false", false],
      ["null", null],
    ]) {
      if (text.startsWith(word, i)) return ((i += word.length), v);
    }
    NUMBER.lastIndex = i;
    const m = NUMBER.exec(text);
    if (!m) fail("unexpected character");
    i += m[0].length;
    return new Num(m[0]);
  };
  const out = value();
  ws();
  if (i !== text.length) fail("trailing data");
  return out;
}

/** Python json.dumps string escaping with ensure_ascii=True. */
function pyString(s) {
  const named = { '"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f" };
  let out = '"';
  for (const ch of s) {
    for (let k = 0; k < ch.length; k++) {
      const c = ch[k];
      const code = c.charCodeAt(0);
      if (named[c]) out += named[c];
      else if (code < 0x20 || code > 0x7e) out += "\\u" + code.toString(16).padStart(4, "0");
      else out += c;
    }
  }
  return out + '"';
}

/** json.dumps(v, sort_keys=True, separators=(",", ":")) for values from parseJson. */
export function pyDumps(v) {
  if (v === null) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (v instanceof Num) return v.raw;
  if (typeof v === "string") return pyString(v);
  if (Array.isArray(v)) return "[" + v.map(pyDumps).join(",") + "]";
  if (v instanceof Map) {
    const keys = [...v.keys()].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
    return "{" + keys.map((k) => pyString(k) + ":" + pyDumps(v.get(k))).join(",") + "}";
  }
  throw new TypeError("unsupported value");
}

/** The attested subset of a report (JSON text), serialised exactly like the backend does. */
export function canonicalReport(text) {
  const report = parseJson(text);
  if (!(report instanceof Map)) throw new SyntaxError("the report is not a JSON object");
  const body = new Map();
  for (const k of ATTESTED_FIELDS) {
    if (!report.has(k)) throw new Error(`report field "${k}" is missing`);
    body.set(k, report.get(k));
  }
  return pyDumps(body);
}

export async function sha256Hex(textOrBytes) {
  const bytes = typeof textOrBytes === "string" ? new TextEncoder().encode(textOrBytes) : textOrBytes;
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Hex SHA-256 (no 0x) of a report's canonical form: equals the on-chain `reportHash`. */
export async function reportHash(text) {
  return sha256Hex(canonicalReport(text));
}

/** Mint-Gate's image hash: SHA-256 over "<w>x<h>" + the RGB pixel bytes (detection/preprocessing.py). */
export async function pixelHash(width, height, rgba) {
  const prefix = new TextEncoder().encode(`${width}x${height}`);
  const buf = new Uint8Array(prefix.length + width * height * 3);
  buf.set(prefix);
  for (let p = 0, o = prefix.length; p < rgba.length; p += 4) {
    buf[o++] = rgba[p];
    buf[o++] = rgba[p + 1];
    buf[o++] = rgba[p + 2];
  }
  return sha256Hex(buf);
}
