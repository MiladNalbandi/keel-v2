# Codebase details

a change that does not fit the code around it, names that do not exist, a library used against its contract

<!-- keel 0.20.0: keel's wording, adapted for a code change from the codebase details persona of Google's Elephant-Goldfish
Model (content/NOTICE.md). EGM's persona reviews a design document; this one reviews a diff. -->

## Checklist

Codebase details: check the change against the real code, not against what it assumes. Does each function, field, route, config key and table it uses exist with that name and shape (open it)? Does it follow how the same thing is done elsewhere in this project (find one other place and compare)? Does it use a library or framework call the way its version documents? Do the PR text, comments and docs say what the code really does? Is a type or a default assumed that the caller does not give? Cite the file and line that shows it.
