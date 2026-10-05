import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest'

import { BookmarkForm } from '../BookmarkForm'
// Generated from contracts/openapi.yaml — never hand-edited, and a contract change
// breaks these tests rather than passing silently.
import { bookmarkSchema } from '../api/generated/schemas'
import { createBookmark } from '../api/generated/client'

const created = vi.fn()

const server = setupServer(
  http.post('/bookmarks', async ({ request }) => {
    const body = (await request.json()) as { url?: string }
    if (!body.url) {
      return HttpResponse.json({ errors: [{ field: 'url', message: 'required' }] }, { status: 422 })
    }
    created(body.url)
    return HttpResponse.json({ id: 7, url: body.url, createdAt: '2026-01-01T00:00:00Z' }, { status: 201 })
  }),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => { server.resetHandlers(); created.mockClear() })
afterAll(() => server.close())

// The AC id must be in the test *name*: keel runs it alone with `vitest run -t AC-004`.
it('AC-004 shows a validation error for an empty url and sends no request', async () => {
  render(<BookmarkForm />)

  await userEvent.click(screen.getByRole('button', { name: 'Save' }))

  // Assert what the user sees, by role and text — not a class name.
  expect(await screen.findByText('Enter a URL')).toBeVisible()
  // The other half of the AC: nothing was sent.
  expect(created).not.toHaveBeenCalled()
})

it('AC-005 saves a valid url and parses the response against the contract', async () => {
  render(<BookmarkForm />)

  await userEvent.type(screen.getByRole('textbox', { name: 'URL' }), 'https://x.dev')
  await userEvent.click(screen.getByRole('button', { name: 'Save' }))

  expect(await screen.findByText('Saved')).toBeVisible()
  expect(created).toHaveBeenCalledWith('https://x.dev')
})

it('AC-005 parses the created bookmark with the generated zod schema', async () => {
  // Through the generated client, not a hand-written fetch: the client is what the app
  // uses, so this exercises the deserialisation path the feature actually runs on.
  const bookmark = await createBookmark({ url: 'https://x.dev' })

  // `parse` returns the value on success and throws on a mismatch, so asserting on what
  // comes back IS the assertion — there is nothing to wrap in `expect(() => …)`.
  // (`expect(() => parse(res.json())).not.toThrow()` reads fine and can never pass:
  //  `res.json()` is a Promise, so parse always throws.)
  expect(bookmarkSchema.parse(bookmark)).toMatchObject({ url: 'https://x.dev' })
})
