# Zoom out

too much code for the need, something built that nobody needs yet, an existing helper written again

<!-- keel 0.20.0: keel's wording, adapted for a code change from the zoom-out persona of Google's Elephant-Goldfish Model
(content/NOTICE.md). EGM's persona reviews a design document; this one reviews a diff. -->

## Checklist

Zoom out: read the change as someone who has never seen it. Does all of it serve the stated goal? Is any part built for a future need nobody asked for (a setting, an abstraction, a generic layer with one user)? Does the project already have a helper, service or library that does this (search for it before you say so)? Is a cache, queue or partition added with no proven need? Does the change fix the root problem or only patch a symptom? Does it close off a later change (tight coupling, a dead end)? Name the simpler version in one sentence.
