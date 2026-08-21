# myGEKKO MCP server — streamable-HTTP, containerised for the Hostinger Docker stack.
# Build context = this directory (packages/mygekko-mcp).
FROM python:3.12-slim

WORKDIR /app

# Install the package (hatchling builds the wheel from ./src). Copy only what's needed first for caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# In a container the HTTP server must bind all interfaces so the reverse proxy can reach it.
ENV GEKKO_HTTP_HOST=0.0.0.0 \
    GEKKO_HTTP_PORT=8000 \
    GEKKO_HTTP_PATH=/mcp \
    GEKKO_ALLOW_WRITE=false

EXPOSE 8000

# Everything else (GEKKO_MULTI_TENANT, GEKKO_RESOLVE_URL, GEKKO_RESOLVE_SECRET) comes from the env_file.
CMD ["python", "-m", "mygekko_mcp", "--transport", "streamable-http"]
