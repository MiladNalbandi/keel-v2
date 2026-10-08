# keel v2 — one image: web + api (Kotlin) + engine (Python, LangGraph) + keel v2's content + the agent CLIs.
# Everything is installed or built inside the image; nothing comes from your computer except this repo's source.
# keel v1 is a separate project and is not in the image (keel2 start --with-keel-v1 can mount one for its MCP server).
#
#   docker build -t keel-v2 .                                        (CLIs included, every plugin in plugins/ inside)
#   docker build --build-arg INSTALL_CLIS=0 -t keel-v2:slim .        (no claude/codex/copilot/opencode)
#   docker build --build-arg EDITION=product -t keel-v2:product .    (also the keel Product plugin inside: beta)
#   docker build --build-arg EDITION=core -t keel-v2:core .          (keel's core only, no plugin inside)
# EDITION (docs/plugins/11-step3-contract.md): full (the default; the old value dev means full) bakes every plugin in
# plugins/ into /opt/keel-v2/plugins, product = full + keel Product, core = none.
# Run:
#   docker run -p 127.0.0.1:8080:8080 -v /path/to/project:/workspace -v keel-data:/data keel-v2

# ---------- build the web app (keel's own; the plugins' web parts on their own) ----------
FROM node:20-bookworm-slim AS web
ARG EDITION=full
WORKDIR /src/web
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# keel's bundle is built before plugins/ and product/web are even here, so it never holds a plugin's pages
RUN npm run build
# The plugins' pages are their web parts: each built on its own with web's node_modules (build:plugins writes
# plugins/<name>/web/dist, build:product writes product/web/dist) and packed by the plugins stage. None with
# EDITION=core; keel Product's only with EDITION=product.
COPY plugins /src/plugins
COPY product/web /src/product/web
RUN rm -rf /src/product/web/dist /src/plugins/*/web/dist && mkdir -p /src/product/web/dist \
    && if [ "$EDITION" != "core" ]; then npm run build:plugins; fi \
    && if [ "$EDITION" = "product" ]; then npm run build:product; fi

# ---------- build the api (with the web app inside) ----------
FROM eclipse-temurin:21-jdk AS api
ARG EDITION=full
WORKDIR /src/api
COPY api/gradlew ./
COPY api/gradle ./gradle
COPY api/*.gradle.kts ./
RUN ./gradlew --no-daemon -q dependencies > /dev/null || true
COPY api/src ./src
COPY product/api /src/product/api
COPY plugins /src/plugins
COPY --from=web /src/web/dist ./src/main/resources/static
RUN ./gradlew --no-daemon -q bootJar -x test && cp build/libs/*.jar /app.jar
# The plugins' api parts are thin plugin jars (pluginJars: every plugins/<name>/api; productPluginJar), never inside
# keel's jar. /plugin stays empty with EDITION=core.
RUN mkdir -p /plugin && case "$EDITION" in \
      core) ;; \
      product) ./gradlew --no-daemon -q pluginJars productPluginJar ;; \
      *) ./gradlew --no-daemon -q pluginJars ;; \
    esac && find build/libs -name 'keel-plugin-*.jar' -exec cp {} /plugin/ \;

# ---------- the plugins: /out/<name>/<version>/, each packed by scripts/build-plugin.sh ----------
# (the same script as on a computer, with the parts built above). Every plugins/<name> unless EDITION=core, and keel
# Product (product/build-plugin.sh) with EDITION=product. /out stays empty with EDITION=core.
FROM node:20-bookworm-slim AS plugins
ARG EDITION=full
WORKDIR /src
COPY scripts/build-plugin.sh ./scripts/
COPY --from=web /src/plugins ./plugins
COPY product/keel-plugin.yml product/README.md product/build-plugin.sh ./product/
COPY product/engine ./product/engine
COPY product/content ./product/content
COPY --from=web /src/product/web/dist ./product/web/dist
COPY --from=api /plugin ./api/build/libs
RUN set -e; mkdir -p /out /tmp/plugins; \
    if [ "$EDITION" != "core" ]; then \
      for d in plugins/*/; do \
        if [ -f "$d/keel-plugin.yml" ]; then bash scripts/build-plugin.sh "$d" /tmp/plugins --no-build; fi; \
      done; \
    fi; \
    if [ "$EDITION" = "product" ]; then bash product/build-plugin.sh /tmp/plugins --no-build; fi; \
    find /tmp/plugins -mindepth 1 -maxdepth 1 -type d -exec mv {} /out/ \;

# ---------- runtime: Ubuntu 24.04 with a full JDK 21 (projects compile and test inside) ----------
FROM eclipse-temurin:21-jdk-noble
ARG INSTALL_CLIS=1
ARG EDITION=full
ARG NODE_MAJOR=20
ENV LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/engine/.venv/bin:/usr/local/bin:$PATH \
    KEEL_DATA=/data KEEL_WORKSPACE=/workspace KEEL_CONTENT=/opt/keel-v2/content \
    KEEL_ENGINE_URL=http://127.0.0.1:8090 KEEL_API_URL=http://127.0.0.1:8080 \
    UV_PROJECT_ENVIRONMENT=/opt/engine/.venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    CODEGRAPH_NO_DAEMON=1 CODEGRAPH_TELEMETRY=0 DO_NOT_TRACK=1 CODEGRAPH_NO_UPDATE_CHECK=1 \
    KEEL_EDITION=${EDITION}

# System tools, Python 3.12, Node.js (NodeSource) and the Docker CLI with compose + buildx (Docker's apt repo).
# python3-pytest + python-is-python3: a plain Python project (and keel's demo) runs `python -m pytest` out of the box;
# keel's own venv is kept off the PATH its projects and agents see (models/cli.py project_env).
# The Docker daemon is the host's: `keel2 --docker` mounts its socket, so tests can use Testcontainers or compose.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip python3-pytest python-is-python3 git tini curl ca-certificates gnupg sqlite3 \
        openssh-client make zip unzip \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs docker-ce-cli docker-compose-plugin docker-buildx-plugin \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*' \
    && git config --system user.name "keel" && git config --system user.email "keel@localhost"

# The agent CLIs, and CodeGraph (the code index agents query through MCP; content/NOTICE.md). CodeGraph is pinned:
# keel parses its `status --json` and starts `serve --mcp`.
ARG CODEGRAPH_VERSION=1.6.2
RUN if [ "$INSTALL_CLIS" = "1" ]; then \
      npm i -g --no-audit --no-fund @anthropic-ai/claude-code @openai/codex @github/copilot opencode-ai \
        "@colbymchenry/codegraph@${CODEGRAPH_VERSION}" && npm cache clean --force && codegraph --version; \
    fi

# The engine: installed with uv into a venv that uses this image's own Python
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
WORKDIR /opt/engine
COPY engine/pyproject.toml engine/uv.lock* ./
RUN uv sync --no-dev --no-install-project --python /usr/bin/python3 $( [ -f uv.lock ] && echo --frozen )
COPY engine/ ./
RUN uv sync --no-dev --python /usr/bin/python3 $( [ -f uv.lock ] && echo --frozen )

# The api (built above) and the start script
COPY --from=api /app.jar /opt/api/app.jar
# keel v2's own agents, skills, stacks, packs and templates (content/README.md)
COPY content /opt/keel-v2/content
# The plugins inside the image (docs/plugins): <name>/<version>/keel-plugin.yml. Every plugin of plugins/ (and keel
# Product with EDITION=product; none with EDITION=core). keel-start resolves them at every start, together with the
# ones a person installed (/data/plugins/store).
COPY --from=plugins /out /opt/keel-v2/plugins
ENV KEEL_PLUGINS_IMAGE=/opt/keel-v2/plugins
COPY docker/keel-start /usr/local/bin/keel-start
RUN chmod +x /usr/local/bin/keel-start \
    && (id -u ubuntu >/dev/null 2>&1 && userdel -r ubuntu || true) \
    && useradd -m -u 1000 keel && mkdir -p /data /workspace && chown keel:keel /data /workspace

USER keel
WORKDIR /workspace
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 CMD curl -fs http://127.0.0.1:8080/api/health || exit 1
ENTRYPOINT ["tini", "--", "keel-start"]
