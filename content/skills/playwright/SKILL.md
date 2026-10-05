---
name: playwright
description: "End-to-end and smoke test patterns with Playwright: stable locators, API seeding in fixtures, tagging by acceptance criterion, trimmed output, and the smoke subset. Load when writing E2E or smoke tests."
user-invocable: false
---

# Playwright patterns

## One spec per feature, one test per E2E acceptance criterion

```ts
test('AC-006 saves and lists a bookmark @e2e', async ({ page, api }) => {
  await api.createUser();                 // seed through the API, never the UI
  await page.goto('/bookmarks');
  await page.getByRole('textbox', { name: 'URL' }).fill('https://x.dev');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByRole('listitem').filter({ hasText: 'x.dev' })).toBeVisible();
});
```

## Rules

- Locators: roles, labels, text. If a component needs a test id, that is a `[WEB]` AC change, not an E2E-phase edit.
- Seed data through the API in fixtures.
- Start the stack through `webServer` in the config, so local and CI behave the same; reuse a running server locally.
- Use the `line` reporter; report the failing step and the trace path, never paste a whole trace.
- Smoke is a subset: one `@smoke` test that loads the app and walks the single most critical path, under 60 seconds with the shell checks.

## While exploring

Use `playwright-cli` to look at the running app; it keeps snapshots on disk instead of in the conversation. Do not use Playwright MCP for this.
