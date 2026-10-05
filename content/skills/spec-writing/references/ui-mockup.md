<!-- From keel v1 (MIT), kept in step with its spec-authoring references; keel v2 asks its questions with keel-questions buttons. -->
# The UI mockup

One mockup per spec, four states, box-drawing, 80 columns. Fenced in the spec under `## UI mockup`.

## Why four states

A wireframe of the happy path tells you almost nothing you did not already know. The other three are where the missing acceptance criteria live:

| State | The question it forces |
|---|---|
| Default | What is on the screen, and what can the user do? |
| Empty | First run, or a filter with no matches — what does the user see instead of a list? |
| Loading | What is on screen while the request is in flight, and can they still act? |
| Error | The save failed. What does it say, where, and what can they do next? |

This is the same split `web-implementation` (`references/data.md`) requires in the code: loading, error and empty are three branches, not one `isLoading` flag. Drawing them here is what makes those branches acceptance criteria rather than afterthoughts.

If a state genuinely cannot happen, write one line saying why. "No empty state: the list always contains the user's own account" is a decision. Silence is an omission.

## The form

```
┌─ Bookmarks ─────────────────────────────────────────┐
│                                                     │
│  [ https://…                    ]  ( Save )         │
│                                                     │
│  ✓ example.com/a-page              tagged: read     │
│  ✓ example.com/another             tagged: —        │
│                                                     │
│  2 bookmarks                          [ Load more ] │
└─────────────────────────────────────────────────────┘
```

Conventions, so every spec reads the same way:

| Notation | Means |
|---|---|
| `[ text ]` | An input |
| `( Label )` | A button |
| `[ Label ]` | A secondary action or link |
| `( • )` `( )` | Radio, selected and not |
| `[x]` `[ ]` | Checkbox |
| `…` | Truncation, or a value the user supplies |
| `▾` | A select |
| `⟳` | A spinner or in-flight indicator |
| `‹ ›` | Focus, where focus placement is part of the criterion |

## All four, labelled

```
Empty
┌─ Bookmarks ─────────────────────────────────────────┐
│  [ https://…                    ]  ( Save )         │
│                                                     │
│  Nothing saved yet. Paste a link above to start.    │
└─────────────────────────────────────────────────────┘

Loading
┌─ Bookmarks ─────────────────────────────────────────┐
│  [ https://…                    ]  ( Save )  ⟳      │
│                                                     │
│  Loading your bookmarks…                            │
└─────────────────────────────────────────────────────┘

Error
┌─ Bookmarks ─────────────────────────────────────────┐
│  [ not-a-url                    ]  ( Save )         │
│  ⚠ That does not look like a URL.                   │
│                                                     │
│  ✓ example.com/a-page              tagged: read     │
└─────────────────────────────────────────────────────┘
```

Note what the error state settles that prose had left open: the message sits under the field rather than at the top, the existing list stays visible, and the invalid text is not cleared. Each of those is a criterion someone would otherwise decide by accident in GREEN.

## Then write the criteria from it

Read the drawings back and name what each state asserts. The error state above yields something like:

```
- AC-004 [WEB] Given an invalid URL, when the user saves, then the field shows
  "That does not look like a URL.", the entry is kept, and the list is unchanged.
```

If a drawn state produces no criterion, either it does not matter and should come out of the drawing, or you have found the gap the drawing existed to find.
