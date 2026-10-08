# keel v2 — one image: web + api (Kotlin) + engine (Python, LangGraph) + keel v2's content + the agent CLIs.
# Everything is installed or built inside the image; nothing comes from your computer except this repo's source.
# keel v1 is a separate project and is not in the image (keel2 start --with-keel-v1 can mount one for its MCP server).
#
#   docker build -t keel-v2 .                                        (CLIs included)
#   docker build --build-arg INSTALL_CLIS=0 -t keel-v2:slim .        (no claude/codex/copilot/opencode)
#   docker build --build-arg EDITION=product -t keel-v2:product .    (with keel Product, the add-on in product/: beta)
# Run:
#   docker run -p 127.0.0.1:8080:8080 -v /path/to/project:/workspace -v keel-data:/data keel-v2

# ---------- build the web app ----------
FROM node:20-bookworm-slim AS web
ARG EDITION=dev
WORKDIR /src/web
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# keel Product's pages sit next to keel's web (../product/web) only in the product edition; keel's own bundle stays as it is
COPY product/web /src/product/web
RUN if [ "$EDITION" != "product" ]; then rm -rf /src/product; fi && npm run build

# ---------- build the api (with the web app inside) ----------
FROM eclipse-temurin:21-jdk AS api
ARG EDITION=dev
WORKDIR /src/api
COPY api/gradlew ./
COPY api/gradle ./gradle
COPY api/*.gradle.kts ./
RUN ./gradlew --no-daemon -q dependencies > /dev/null || true
COPY api/src ./src
COPY product/api /src/product/api
COPY --from=web /src/web/dist ./src/main/resources/static
RUN ./gradlew --no-daemon -q bootJar -x test && cp build/libs/*.jar /app.jar
# the product edition's jar is keel's api plus the add-on (productBootJar); keel's own jar never holds it
RUN if [ "$EDITION" = "product" ]; then ./gradlew --no-daemon -q productBootJar && cp build/libs/keel-api-product.jar /app.jar; fi

# ---------- keel Product's engine part and content: empty unless EDITION=product ----------
FROM node:20-bookworm-slim AS product
ARG EDITION=dev
COPY product/engine /in/engine
COPY product/content /in/content
RUN mkdir -p /out && if [ "$EDITION" = "product" ]; then cp -r /in/engine/keel_product /in/content /out/ && find /out -name __pycache__ -prune -exec rm -rf {} +; fi

# ---------- runtime: Ubuntu 24.04 with a full JDK 21 (projects compile and test inside) ----------
FROM eclipse-temurin:21-jdk-noble
ARG INSTALL_CLIS=1
ARG EDITION=dev
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
# keel Product (product edition only): /opt/keel-product/keel_product (engine add-on) and /opt/keel-product/content
COPY --from=product /out /opt/keel-product
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
