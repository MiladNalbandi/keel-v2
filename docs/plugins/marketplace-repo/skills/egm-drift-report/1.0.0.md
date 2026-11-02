---
name: egm-drift-report
license: Apache-2.0
disable-model-invocation: true
description: >-
  Runs drift detection on all design documents to check for document and
  code consistency. Produces an actionable report with differences. Use when the
  user asks to detect drift or check for consistency across all design documents
  or READMEs in a directory.
---

> **In keel.** This skill is from Google's Elephant-Goldfish Model (EGM, https://github.com/google/elephant-goldfish-model, commit `403d8881`, Apache-2.0; see keel's content/NOTICE.md), unchanged below this note. In keel, the design-review flow runs EGM's goldfish reviewers itself: each one is a keel agent (`egm-zoom-out`, `egm-comprehension`, `egm-design`, `egm-structural`, `egm-style`, `egm-implementation-readiness`, `egm-codebase-details`) in a fresh session of its own, and keel merges their findings into one report. Inside a keel step, do not start subagents and do not ask questions with a question tool: do the part the step asks and answer in the format the step gives. EGM's `agents/<name>.md` files are those keel agents; its `resources/` files (the document standards) are in the `egm-review` skill's `references/`.

# Detect Drift In Designs and Code

When running in Codex or ChatGPT Work, first read [Codex integration](../../docs/codex.md).
In Claude Code or Cowork, read `${CLAUDE_PLUGIN_ROOT}/docs/claude.md` first.

When the user asks to detect drift across all designs or a set of READMEs, the
goal is to identify all instances where the codebase has diverged or failed to
implement the specifications described by those designs or READMEs.

1.  **Check Input**: Verify a directory containing design documents or READMEs
    is provided. If not provided, ask the user to specify the target directory.
2.  **List Documents**: List all files in the design directory.
3.  **Analyze**: For EACH design document:

    *   **Contextualize**: Read the design document. Identify the intent,
        proposed changes, and specific filenames/symbols mentioned.
    *   **Locate Source**: Find the corresponding source code files in the
        source tree. If paths aren't explicit, use code search or grep to
        locate the relevant classes, functions, or API endpoints mentioned in
        the doc. Prompt the user to add missing links to the document to make
        future detection cheaper.
    *   **Verify**: Read the current state of those source files.
    *   **Detect Drift**: Compare the design vs. reality. Look specifically for:
        *   Missing Features: Logic described in the doc but absent in the code.
        *   Incomplete Models: Dataclasses/Classes/Structs/Protos missing fields
            specified in the design.
        *   Logic Shortcuts: Hardcoded values where dynamic logic was planned.
        *   Architectural Divergence: Implementation using different
            patterns/libraries than proposed.
    *   **Filter**: Report ONLY the differences. Do not describe what is working
        or what matches. If the code matches the design perfectly, output "No
        drift detected."

4.  **Report**: Produce a single report listing all discrepancies found.

    *   Append the analysis for each file to `{YYYY-MM-DD}_DRIFT_REPORT.md`
        using your file-writing tool.
    *   Strict Format:

        ```markdown
        ## <DESIGN_DOC_NAME>

        <Bulleted list of specific divergences found>
        <Or "No drift detected">
        ```

5.  **Guidance**

    -   Be rigorous. If a design doc mentions a specific object, field or file
        name and the code doesn't have it, that is a reportable drift. Double
        check for coverage. If there is some part of the logic that is not
        referenced in a design or README, this is a reportable drift.
    -   Read files liberally to ensure you are looking at the current state
        of the code, not relying on memory.
    -   Process the files sequentially to ensure the tool output buffer doesn't
        overflow.
