// node --test "frontend/tests/*.test.mjs"   (Node >= 20: crypto.subtle is global)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { canonicalReport, parseJson, pixelHash, pyDumps, reportHash } from "../js/canonical.js";

const vector = JSON.parse(
  readFileSync(new URL("../../backend/tests/fixtures/canonical_vector.json", import.meta.url), "utf8"),
);

test("report hash equals Python's canonical_hash byte for byte", async () => {
  assert.equal(await reportHash(vector.report_text), vector.hash);
});

test("number spellings survive (JSON.parse would lose them)", () => {
  const text = '{"b":1.0,"a":[1e-05,12345678901234567890123,-0.5]}';
  assert.equal(pyDumps(parseJson(text)), '{"a":[1e-05,12345678901234567890123,-0.5],"b":1.0}');
});

test("strings are escaped like json.dumps(ensure_ascii=True)", () => {
  const input = String.raw`"é😀\u0001\u007f\"/"`;
  const bs = "\\"; // python -c 'import json; print(json.dumps("é😀\x01\x7f\"/"))'
  const expected = `"${bs}u00e9${bs}ud83d${bs}ude00${bs}u0001${bs}u007f${bs}"/"`;
  assert.equal(pyDumps(parseJson(input)), expected);
});

test("tampering changes the hash; missing fields and bad JSON are errors", async () => {
  const tampered = vector.report_text.replace('"score": 93', '"score": 94');
  assert.notEqual(tampered, vector.report_text);
  assert.notEqual(await reportHash(tampered), vector.hash);
  assert.throws(() => canonicalReport('{"report_id": "x"}'), /missing/);
  assert.throws(() => parseJson('{"a": 1,}'), SyntaxError);
  assert.throws(() => parseJson("[1] 2"), SyntaxError);
});

test("pixel hash covers size + RGB, ignores alpha", async () => {
  const rgba = new Uint8Array([1, 2, 3, 255, 4, 5, 6, 0]);
  const same = new Uint8Array([1, 2, 3, 9, 4, 5, 6, 9]);
  assert.equal(await pixelHash(2, 1, rgba), await pixelHash(2, 1, same));
  assert.notEqual(await pixelHash(1, 2, rgba), await pixelHash(2, 1, rgba));
});
