# content — what keel's agents read

Everything keel v2 gives its agents as text lives here: who each agent is, the skills they load,
the stacks keel recognises, the optional packs and the templates. Both the engine and the API read
this folder; nothing reads agents, skills, stacks or templates from anywhere else.

Where it is:

| Where | Path |
|---|---|
| Docker image | `/opt/keel-v2/content` (`KEEL_CONTENT` is set to it) |
| Any install | `KEEL_CONTENT=<path>` wins over everything |
| Dev checkout | `<repo>/content`, found from the engine's and the API's own location |

## Folders

| Folder | What it is | Who reads it |
|---|---|---|
| `agents/*.md` | One file per agent. YAML front matter (`name`, `description`, `tools`, `model`, `maxTurns`, `disallowedTools`), then the agent's role text. | Engine: the role text is the system prompt, `maxTurns` caps one step (`runtime/prompts.py`). API: the Agents page and the default tools/model (`agents/AgentCatalog.kt`). |
| `skills/<id>/SKILL.md` | A skill: front matter `name` + `description`, then the text. `references/*.md` are longer pages the skill points at; `examples/` are sample files. | API: the Skill hub and the skill text sent to each agent (`skills/SkillService.kt`). Engine: `references/x.md` in a skill becomes its full path here, so an agent opens it directly. |
| `stacks/*.yml` | Built-in stacks (`kotlin-spring`, `ts-react`): how to detect them, test layers, commands, tools. | API: the Stacks page and stack detection (`stacks/StackService.kt`). |
| `packs/<name>/` | Optional stacks that are not used until a project installs one (`django`, `react-js`, `symfony`). | API: listed on the Stacks page as installable. |
| `templates/` | Files keel writes into a project: `knowledge/` (the index and six knowledge-base sections the librarian fills), `config.yml` (`.keel/config.yml`), `spec.md`, compose/devcontainer/smoke/Playwright files, and `starter/` for a new project. | Engine: the librarian is pointed at `templates/knowledge/`. |

## A pack

Each pack is one folder, in the shape keel has always read a pack in:

```
packs/<name>/
  stack.yml                  the stack definition (same keys as stacks/*.yml)
  skills/<id>/SKILL.md       its testing + implementation skills (shown as "keel pack" skills)
  references/*.md            its own architecture placement references (symfony: PHP)
  templates/starter/         what a new project of this stack starts with
```

Paths inside `stack.yml` (`skill_files`, `arch_refs`, `starter.from`) are relative to the pack
folder. Installing a pack copies the folder to `<project>/.keel/stacks/<name>/`.

## Rules for this folder

- Agents never run keel commands: keel runs the tests, makes the commits and moves between phases
  after them. Text here says what to do, never `keel <command>` or a slash command.
- Say "keel does X" only when keel v2 really does X.
- `engine/tests/test_content.py` checks the front matter, the YAML and the forbidden words.

Most files here started as copies of keel v1; see `NOTICE.md`.
