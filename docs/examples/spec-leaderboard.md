# Leaderboard page

A page listing top players by best score, searchable by name, with rank shown
for each player.

## Request

Add a leaderboard page that lists the top players by best score. Users
should be able to search it by player name, and each player shows their
rank.

## Decisions

- Leaderboard page means a small static HTML page served by a plain Node
  `http` server built on Node built-ins only (no framework, no build step).
- `playerId` doubles as the player name: no separate name field is added.
- Search by name is a case-insensitive substring match (e.g. "an" matches
  "ana").
- Ties on best score share a rank and skip the next rank (1, 2, 2, 4).

## Assumptions

- The server exposes the leaderboard data as JSON on an endpoint the page's
  script fetches, and renders the list client-side; search runs against
  that endpoint via a query parameter, not purely in the browser, since the
  full score set is the source of truth.
- Players with no scores recorded are not listed (only ids with at least
  one `addScore` call appear).
- The search query parameter is optional; omitting it returns the full
  ranked list.
- Rank numbers are computed over the full leaderboard, not just the
  filtered/searched subset, so a filtered player keeps the rank they'd have
  unfiltered.

## Acceptance criteria

- **AC-1** [API] Given players with recorded scores, when GET /api/leaderboard
  is called with no query, then it returns all players ordered by best
  score descending, each with their `id`, `best` score, and `rank`.
- **AC-2** [API] Given two or more players tied on best score, when the
  leaderboard is requested, then the tied players share the same rank and
  the next distinct rank skips accordingly (1, 2, 2, 4).
- **AC-3** [API] Given players named e.g. "ana" and "bo", when GET
  /api/leaderboard?search=an is called, then only players whose id
  contains "an" (case-insensitive) are returned, each still showing the
  rank it holds in the full (unfiltered) leaderboard.
- **AC-4** [API] Given a search query that matches no player, when the
  leaderboard is requested with that query, then the response is an empty
  list with a 200 status.
- **AC-5** [WEB] Given the leaderboard page is loaded in a browser, when it
  finishes loading, then it displays each player's rank, name, and best
  score in rank order.
- **AC-6** [WEB] Given the leaderboard page is loaded, when a user types
  into the search box, then the displayed list updates to only the
  matching players without a full page reload.

## Mockup

```
Loading:
+-------------------------------+
| Leaderboard                   |
| [ Search players...        ]  |
|                                |
| Loading...                     |
+-------------------------------+

Filled:
+-------------------------------+
| Leaderboard                   |
| [ Search players...        ]  |
|                                |
| #1  bo    9                   |
| #2  ana   7                   |
| #2  cy    7                   |
| #4  dan   3                   |
+-------------------------------+

Empty (no scores yet):
+-------------------------------+
| Leaderboard                   |
| [ Search players...        ]  |
|                                |
| No players yet.               |
+-------------------------------+

Error:
+-------------------------------+
| Leaderboard                   |
| [ Search players...        ]  |
|                                |
| Could not load leaderboard.   |
+-------------------------------+
```

## Request path

```
Browser GET /                -> NEW: static HTML/JS leaderboard page
Browser fetch /api/leaderboard[?search=] -> NEW: HTTP route
  -> src/scores.js topScores()/new ranking+search logic -> CHANGED/NEW
  -> in-memory scores Map (existing)                    -> unchanged
```

## Out of scope

- Authentication/authorization: the leaderboard is public, no login.
- Pagination: all matching players are returned in one response.
- Persistence: scores remain in-memory only, reset on process restart.
- Separate display names distinct from `playerId`.
