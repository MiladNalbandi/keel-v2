# Finding the commit that introduced it

Bisect is for one question: **which commit made this start failing?** It needs a test that
fails now and passed before, so write the reproducing test first, then bisect with it.

## The setup

The reproducing test lives on your branch, but bisect checks out old commits where it does
not exist. So either stash the test and use a command that works at every commit, or keep
the test file out of the checkout:

```
git bisect start
git bisect bad                       # HEAD, where it fails
git bisect good v1.4.0               # a tag or sha where it worked
```

Then let git run the tagged test at each step:

```
git bisect run ./gradlew -q test --tests '*BUG014*'
```

The test command exits non-zero when the tagged test fails, which is exactly the contract
`git bisect run` needs. When it finishes, git names the first bad commit; read it with
`git show`.

```
git bisect reset                     # always, before doing anything else
```

## Keeping the test available across old commits

The test does not exist at the good commit, so the run there will report "no tests
found" — a non-zero exit, which bisect reads as *bad* and ruins the search. Two ways round it:

**Copy the test in at each step.** Keep it outside the repo and have bisect place it:

```
cp /tmp/BUG014Test.kt apps/api/src/test/kotlin/app/ && ./gradlew -q test --tests '*BUG014*'
```

Wrap that in a small script and pass the script to `git bisect run`.

**Or bisect on a coarser command.** If the symptom shows through an existing test or a
one-line check, use that instead — `git bisect run ./gradlew -q test --tests '*SeatServiceTest*'`.

## Reading the result honestly

The first bad commit is where the symptom *appeared*, which is not always where the cause
lives. A commit that adds a caller can expose a latent bug written months earlier. So treat
the bisect result as evidence, not as a verdict: `git show <sha>` and ask whether that diff
could plausibly cause this symptom. If it cannot, the cause is older and that commit merely
started calling it.

## When bisect is the wrong tool

- The test is flaky: bisect will follow the noise. Make it deterministic first
  (`reproduce-flaky.md`).
- The failure depends on data or environment rather than code: bisecting the code finds
  nothing.
- The range is under about five commits: just read them.

## Cheaper first attempts

Before a full bisect, two greps often find it outright:

```
git log -p --since='3 weeks ago' -- apps/api/src/main/kotlin/app/seat/
git log -S 'remaining(' --oneline        # commits that changed this symbol's usage
```

`git log -S` is the underused one: it finds commits where the count of a string changed,
which is usually the commit that touched the logic you care about.
