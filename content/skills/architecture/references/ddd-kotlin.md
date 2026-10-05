# DDD placement — Kotlin/Spring

DDD here means **context-first packaging**: the top-level split is by business area, not by technical role. Everything a context owns lives under it, and contexts talk through published events or explicit interfaces — never by reaching into each other's internals.

```
apps/api/src/main/kotlin/app/
├── booking/                        a bounded context
│   ├── domain/
│   │   ├── Booking.kt              the AGGREGATE ROOT: enforces its invariants
│   │   ├── BookingId.kt            value class
│   │   ├── Seat.kt                 an entity inside the aggregate, no id of its own outside it
│   │   ├── BookingConfirmed.kt     a domain EVENT, past tense
│   │   └── Bookings.kt             the repository interface — one per aggregate
│   ├── application/
│   │   └── ConfirmBooking.kt       one use case, one transaction, one aggregate
│   └── adapter/
│       ├── web/BookingEndpoint.kt
│       └── persistence/JpaBookings.kt
└── billing/                        another context: its own Invoice, its own language
    └── …                           may hold its own BookingId copy; does not import booking.domain
```

## Where each kind of new code goes

| The test drives… | Put it in | Notes |
|---|---|---|
| An invariant ("a booking needs at least one seat") | The aggregate root's constructor or method | Not a validator class, not the use case |
| A rule spanning two aggregates | `application/` use case, or an event handler | Never a method that loads both and mutates both |
| A new field on an entity inside the aggregate | Through the root | Outside code never holds a `Seat` directly |
| A query for a screen, not a rule | A read model in `adapter/persistence/` | Do not distort the aggregate to serve a view |
| A reaction in another context | Publish an event; handle it in that context's `application/` | The publisher never imports the subscriber |
| A new endpoint | That context's `adapter/web/` | |

## Imports, allowed and not

- `<context>/domain/` imports only itself and the stdlib.
- `<context>/application/` imports its own `domain/`.
- **No context imports another context's `domain/`.** Duplicating a small id or value type across contexts is correct here, not duplication to be removed.
- `adapter/` imports inward freely.

## The two mistakes this style invites

**One aggregate per table.** Aggregates are consistency boundaries, not rows. If `Booking` and `Seat` must change together to stay valid, they are one aggregate with one repository — `Bookings` — and there is no `Seats`.

**A context boundary with no language behind it.** If `billing` and `booking` use the same words to mean the same things and always change together, they are one context wearing two directory names. The cost of the split is real; take it only where the vocabulary genuinely differs.

## Two smaller traps

- **Events used as function calls.** `BookingConfirmed` is a statement of fact in the past tense. If the publisher needs a result back, that is a call through an interface, not an event.
- **A use case spanning transactions on two aggregates.** One use case, one aggregate, one transaction; anything further happens on the event.
