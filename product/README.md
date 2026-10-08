# keel Product (add-on, beta)

keel Product takes an idea from a product owner or product manager to the teams' stories:

    idea → brief → impact → decision → plan → delivery (keel Tasks or Jira) → outcome

keel writes and checks each stage; people decide at every gate. It reads the code for the impact (read only, one
analyst per repo), knows which team owns which repo (by hand or from CODEOWNERS), keeps every document as a versioned
commit in its own product repo, and hands each story to its repo's project as a keel task with its acceptance
criteria, so a developer starts it as a normal keel flow.

**Status: beta (0.1.0-beta.1).** It is not part of keel's own image and never moves `:latest`. It is built only into
the product edition image (`EDITION=product`). Do not use it for real work until its release gates pass.

## How it is versioned

keel Product has its own version, apart from keel's:

|                   | where                                                                                                | now                |
| ----------------- | ---------------------------------------------------------------------------------------------------- | ------------------ |
| keel Product      | `engine/keel_product/__init__.py` `VERSION`, `api/.../ProductAutoConfiguration.kt` `PRODUCT_VERSION` | 0.1.0-beta.1       |
| the keel it needs | `engine/keel_product/__init__.py` `requires`                                                         | `>=0.13.0,<0.15.0` |

keel's engine loads the add-on only when its version fits `requires` (else `GET /addons` lists it under problems), so a
keel upgrade never runs an add-on that was not made for it. keel's own code knows nothing about Product: the add-on
uses keel's extension points (engine `KEEL_ADDONS`, api `KeelAddon` and Spring auto-configuration, web
`product/web/index.tsx`), and keel's jar, engine and web bundle stay the same size without it.

## Modes

Settings › General › _What this keel does_: **Dev only**, **Product only** or **Product and Dev** (auto = both when
Product is installed). With both, each person picks a View (All, Product, Dev) under the project picker. Turning a part
off deletes nothing; the product api answers 409 while Product is off.

## Layout

    product/content/   workflows (product-discover, -impact, -decide, -plan, -outcome) and the 4 agents
    product/engine/    keel_product: actions (documents, plan check, presentation, decision), fake answers, routes
    product/api/       Spring auto-configuration: initiatives, teams, delivery, follow-ups (own tables, own Flyway history)
    product/web/       the Initiatives and Teams pages (a lazy chunk of keel's web)
    product/e2e/       the end-to-end script

## Tests

    cd engine && PYTHONPATH=../product/engine uv run pytest -q ../product/engine/tests
    cd api && ./gradlew productTest
    cd web && npx vitest run ../product/web
    docker build --build-arg EDITION=product -t keel-v2:product .
    python3 product/e2e/e2e.py --image keel-v2:product            # a throw-away keel-lab with the fake model

`product/e2e/e2e.py --real claude` runs the same walk on a running keel-lab with a real model.
