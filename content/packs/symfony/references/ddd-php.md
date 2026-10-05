# DDD placement — Symfony

DDD here means **context-first packaging**: the top-level split is by business area, not by
technical role. Everything a context owns lives under it, and contexts talk through published
events or explicit interfaces — never by reaching into each other's internals.

```
src/
├── Booking/                          a bounded context
│   ├── Domain/
│   │   ├── Booking.php               the AGGREGATE ROOT: enforces its invariants
│   │   ├── BookingId.php             a value object
│   │   ├── Seat.php                  an entity inside the aggregate, no id of its own outside it
│   │   ├── BookingConfirmed.php      a domain EVENT, past tense
│   │   └── Bookings.php              the repository interface — one per aggregate
│   ├── Application/
│   │   └── ConfirmBooking.php        one use case, one transaction, one aggregate
│   └── Infrastructure/
│       ├── Web/BookingController.php
│       └── Persistence/DoctrineBookings.php
└── Billing/                          another context: its own Invoice, its own language
    └── …                            may hold its own copy of BookingId; does not import Booking\Domain
```

## Where each kind of new code goes

| The test drives… | Put it in | Notes |
|---|---|---|
| An invariant ("a booking needs at least one seat") | The aggregate root's constructor or method | Not a Validator class, not the use case |
| A rule spanning two aggregates | `Application/` use case, or an event listener | Never a method that loads both and mutates both |
| A new field on an entity inside the aggregate | Through the root | Outside code never holds a `Seat` directly |
| A query for a screen, not a rule | A read model in `Infrastructure/Persistence/` | Do not distort the aggregate to serve a view |
| A reaction in another context | Dispatch a Symfony Messenger event; handle it in that context's `Application/` | The publisher never imports the subscriber |
| A new route | That context's `Infrastructure/Web/` | |

## Imports, allowed and not

- `<Context>/Domain/` imports only itself and PHP's standard library.
- `<Context>/Application/` imports its own `Domain/`.
- **No context imports another context's `Domain/`.** Duplicating a small id or value type
  across contexts is correct here, not duplication to be removed.
- `Infrastructure/` imports inward freely.

## The two mistakes this style invites

**One aggregate per table.** Aggregates are consistency boundaries, not rows. If `Booking` and
`Seat` must change together to stay valid, they are one aggregate with one repository —
`Bookings` — and there is no `Seats`.

**A context boundary with no language behind it.** If `Billing` and `Booking` use the same words
to mean the same things and always change together, they are one context wearing two directory
names. The cost of the split is real; take it only where the vocabulary genuinely differs.

## Two smaller traps

- **Events used as function calls.** `BookingConfirmed` is a statement of fact in the past tense.
  If the publisher needs a result back, that is a call through an interface, not a Messenger
  event.
- **A use case spanning transactions on two aggregates.** One use case, one aggregate, one
  `wrapInTransaction()`; anything further happens on the event.
