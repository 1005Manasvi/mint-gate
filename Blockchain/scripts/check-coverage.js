// Fails CI when contract coverage drops below the target. Run after `npx hardhat coverage`.
const fs = require("fs");
const path = require("path");

const MIN = Number(process.env.MIN_COVERAGE || 95); // percent, lines and branches
const summary = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "coverage", "coverage-summary.json")));
const { lines, branches } = summary.total;
console.log(`contract coverage: lines ${lines.pct} %, branches ${branches.pct} % (minimum ${MIN} %)`);
if (lines.pct < MIN || branches.pct < MIN) {
  console.error("coverage below target");
  process.exit(1);
}
