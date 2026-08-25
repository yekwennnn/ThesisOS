# Container deployment

The top-level `Dockerfile` packages only the FastAPI/core distribution. It does
not copy `apps/web`, repository credentials, local workspaces, tests, or example
research data into the image.

## Local, localhost-only smoke deployment

Create an untracked `.env` only when a real adapter needs configuration, then
start the example stack:

```console
docker compose -f docker-compose.example.yml up --build
curl --fail http://127.0.0.1:8000/health
```

The example publishes the API only on `127.0.0.1`. The application does not yet
provide end-user authentication, tenant isolation, TLS, or request rate limits,
so it must not be exposed directly to the public internet. A production website
must call it through an authenticated same-origin backend or trusted API gateway.

The named `thesisos-workspace` volume holds immutable documents, artifacts, and
version pointers. Back it up before upgrades. `docker compose down` preserves
the volume; `docker compose down --volumes` permanently removes it.

## Provider configuration

All provider configuration is supplied at runtime. Never add credential values
to the Dockerfile, Compose file, image layers, source control, model request
metadata, or browser code. For local Compose, `.env` is ignored by Git. On a
hosted platform, inject credentials from its secret manager instead; remember
that container environment values remain visible to Docker administrators.

The model boundary is a no-shell subprocess argv protocol. A representative
runtime configuration is:

```dotenv
THESISOS_MODEL_IDENTIFIER=gpt-5.5
THESISOS_MODEL_ADAPTER_ARGV=["thesisos-openai-adapter"]
OPENAI_API_KEY=replace-in-your-secret-manager
```

The bundled `thesisos-openai-adapter` implements
`docs/model-adapter-protocol.md`. A different referenced executable must exist
inside a derived image or be mounted read-only at deployment time and implement
that same protocol. Do not place a secret in the argv array: argv can be exposed
by process inspection, while the adapter already inherits the container
environment.

`WIND_API_KEY` is likewise passed only through the runtime environment. The
built-in Wind provider uses a fixed, no-shell CLI route. For example:

```dotenv
THESISOS_FINANCE_PROVIDER=wind
THESISOS_WIND_CLI_ARGV=["node","/opt/wind-mcp-skill/scripts/cli.mjs"]
WIND_API_KEY=replace-in-your-secret-manager
```

The referenced Wind CLI and its Node.js runtime are not copied from a developer
machine into the base image. Install them in an auditable derived image or mount
the CLI read-only, and pin/test their versions before enabling this provider.
`THESISOS_FINANCE_PROVIDER=disabled` remains the safe default. Selecting `wind`
without a usable CLI argv intentionally makes startup fail instead of silently
returning demo data.

## Production requirements

- Pin `THESISOS_PYTHON_IMAGE` to an approved immutable image digest and rebuild
  regularly for security updates.
- Terminate TLS and enforce authentication/authorization before traffic reaches
  the API. Restrict the container or service network to that gateway.
- Keep one Uvicorn process and one container replica per local workspace. The
  filesystem version store is not a shared multi-writer database.
- Persist `/var/lib/thesisos`; keep the rest of the filesystem read-only and a
  writable, non-executable `/tmp` for bounded document parsing.
- Set upload/body limits at the gateway no higher than
  `THESISOS_MAX_UPLOAD_BYTES`, and apply request timeouts and rate limits there.
- Monitor both HTTP status and the provider fields returned by `/health`. A 200
  response means the API process and local storage are alive; model or finance
  providers may still report `configured: false`.
- Run model adapters and financial connectors with the minimum outbound network
  access they need. Do not grant the container Docker socket, host filesystem,
  Linux capabilities, or root privileges.

The image runs as UID/GID `10001`, drops all Linux capabilities in the Compose
example, sets `no-new-privileges`, and uses a persistent volume plus an ephemeral
`tmpfs` rather than making the image filesystem writable.
