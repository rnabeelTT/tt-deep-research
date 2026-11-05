# Pydantic's mcp-run-python: a sandboxed Python (Pyodide in Deno) as an MCP tool, so agents can
# compute instead of doing arithmetic in their heads (docs/LOG.md).
FROM denoland/deno:bin-2.9.7 AS deno

FROM python:3.12-slim
COPY --from=deno /deno /usr/local/bin/deno
RUN pip install --no-cache-dir mcp-run-python==0.0.22 \
 && python -c "import mcp_run_python, pathlib, shutil; shutil.copytree(pathlib.Path(mcp_run_python.__file__).parent / 'deno', '/app')"
WORKDIR /app
# Pyodide and the server's packages into node_modules now, so starting needs no network.
RUN deno run --allow-net --allow-read=./node_modules --allow-write=./node_modules --node-modules-dir=auto src/main.ts noop
EXPOSE 9910
# The package's own `--disable-networking` also denies the server its port; this grants Deno only
# the listening socket, so code run in the sandbox reaches nothing else.
CMD ["deno", "run", "--allow-net=0.0.0.0:9910,[::]:9910", "--allow-read=./node_modules", "--node-modules-dir=auto", \
     "src/main.ts", "streamable_http", "--return-mode=xml", "--port=9910"]
