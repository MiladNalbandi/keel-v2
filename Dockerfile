# keel v2 — one image: web + api (Kotlin) + engine (Python, LangGraph) + keel v1 + the agent CLIs.
# Everything is installed or built inside the image; nothing comes from your computer except this repo's source.
#
#   docker build -t keel-v2 .                                        (keel v1 from GitHub main, CLIs included)
#   docker build --build-arg KEEL_REF=v0.67.0 -t keel-v2 .           (pin keel v1 to a tag or branch)
#   docker build --build-arg INSTALL_CLIS=0 -t keel-v2:slim .        (no claude/codex/copilot/opencode)
# Run:
#   docker run -p 127.0.0.1:8080:8080 -v /path/to/project:/workspace -v keel-data:/data keel-v2

# ---------- build the web app ----------
FROM node:20-bookworm-slim AS web
WORKDIR /src/web
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------- build the api (with the web app inside) ----------
FROM eclipse-temurin:21-jdk AS api
WORKDIR /src/api
COPY api/gradlew ./
COPY api/gradle ./gradle
COPY api/*.gradle.kts ./
RUN ./gradlew --no-daemon -q dependencies > /dev/null || true
COPY api/src ./src
COPY --from=web /src/web/dist ./src/main/resources/static
RUN ./gradlew --no-daemon -q bootJar -x test && cp build/libs/*.jar /app.jar

# ---------- runtime: Ubuntu 24.04 with Java 21 ----------
FROM eclipse-temurin:21-jre-noble
ARG INSTALL_CLIS=1
ARG KEEL_REPO=https://github.com/MiladNalbandi/keel.git
ARG KEEL_REF=main
ARG NODE_MAJOR=20
ENV LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/engine/.venv/bin:/usr/local/bin:$PATH \
    KEEL_DATA=/data KEEL_WORKSPACE=/workspace KEEL_HOME=/opt/keel \
    KEEL_ENGINE_URL=http://127.0.0.1:8090 KEEL_API_URL=http://127.0.0.1:8080 \
    UV_PROJECT_ENVIRONMENT=/opt/engine/.venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

# System tools, Python 3.12 and Node.js (from NodeSource)
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv git tini curl ca-certificates gnupg sqlite3 openssh-client \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*' \
    && git config --system user.name "keel" && git config --system user.email "keel@localhost"

# The agent CLIs
RUN if [ "$INSTALL_CLIS" = "1" ]; then \
      npm i -g --no-audit --no-fund @anthropic-ai/claude-code @openai/codex @github/copilot opencode-ai && npm cache clean --force; \
    fi

# keel v1 from GitHub (no npm dependencies; node is enough)
RUN git clone --depth 1 --branch "$KEEL_REF" "$KEEL_REPO" /opt/keel \
    && rm -rf /opt/keel/.git \
    && chmod +x /opt/keel/bin/keel && ln -s /opt/keel/bin/keel /usr/local/bin/keel \
    && node -p "'keel ' + require('/opt/keel/.claude-plugin/plugin.json').version"

# The engine: installed with uv into a venv that uses this image's own Python
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
WORKDIR /opt/engine
COPY engine/pyproject.toml engine/uv.lock* ./
RUN uv sync --no-dev --no-install-project --python /usr/bin/python3 $( [ -f uv.lock ] && echo --frozen )
COPY engine/ ./
RUN uv sync --no-dev --python /usr/bin/python3 $( [ -f uv.lock ] && echo --frozen )

# The api (built above) and the start script
COPY --from=api /app.jar /opt/api/app.jar
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
