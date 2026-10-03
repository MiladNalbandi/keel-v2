# keel v2 — one image: web + api (Kotlin) + engine (Python, LangGraph) + keel v1.
#
# Build (keel v1 source comes in as a named build context, so nothing is fetched from git):
#   docker build --build-context keel=../keel -t keel-v2 .            (claude, codex, copilot, opencode included)
#   docker build --build-context keel=../keel --build-arg INSTALL_CLIS=0 -t keel-v2:slim .   (no CLIs)
# Run:
#   docker run -p 127.0.0.1:8080:8080 -v /path/to/project:/workspace -v keel-data:/data keel-v2

# ---------- web ----------
FROM node:20-bookworm-slim AS web
WORKDIR /src/web
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------- api ----------
FROM eclipse-temurin:21-jdk AS api
WORKDIR /src/api
COPY api/gradlew ./
COPY api/gradle ./gradle
COPY api/*.gradle.kts ./
RUN ./gradlew --no-daemon -q dependencies > /dev/null || true
COPY api/src ./src
COPY --from=web /src/web/dist ./src/main/resources/static
RUN ./gradlew --no-daemon -q bootJar -x test && cp build/libs/*.jar /app.jar

# ---------- engine ----------
FROM python:3.12-slim-bookworm AS engine
COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv
WORKDIR /opt/engine
ENV UV_PROJECT_ENVIRONMENT=/opt/engine/.venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY engine/pyproject.toml engine/uv.lock* ./
RUN uv sync --no-dev --no-install-project $( [ -f uv.lock ] && echo --frozen )
COPY engine/ ./
RUN uv sync --no-dev $( [ -f uv.lock ] && echo --frozen )

# ---------- runtime ----------
FROM python:3.12-slim-bookworm
ARG INSTALL_CLIS=1
ENV LANG=C.UTF-8 \
    JAVA_HOME=/opt/java \
    PATH=/opt/java/bin:/opt/engine/.venv/bin:/usr/local/bin:$PATH \
    KEEL_DATA=/data KEEL_WORKSPACE=/workspace KEEL_HOME=/opt/keel \
    KEEL_ENGINE_URL=http://127.0.0.1:8090 KEEL_API_URL=http://127.0.0.1:8080

RUN apt-get update && apt-get install -y --no-install-recommends git tini curl ca-certificates openssh-client sqlite3 \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*' \
    && git config --system user.name "keel" && git config --system user.email "keel@localhost"

COPY --from=eclipse-temurin:21-jre /opt/java/openjdk /opt/java
COPY --from=node:20-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:20-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && if [ "$INSTALL_CLIS" = "1" ]; then npm i -g --no-audit --no-fund @anthropic-ai/claude-code @openai/codex @github/copilot opencode-ai; fi

COPY --from=keel / /opt/keel
RUN rm -rf /opt/keel/.git && ln -s /opt/keel/bin/keel /usr/local/bin/keel && chmod +x /opt/keel/bin/keel

COPY --from=engine /opt/engine /opt/engine
COPY --from=api /app.jar /opt/api/app.jar
COPY docker/keel-start /usr/local/bin/keel-start
RUN chmod +x /usr/local/bin/keel-start \
    && useradd -m -u 1000 keel && mkdir -p /data /workspace && chown keel:keel /data /workspace

USER keel
WORKDIR /workspace
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 CMD curl -fs http://127.0.0.1:8080/api/health || exit 1
ENTRYPOINT ["tini", "--", "keel-start"]
