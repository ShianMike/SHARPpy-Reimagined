// The shared test fixture (DESIGN-tooling T8 collector). Every spec records
// console messages, page errors, and securitypolicyviolation events, and fails
// on any console.error, uncaught error, or CSP violation. Backend failures and
// contract violations log with console.warn, so error scenarios can still
// pass on purpose; specs read `consoleMessages` to assert their warnings.
import { test as base, expect } from "@playwright/test";

export interface ConsoleEntry {
  type: string;
  text: string;
}

interface Fixtures {
  consoleMessages: ConsoleEntry[];
}

declare global {
  interface Window {
    __e2eCspViolations?: string[];
  }
}

export const test = base.extend<Fixtures>({
  consoleMessages: [
    async ({ page }, use) => {
      await page.emulateMedia({ colorScheme: "dark" });
      const messages: ConsoleEntry[] = [];
      const pageErrors: string[] = [];
      await page.addInitScript(() => {
        window.__e2eCspViolations = [];
        document.addEventListener("securitypolicyviolation", (event) => {
          window.__e2eCspViolations?.push(`${event.violatedDirective} blocked ${event.blockedURI}`);
        });
      });
      page.on("console", (message) => {
        messages.push({ type: message.type(), text: message.text() });
      });
      page.on("pageerror", (error) => {
        pageErrors.push(error.message);
      });

      await use(messages);

      const violations = await page
        .evaluate(() => window.__e2eCspViolations ?? [])
        .catch(() => [] as string[]);
      const errors = messages.filter((entry) => entry.type === "error").map((entry) => entry.text);
      expect(pageErrors, "uncaught page errors").toEqual([]);
      expect(errors, "console.error messages").toEqual([]);
      expect(violations, "CSP violations").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
