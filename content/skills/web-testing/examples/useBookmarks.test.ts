import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'

import { useBookmarks } from '../useBookmarks'
// Generated from contracts/openapi.yaml. A contract change breaks this test rather than
// passing silently, which is the whole reason to parse with it.
import { bookmarkSchema } from '../api/generated/schemas'

const server = setupServer(
  http.get('/bookmarks', () =>
    HttpResponse.json([{ id: '1', url: 'https://x.dev', createdAt: '2026-01-01T00:00:00Z' }])),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// Most [WEB] criteria live at this layer: the behaviour around a request — loading, error,
// what the screen has to render — rather than the markup that renders it.
it('AC-006 exposes the bookmarks once the request settles', async () => {
  const { result } = renderHook(() => useBookmarks())

  // The loading state is a criterion in its own right: a screen that cannot tell "empty"
  // from "not yet" shows the empty state during every load.
  expect(result.current.isLoading).toBe(true)

  await waitFor(() => expect(result.current.isLoading).toBe(false))

  expect(result.current.data).toHaveLength(1)
  // One test per endpoint parses the real shape, so a body mismatch fails here too and not
  // only in the backend's body test.
  expect(bookmarkSchema.parse(result.current.data![0])).toMatchObject({ url: 'https://x.dev' })
})

it('AC-007 reports a failure instead of an empty list', async () => {
  server.use(http.get('/bookmarks', () => new HttpResponse(null, { status: 500 })))

  const { result } = renderHook(() => useBookmarks())
  await waitFor(() => expect(result.current.isLoading).toBe(false))

  // The assertion that matters: a failed load is distinguishable from a successful empty one.
  // `data` being undefined rather than `[]` is what lets the screen show an error at all.
  expect(result.current.error).toBeTruthy()
  expect(result.current.data).toBeUndefined()
})
