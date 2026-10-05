import { expect, test as base } from '@playwright/test'

/**
 * Test data is seeded through the API in a fixture, never by clicking through the UI:
 * a setup path that goes through the interface makes every test depend on every screen.
 */
const test = base.extend<{ api: Api }>({
  api: async ({ request }, use) => {
    const created: number[] = []
    await use({
      async createBookmark(url: string) {
        const res = await request.post('/api/bookmarks', { data: { url } })
        expect(res.status(), 'seeding a bookmark').toBe(201)
        const body = await res.json()
        created.push(body.id)
        return body
      },
    })
    for (const id of created) await request.delete(`/api/bookmarks/${id}`)
  },
})

interface Api {
  createBookmark(url: string): Promise<{ id: number; url: string }>
}

// One test per [E2E] acceptance criterion, the AC id first in the title, tagged @e2e.
test('AC-006 saves and lists a bookmark @e2e', async ({ page }) => {
  await page.goto('/bookmarks')

  // Locators by role and label. If a component needs a test id, that is a [WEB] AC
  // change — not an edit made during the E2E phase.
  await page.getByRole('textbox', { name: 'URL' }).fill('https://x.dev')
  await page.getByRole('button', { name: 'Save' }).click()

  await expect(page.getByRole('listitem').filter({ hasText: 'x.dev' })).toBeVisible()
})

test('AC-007 shows an existing bookmark on load @e2e', async ({ page, api }) => {
  await api.createBookmark('https://seeded.dev')

  await page.goto('/bookmarks')

  await expect(page.getByRole('listitem').filter({ hasText: 'seeded.dev' })).toBeVisible()
})

// The smoke subset: one test, the single most critical path, tagged @smoke so
// the smoke run can select it with `playwright test --grep @smoke`.
test('the app shell loads and the bookmarks route is reachable @smoke', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('navigation')).toBeVisible()

  await page.getByRole('link', { name: 'Bookmarks' }).click()
  await expect(page.getByRole('heading', { name: 'Bookmarks' })).toBeVisible()
})
