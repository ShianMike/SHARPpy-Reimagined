// Test support: reads tokens from the text of src/styles/tokens.css, so tests
// use the shipped values. Importable only from test files (ESLint, T3).
//
// It collects the declarations of top-level rules whose selector is exactly
// ":root" (not the profile blocks, nothing inside @media), and resolves
// var(--name) references within that set, as getComputedStyle would.

export type CssTokenReader = (name: string) => string;

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Declarations of the top-level `:root` rules, in source order. */
export function rootDeclarations(css: string): Map<string, string> {
  const text = stripComments(css);
  const declarations = new Map<string, string>();
  let depth = 0;
  let selectorStart = 0;
  let blockStart = -1;
  let selector = "";
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === "{") {
      if (depth === 0) {
        selector = text.slice(selectorStart, index).trim();
        blockStart = index + 1;
      }
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        if (selector === ":root") {
          for (const part of text.slice(blockStart, index).split(";")) {
            const colon = part.indexOf(":");
            const name = part.slice(0, colon).trim();
            if (colon > 0 && name.startsWith("--")) {
              declarations.set(name, part.slice(colon + 1).trim().replace(/\s+/g, " "));
            }
          }
        }
        selectorStart = index + 1;
      }
    }
  }
  return declarations;
}

function resolveValue(name: string, declarations: Map<string, string>, seen: Set<string>): string {
  const value = declarations.get(name);
  if (value === undefined) {
    return "";
  }
  if (seen.has(name)) {
    throw new Error(`cssTokens: circular reference through ${name}`);
  }
  seen.add(name);
  const resolved = value.replace(/var\((--[\w-]+)\)/g, (_match, ref: string) =>
    resolveValue(ref, declarations, seen),
  );
  seen.delete(name);
  return resolved;
}

/** A reader with getComputedStyle semantics: "" for a missing token. */
export function cssTokenReader(css: string): CssTokenReader {
  const declarations = rootDeclarations(css);
  return (name) => resolveValue(name, declarations, new Set());
}
