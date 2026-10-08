# 6 · Spikes: the two risky ideas of step 1, tried for real

Before step 1 we tried its two riskiest ideas on keel 0.14.0 (`main` at `a6f5f34`), outside the repo. Both work.
Date: 2026-10-08.

## 6.1 The api loads a plugin jar from a folder (no rebuild)

**Question:** can keel's normal api jar, which has **no** Product code in it, load keel Product's api part from a
folder when it starts?

```
 keel-api.jar (44 MB, 0 Product classes)          plugins/product/api/keel-plugin-product.jar (213 KB)
        │                                          = product classes + META-INF/spring/…AutoConfiguration.imports
        │                                            + db/product/P1__product.sql
        ▼
 java -Dloader.path=plugins/product/api/keel-plugin-product.jar \
      -cp keel-api.jar org.springframework.boot.loader.launch.PropertiesLauncher
```

How we built the thin jar (Product needs no library beyond core's):

```bash
cd api && ./gradlew bootJar productClasses
jar cf keel-plugin-product.jar -C build/classes/kotlin/product . -C build/resources/product .
```

**Result:**

| check                          | with the plugin jar                                                       | with an empty `loader.path`       |
| ------------------------------ | ------------------------------------------------------------------------- | --------------------------------- |
| api starts                     | yes, in 2.1 s                                                             | yes                               |
| `/api/features`                | mode `both`, add-on `product 0.1.0-beta.1`, screens Initiatives and Teams | mode `dev`, no add-ons (as today) |
| Product's tables               | 11 `product_*` tables + its own `product_schema_history`                  | none                              |
| core's `flyway_schema_history` | untouched                                                                 | untouched                         |

So `PropertiesLauncher` finds `Start-Class` in the jar's manifest by itself; no `loader.main` is needed. Spring finds
the plugin's auto-configuration, and Product's own Flyway runs in its own history.

## 6.2 The browser loads a plugin's web part at run time, with one React

**Question:** can a plugin's web part, built on its own, run inside keel's page and share keel's React (hooks,
context)?

```
 index.html
   <script type="importmap">  react → /assets/sdk/react.js, react/jsx-runtime → …, @keel/web-sdk → …
   core.js       bundles React; sets window.__keel = { React, ReactDOM, jsxRuntime, sdk }
   /assets/sdk/react.js        "const m = window.__keel.React; export const { useState, … } = m;"
                               (the names are listed at build time)
   import("/plugins/demo/1.0.0/web/index.js")       853 bytes: `import { useState } from "react"` stays a bare import
```

The plugin used `useState`, `useId` and `useKeel()` (a context that core provides).

**Result:**

- The plugin page rendered inside core's tree.
- It read core's context (`ludus-engine`).
- The button counted clicks (state works).
- `window.__keel.React === React` was `true`.
- No console errors.

With two Reacts the hooks would fail with "Invalid hook call".

**For step 1:**

- core's Vite build writes these shim files (one per shared name), and `index.html` gets the import map.
- A plugin's Vite build marks `react`, `react-dom`, `react/jsx-runtime` and `@keel/web-sdk` as `external`.

## 6.3 What is still open for step 1

- **Engine:** loading a package from a folder already works today (the product image adds a `PYTHONPATH` entry and
  sets `KEEL_ADDONS`). Step 1 only moves this into the engine (`sys.path`), so project commands do not see it.
- **keel-start:** the restart loop (exit code 75) and the fallback to the last good set are new. They are simple
  shell, but they need their own tests.
- **Product's version check:** the api side has none yet (the engine side has `requires`). The resolver will check it
  before the jar is put on `loader.path`.
