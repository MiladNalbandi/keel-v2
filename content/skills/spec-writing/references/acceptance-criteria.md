<!-- From keel v1 (MIT), kept in step with its spec-authoring references; keel v2 asks its questions with keel-questions buttons. -->
# Acceptance criteria

`AC-001`, `AC-002`, … zero-padded, one sequence per spec, never renumbered once the spec is approved — commits and test names reference them by id.

## The form

```
- AC-003 [API] Given a request with no url, when POST /bookmarks is called,
  then it returns 422 and the body names the url field.
```

Given / when / then, and the *then* must be observable from outside: a status code, a stored value, something on screen, a message sent. "The service validates the URL" is not observable; "returns 422 naming the url field" is.

## Layer tags decide the loop

| Tag | Loop | Test layer |
|---|---|---|
| `[API]` | Phase 3, `api` lane | unit, web slice, body, data slice |
| `[WEB]` | Phase 4, `web` lane | component, hook |
| `[E2E]` | The E2E phase, `e2e-author` | Playwright journey |
| `[SMOKE]` | Phase 7 | a shell check plus one `@smoke` test |

The tag is not a label: it routes the criterion. It picks the lane, which the guard matrix then enforces — an `[API]` criterion cannot edit frontend code — and it picks which testing skill loads in RED. A mis-tagged criterion sends the whole loop to the wrong place, so tag by *where the assertion lives*, not where the work feels like it belongs.

`[gate: skip]` on the line marks a criterion trivial enough to need no human gate:

```
- AC-007 [API] [gate: skip] Rename BookmarkSvc to BookmarkService.
```

## One criterion, one assertion concept

If you need "and" between two observable outcomes, it is usually two criteria. The test for a criterion should fail for exactly one reason.

Split when: two different status codes, two different screens, or a rule and its error message that could plausibly change independently.

Do not split when: one outcome has several fields to check, or the same rule applies to several inputs — that is one criterion with a few assertions, or a parameterised test.

## How many

Three to eight for a feature. Fewer than three usually means the criteria are too coarse to test. More than eight usually means two features, or a spec that should be split — and keel's size check will say so during a change flow.

## Asking so the answers become criteria

An interview question with real options produces a criterion almost verbatim; an open question produces a paragraph you then have to interpret. A `keel-questions` block (see the `spec-clarify` skill) takes 2–4 options per question, so use them:

```
Q  A URL already saved — what should saving it again do?
   · Reject with 409 (recommended)   the list stays unique; the client shows the existing entry
   · Update the existing entry       last-write-wins; the original timestamp is lost
   · Allow a duplicate row           simplest; the list can show the same link twice
```

The chosen option is the criterion:

```
- AC-005 [API] Given a URL already saved, when POST /bookmarks is called again,
  then it returns 409 and the body names the existing id.
```

Each option must say what it *means*, not just what it is called — "reject with 409" is a label, "the list stays unique; the client shows the existing entry" is the consequence the user is actually choosing between. Put your recommendation first and mark it.

## Where they come from

From the interview and from the drawings, in that order. The interview gives you the rules; the mockup's four states and the request path's marked boxes give you the ones the interview missed. A criterion that appeared only because you drew the empty state is the most valuable kind.

## Register them

keel reads the criteria from the spec file after you write it, one per `- **AC-n** [LAYER]` line, so a criterion that is not in that form is not registered.
