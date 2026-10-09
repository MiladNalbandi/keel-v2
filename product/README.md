# keel Product (add-on, beta)

keel Product takes an idea from a product owner or product manager to the teams' stories:

    idea → brief → impact → decision → plan → delivery (keel Tasks or Jira) → outcome

keel writes and checks each stage; people decide at every gate. It reads the code for the impact (read only, one
analyst per repo), knows which team owns which repo (by hand or from CODEOWNERS), keeps every document as a versioned
commit in its own product repo, and hands each story to its repo's project as a keel task with its acceptance
criteria, so a developer starts it as a normal keel flow.

**Status: beta (0.1.0-beta.1).** It is a plugin package (`product-<version>.kplug`), not part of keel's own image, and
it never moves `:latest`. The product edition image (`EDITION=product`) has it inside, in `/opt/keel-v2/plugins`. Do not
use it for real work until its release gates pass.

## How it is versioned

keel Product has its own version, apart from keel's:

|                   | where                                                                                                                             | now               |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| keel Product      | `keel-plugin.yml` `version`, `engine/keel_product/__init__.py` `VERSION`, `api/.../ProductAutoConfiguration.kt` `PRODUCT_VERSION` | 0.1.0-beta.1      |
| the keel it needs | `keel-plugin.yml` `requires.keel`, `engine/keel_product/__init__.py` `requires`                                                   | `>=0.15.0,<1.0.0` |

`engine/tests/test_manifest.py` checks that these are the same. At every start keel checks the manifest
(`requires.sdk`, `requires.keel`, `files.sha256`) and loads the plugin only when it fits (else it is listed under
problems), so a keel upgrade never runs a plugin that was not made for it. keel's own code knows nothing about
Product: the plugin uses keel's extension points (engine add-on, api `KeelAddon` and Spring auto-configuration, web
`product/web/index.tsx`), and keel's jar, engine and web bundle stay the same size without it.

## Modes

Settings › General › _What this keel does_: **Dev only**, **Product only** or **Product and Dev** (auto = both when
Product is installed). With both, each person picks a View (All, Product, Dev) under the project picker. Turning a part
off deletes nothing; the product api answers 409 while Product is off.

## Layout

    product/keel-plugin.yml   the plugin manifest (name, version, what keel it needs, its parts)
    product/build-plugin.sh   builds the parts and packs them (see below)
    product/content/          workflows (product-discover, -impact, -decide, -plan, -outcome) and the 4 agents
    product/engine/           keel_product: actions (documents, plan check, presentation, decision), fake answers, routes
    product/api/              Spring auto-configuration: initiatives, teams, delivery, follow-ups (own tables, own Flyway
                              history); built as a thin jar (cd api && ./gradlew productPluginJar)
    product/web/              the Initiatives and Teams pages (built on their own: npm run build:product in web/)
    product/e2e/              the end-to-end script

## Build the plugin package

    product/build-plugin.sh out              # builds the api jar and the web part, then packs them
    product/build-plugin.sh out --no-web     # does not build the web part (packs product/web/dist if it is there)

It writes `out/product-<version>.kplug` (a tar.gz, no top folder inside) and the same folder unpacked:

    out/product/<version>/
      keel-plugin.yml  README.md  files.sha256      files.sha256: "<sha256>  <path>" for every other file
      engine/keel_product/                          the engine part (no tests, no caches)
      api/keel-plugin-product.jar                   the api part
      web/index.js  web/style.css                   the web part
      content/                                      workflows and agents

The product image puts the unpacked folder in `/opt/keel-v2/plugins/product/<version>/`, and keel-start loads it at every
start.

## Tests

    cd engine && PYTHONPATH=../product/engine uv run pytest -q ../product/engine/tests
    cd api && ./gradlew productTest
    cd web && npx vitest run ../product/web
    docker build --build-arg EDITION=product -t keel-v2:product .
    python3 product/e2e/e2e.py --image keel-v2:product            # a throw-away keel-lab with the fake model

`product/e2e/e2e.py --real claude` runs the same walk on a running keel-lab with a real model.
