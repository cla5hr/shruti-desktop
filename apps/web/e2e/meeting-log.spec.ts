// Happy-path e2e against the live dev stack (api + worker + web all running).
// The upload test exercises the REAL pipeline with whisper-tiny — expect ~15-60s.
import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.resolve(HERE, "../../../fixtures/sample-meeting.mp3");

test("home renders the log shell", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".wordmark__latin")).toHaveText("SHRUTI");
  await expect(page.locator(".upload__action")).toBeVisible();
});

test("upload → pipeline → synced transcript", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/");

  await page.locator('input[type="file"]').setInputFiles(FIXTURE);

  // lands on the new meeting page; pipeline tray appears, then finishes
  await expect(page.locator(".masthead__title")).toHaveText("sample-meeting", {
    timeout: 30_000,
  });
  await expect(page.locator(".masthead__meta")).toContainText("READY", { timeout: 200_000 });

  // transcript rendered with plausible content
  const rows = page.locator(".utt");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThan(3);
  await expect(page.locator(".log")).toContainText(/meeting/i);

  // strip chart drew and click-to-seek moves the transport clock
  await expect(page.locator(".chart__canvas")).toBeVisible();
  const lastTs = page.locator(".utt__ts").last();
  const label = await lastTs.textContent();
  await lastTs.click();
  await expect(page.locator(".transport__time strong")).toHaveText(label!.trim());

  // search finds text and reports matches
  await page.getByPlaceholder("find in transcript…").fill("meeting");
  await expect(page.locator(".logpane__matches")).toContainText("of");
  await expect(page.locator("mark").first()).toBeVisible();
});
