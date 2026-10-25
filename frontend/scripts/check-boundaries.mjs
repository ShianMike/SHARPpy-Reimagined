// Boundary check (DESIGN-tooling T7), run by `npm run lint` after ESLint.
// Prints every violation as <file>:<line>: [rule] message and exits 1 if any.
//
// 1 colour literals, 2 pixel literals, 3 IPC boundary, 4 glass,
// 5 science-term scan, 6 file size.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(frontendRoot, "..");
const srcRoot = join(frontendRoot, "src");
const SKIP_DIRS = new Set(["node_modules", "dist", "dist-browser", "dist-fixture", "test-results", "playwright-report", "target"]);

/** Repo-relative path with forward slashes. */
function rel(file) {
  return relative(repoRoot, file).split(sep).join("/");
}

function walk(dir, accept, out = []) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name) && !entry.name.startsWith(".")) {
        walk(full, accept, out);
      }
    } else if (entry.isFile() && accept(full)) {
      out.push(full);
    }
  }
  return out;
}

const violations = [];
function report(file, line, rule, message) {
  violations.push(`${rel(file)}:${String(line)}: [rule ${String(rule)}] ${message}`);
}

const isTest = (file) => /\.test\.tsx?$/.test(file);
const isTs = (file) => /\.tsx?$/.test(file);
const isCss = (file) => file.endsWith(".css");
const srcFiles = walk(srcRoot, (file) => isTs(file) || isCss(file));
const srcRel = (file) => relative(srcRoot, file).split(sep).join("/");

function linesOf(file) {
  return readFileSync(file, "utf8").split(/\r?\n/);
}

// Rules 1 and 2: colour and pixel literals outside tokens.css and tests.
const COLOUR = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![0-9a-zA-Z_-])|\b(?:rgba?|hsla?)\(/g;
const PIXEL = /(?<![\w.-])(?:\d+\.?\d*|\.\d+)px\b/g;
const AT_CONDITION = /^\s*@(?:media|container)\b[^{]*/;
for (const file of srcFiles) {
  if (srcRel(file) === "styles/tokens.css" || isTest(file)) {
    continue;
  }
  linesOf(file).forEach((text, index) => {
    for (const match of text.matchAll(COLOUR)) {
      report(file, index + 1, 1, `colour literal ${match[0]} (use a token from styles/tokens.css)`);
    }
    // @media and @container conditions cannot use variables, so they are exempt.
    const scanned = isCss(file) ? text.replace(AT_CONDITION, "") : text;
    for (const match of scanned.matchAll(PIXEL)) {
      report(file, index + 1, 2, `pixel literal ${match[0]} (use a token from styles/tokens.css)`);
    }
  });
}

// Rule 3: only tauriTransport.ts touches @tauri-apps/api or calls invoke(.
for (const file of srcFiles) {
  if (srcRel(file) === "lib/backend/tauriTransport.ts") {
    continue;
  }
  linesOf(file).forEach((text, index) => {
    if (text.includes("@tauri-apps/api")) {
      report(file, index + 1, 3, "@tauri-apps/api is imported only by src/lib/backend/tauriTransport.ts");
    }
    if (/\binvoke\(/.test(text)) {
      report(file, index + 1, 3, "invoke( is called only by src/lib/backend/tauriTransport.ts");
    }
  });
}

// Rule 4: backdrop-filter and the .glass selector live in styles/glass.css;
// the glass class names are applied only by components/GlassSurface.tsx.
const GLASS_SELECTOR = /(?<![\w$)\]-])\.glass(?:--[\w-]+)?(?![\w-])/g;
const STRING = /(["'`])((?:\\.|(?!\1)[^\\])*)\1/g;
for (const file of srcFiles) {
  const name = srcRel(file);
  const lines = linesOf(file);
  lines.forEach((text, index) => {
    if (name !== "styles/glass.css" && text.includes("backdrop-filter")) {
      report(file, index + 1, 4, "backdrop-filter appears only in src/styles/glass.css");
    }
    if (name !== "styles/glass.css" && isCss(file) && GLASS_SELECTOR.test(text)) {
      report(file, index + 1, 4, "the .glass selector appears only in src/styles/glass.css");
    }
    GLASS_SELECTOR.lastIndex = 0;
    if (isTs(file) && name !== "components/GlassSurface.tsx" && !isTest(file)) {
      for (const match of text.matchAll(STRING)) {
        const tokens = (match[2] ?? "").split(/\s+/);
        if (tokens.some((token) => token === "glass" || /^glass--[\w-]+$/.test(token) || /^\.glass(--[\w-]+)?$/.test(token))) {
          report(file, index + 1, 4, "glass classes are applied only by src/components/GlassSurface.tsx");
        }
      }
    }
  });
}

// Rule 5: no meteorological terms in TypeScript (the allowlist starts empty).
const SCIENCE_TERMS = [
  "cape", "mlcape", "mucape", "sbcape", "cin", "srh", "helicity", "shear", "lapse", "dewpoint",
  "theta", "parcel", "lifted", "lcl", "lfc", "bunkers", "storm motion", "stp", "scp", "ehi",
  "precipitable", "mixing ratio", "wet bulb", "virtual temperature", "vorticity",
];
const SCIENCE = new RegExp(`\\b(?:${SCIENCE_TERMS.map((t) => t.replace(/ /g, "\\s+")).join("|")})\\b`, "gi");
// "src/relative/path.ts:term" -> reason. Only visible labels of controls whose
// values sharpmod computes may be listed; never a calculation.
const SCIENCE_ALLOWLIST = new Map([
  [
    "features/sounding/AscentPicker.tsx:parcel",
    "the visible name of sharpmod.rendering.cli's PARCEL_TYPES choice; sharpmod draws the image",
  ],
  [
    "map/controls/SurfacePlotControls.tsx:dewpoint",
    "the visible legend for sharpmod.bridge.surface_plots' lower_text; sharpmod formats the provider's reading",
  ],
  [
    "map/hover/ReportTooltip.tsx:dewpoint",
    "the visible label for sharpmod.bridge.surface_plots' numeric reading; only display units change",
  ],
  [
    "features/sounding/SoundingImage.tsx:dewpoint",
    "the visible label for sharpmod.rendering.interactive's native profile value; only display units change",
  ],
]);
for (const file of srcFiles.filter(isTs)) {
  linesOf(file).forEach((text, index) => {
    for (const match of text.matchAll(SCIENCE)) {
      if (!SCIENCE_ALLOWLIST.has(`${srcRel(file)}:${match[0].toLowerCase()}`)) {
        report(file, index + 1, 5, `meteorological term "${match[0]}" in TypeScript (no science in the frontend)`);
      }
    }
  });
}

// Rule 6: file size ceilings.
function lineCount(file) {
  const text = readFileSync(file, "utf8");
  if (text === "") {
    return 0;
  }
  const lines = text.split(/\r?\n/);
  return lines[lines.length - 1] === "" ? lines.length - 1 : lines.length;
}
function checkSize(files, limit, what) {
  for (const file of files) {
    const count = lineCount(file);
    if (count > limit) {
      report(file, limit + 1, 6, `${what} has ${String(count)} lines (limit ${String(limit)})`);
    }
  }
}
const frontendTs = walk(frontendRoot, (file) => isTs(file) && !file.endsWith(".d.ts"));
checkSize(frontendTs, 300, "TypeScript file");
checkSize(walk(frontendRoot, (file) => file.endsWith(".module.css")), 300, "CSS module");
checkSize(srcFiles.filter((file) => srcRel(file) === "styles/tokens.css"), 400, "tokens.css");
const rustDir = join(repoRoot, "src-tauri", "src");
checkSize(walk(rustDir, (file) => file.endsWith(".rs")), 400, "Rust file");
const bridgeDir = join(repoRoot, "sharpmod", "bridge");
checkSize(walk(bridgeDir, (file) => file.endsWith(".py")), 300, "Python bridge file");

for (const required of [rustDir, bridgeDir, srcRoot]) {
  if (!statSync(required, { throwIfNoEntry: false })?.isDirectory()) {
    violations.push(`${rel(required)}: [setup] expected directory is missing`);
  }
}

if (violations.length > 0) {
  for (const line of violations) {
    console.error(line);
  }
  console.error(`check-boundaries: ${String(violations.length)} violation(s)`);
  process.exit(1);
}
console.log(`check-boundaries: ${String(srcFiles.length)} source files checked, no violations`);
