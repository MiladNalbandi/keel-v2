# Integrations

Everything that leaves this process, and what stands in for it in tests. An integration with
no stand-in is a test that cannot run offline — say so explicitly rather than leaving an
agent to discover it.

> Claims here carry a backticked `path:line`; every one must resolve.
## Outbound

| Depends on | Protocol | Used for | Owns the call | Stand-in in tests |
|---|---|---|---|---|
| {{SERVICE}} | <HTTP / gRPC / AMQP> | <one line> | `{{ADAPTER_CLASS}}` | <WireMock / Testcontainers module / local Keycloak / fake> |

<For each, the two things that actually bite: what happens when it is down (retry, circuit
breaker, fail the request), and whether the call is idempotent.>

## Inbound

| Comes from | How | Verified by |
|---|---|---|
| {{CALLER}} | <webhook / poll / queue> | <signature, token, mTLS> |

## Configuration

Names only — values live in `.env.local`, which keel refuses to read so secrets stay out of
the conversation.

| Variable | For | Required |
|---|---|---|
| {{VAR}} | {{SERVICE}} | <yes / only in production> |

## Stand-ins

| Layer | What runs |
|---|---|
| Unit and slice | <mocked adapter — MockK> |
| Data and integration | Testcontainers: <PostgreSQL, Kafka, Redis modules> |
| E2E | <WireMock for HTTP third parties; the real dev stack for our own services> |

## Cannot run locally

<Company SSO, internal APIs, paid SaaS. Each with the suggested stand-in and what is
therefore *not* covered by any test — the honest gap, recorded so nobody assumes coverage
that does not exist. The runbook's "needs you" list is the other half of this.>
