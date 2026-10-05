# Design principles, in Kotlin

SOLID and the patterns around it, judged against what keel already enforces. Load this in
`refactor`, or in GREEN when a criterion touches a shape that already exists.

**Read this against the minimum-code rule, not around it.** GREEN forbids any abstraction the
current test does not drive. That is not in tension with good design — it is *when* good design is
allowed to appear. A principle applied before a second caller exists is speculation with a
respectable name; the `refactor` phase is where it is applied against real duplication, with the
tests green and holding behaviour still.

## The one that pays for itself, and the one that does not

**Single responsibility earns its place immediately.** Not "one class, one job" as a slogan —
the usable form is *one reason to change*. A use case that decides a rule, maps a DTO and formats
a response has three, and the third AC that touches any of them will break the other two.

**Dependency inversion is the one most often cargo-culted here.** An interface with exactly one
implementation, created so the use case "depends on an abstraction", buys nothing: it adds a file,
a jump in every trace, and a mock that can now assert a contract the real adapter never produces —
which is `test-integrity`'s first finding. In a Spring codebase the seam usually already exists
(constructor injection, a repository interface Spring Data generates). Add your own only when
there is a **second** implementation or a boundary the architecture style names, and
`architecture` is where that boundary is defined.

**Open/closed is a description, not an instruction.** Code that turned out to be extensible is
good; code shaped for extension nobody asked for is the abstraction GREEN forbids. If a `when` over
a sealed class is getting a fourth branch and each branch has its own data, that is the signal —
not the first branch.

**Liskov and interface segregation** rarely bite in Kotlin the way they do in Java: sealed classes
make the hierarchy closed and explicit, and interfaces here are small by habit. Where LSP does bite
it is usually an entity subclass changing what a method promises — and that is better solved by
composition than by a rule.

## Kotlin-specific, where the language decides the design

- **Model absence with types, not nulls plus checks.** `String?` forces the decision at the call
  site; a non-null field defaulted to `""` moves the bug to whoever reads it. Prefer a sealed
  result over an exception when the caller must handle both paths — but not for the transport
  layer's own failures, which belong in the error mapper.
- **`data class` for values, never for a JPA entity.** `equals`/`hashCode` generated over mutable
  persistent fields breaks identity the moment the id is assigned; see
  `kotlin-backend-jpa-entity-mapping` if the project has it, and the entity rules in this
  skill's parent.
- **Extension functions instead of a utility class**, and keep them next to the type they extend.
  A `Utils` object is a namespace pretending to be a design.
- **`value class` for an identifier** that would otherwise be a bare `String` or `UUID` passed
  through four layers. This is the cheapest correctness win in the language: it makes a swapped
  `userId`/`accountId` a compile error rather than a production incident.
- **`require`/`check` at the top of a function** for invariants the type cannot express. They read
  as documentation and fail loudly, which is better than a validation branch no test reaches.
- **Prefer immutable state and `copy`.** A mutable field on a shared object is how the
  `concurrency` lens finds you.

## Patterns worth naming, and what they cost here

| Pattern | Use it when | The cost keel will charge |
|---|---|---|
| Strategy (sealed + `when`) | three or more variants with real behaviour differences | every branch needs a test, or the coverage floor catches it |
| Factory | construction is genuinely conditional | a factory with one path is an indirection, not a factory |
| Repository | already given by Spring Data | hand-writing one over a `JpaRepository` duplicates a seam that exists |
| Decorator/proxy | cross-cutting concern with several subjects | Spring AOP and `@Transactional` already do this; a self-call bypasses both |
| Mapper/DTO | a response shape that is not the entity | always worth it — an entity crossing the controller is a `LazyInitializationException` waiting for the serializer |

## The test that settles most arguments

Before adding an abstraction, name the **second** caller. If you cannot, you are designing for a
future that has not asked for anything, and the code no test drives is exactly what GREEN refuses.
If you can, the abstraction is already justified and the duplication is already real — which means
it belongs in `refactor`, where the tests hold the behaviour still while you move it.
