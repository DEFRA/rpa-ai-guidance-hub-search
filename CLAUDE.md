# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python/FastAPI backend service for the RPA AI Guidance Hub, built from DEFRA's CDP python backend template and deployed to the Core Delivery Platform (CDP). It runs on port 8085.

## Commands

```bash
uv sync                      # install deps (dev group included by default)
uv run task format           # ruff format + ruff check --fix
uv run task lint             # ruff format --check + ruff check (no fixes)
uv run task typecheck        # mypy over app/ and tests/
uv run task test             # lint + typecheck + pytest with coverage reports
uv run pytest                # tests only
uv run pytest tests/health/test_health_router.py::TestHealthProbe::test_health  # one test
pre-commit install           # git hooks (ruff + hygiene checks)
```

Running locally:

```bash
docker compose up --build                                # everything, including this service
docker compose up -d floci mongodb redis                 # dependencies only
uv run --env-file .env rpa-ai-guidance-hub-search           # service on host, deps in docker
```

Copy `.env.example` to `.env` first — `AppConfig` has required fields and refuses to start without them. `compose.debug.yaml` is an overlay that runs the app under debugpy (port 5678, waits for client).

## Gotchas

- **`app/` uses namespace packages — there are no `__init__.py` files.** pytest is configured with `--import-mode=importlib` and mypy with `explicit_package_bases`. Don't add `__init__.py` when creating a new subpackage.
- **Import modules, not names** ([AICE Python Style Guide](https://github.com/DEFRA/aice-team/blob/main/plugins/python/skills/python-style-guide/references/python-style-guide.md) §2.1.2, after [Google's](https://google.github.io/styleguide/pyguide.html#22-imports) §2.2): `from app.guidance import ids`, then `ids.DocumentId`; never `from app.guidance.ids import DocumentId`. The one exception is types from `typing` and `collections.abc`. No relative imports. Ruff does not enforce this, so check it by eye.
- `tests/conftest.py` seeds required environment variables with `os.environ.setdefault` *before* any app import. `AppConfig` is a validate-on-construct singleton (`app.config.get_config()`), so tests that need different config must mock `get_config`, not mutate env vars after import.
- The ruff rev in `.pre-commit-config.yaml` must match the ruff version in `uv.lock`, or the hook's formatter fights `uv run task lint`.
- mypy runs with `disallow_untyped_defs` on `app/` (tests are exempt via a `[[tool.mypy.overrides]]` entry).
- `.env` holds *host-side* values (e.g. `FLOCI_ENDPOINT_URL=http://localhost:4566`). `compose.yml` layers `environment:` over `env_file: .env` to re-point them at the container network. `tests/test_compose_config.py` guards that these overrides stay in place — add one there whenever a new host-oriented endpoint enters `.env`.

## Architecture

- **Config** (`app/config.py`): pydantic-settings `AppConfig` read from environment/`.env`, cached in a module-level singleton behind `get_config()`. `CLAUDE_SONNET_MODEL_CONFIG` is a packed string — `model_id,inference_profile[,guardrail_id:guardrail_version]` — parsed by a field validator into `BedrockModelConfig`; a computed `bedrock` field exposes it as structured config. LLM access is AWS Bedrock via inference-profile ARNs with optional guardrails.
- **Entrypoint** (`app/entrypoints/fastapi.py`): builds the `app` instance, mounts routers and middleware, and exposes `main()` — the target of both `[project.scripts]` and the Dockerfile `CMD` (`/home/nonroot/.venv/bin/rpa-ai-guidance-hub-search`).
- **Routers**: one package per feature (`app/health/`, `app/review/`), each exporting an `APIRouter` that the entrypoint mounts. `GET /health` is required by CDP — do not remove it.
- **Domains**: `app/records/` holds the canonical, source-neutral records: `DocumentHeader` (with the `url` a reader views it at, and `source`: what its source says about it, typed per source type in `source_metadata`; only `GuideMetadata` today, empty until a delivery path supplies it), `SectionRecord` (whole sections, with blocks and a section `content_hash`) and the derived `ChunkRecord`, plus the section JSONL file and `SectionStore`, and the staged `ChunkSet` file and `ChunkSetStore`. `app/indexing/` is the write side, in two steps (`stages.py`) that every entry point runs in order, so preparing can later scale out behind a single writer: `prepare/` turns an `IndexJob` into an `ApplyJob` (a per-source `SourceReader`, only `guide_source` + `guide_markdown` today, makes a `SourceDocument`, and `guide_cms` holds pydantic models of the CMS API's guide and converts them to `GuideMetadata`; `sectioning.build` makes the canonical sections; a named `ChunkingStrategy`, `packed_chunking` / `packed-800`, derives chunks; `DocumentPreparer` stores the sections and stages the chunk set), and `apply/` writes the staged chunk set to the index (`DocumentApplier`, and `publish` around the active build). `prepare/` never imports `app.index`, and `apply/` never imports `prepare/`. `app/search/` is the read side (query → results → HTTP). Both depend on `app/index/` (the index fields, the `ChunkIndex` protocols, the Tantivy engine, and its builds and active build) and `app/matching/` (what counts as a word or title match), and never on each other. `tests/test_import_boundaries.py` enforces this. Add a source (GOV.UK content items next) by adding its metadata type to `source_metadata`, pydantic models of its upstream payload beside its reader, and a reader that returns a `SourceDocument`; add a retrieval style by writing a strategy. Sections are canonical; chunk sets are staged copies the apply step reads, and can always be prepared again.
- **Versioning**: each contract carries its own version. HTTP responses are versioned by URL prefix: `app/search/v1/` holds the `/v1` router and schemas, converting from the version-neutral `app/search/service.py` models; within a version, changes are additive, and a breaking change is a sibling `v2/` package. Index builds and their Mongo pointer are keyed by `index_schema.SCHEMA_VERSION` (`tests/index/test_index_schema.py` pins it against the field list, so bump it when `CHUNK_INDEX` changes). Section files carry `jsonl.SCHEMA_VERSION` and chunk sets `chunk_sets.SCHEMA_VERSION`; a chunking strategy's name is in every chunk id, and `index_schema.CHUNK_STRATEGY` names the one an index version holds, so rename a strategy (and bump the index schema version) when its output changes. Indexing's inbound contract is `app/ingestion/`: the CMS's `PUT` and `DELETE /documents/{documentId}/versions/{versionId}` routes, mounted only when `INGESTION_MODE=http`, whose body schemas convert to the version-neutral `IndexJob` (to prepare) and `RemoveJob`, keeping transport types out of `app/indexing/`. Whatever delivers jobs later (a queue, behind `dispatch.ApplyDispatcher`) gets its own entry-point package the same way.
- **Ids**: document and version ids are UUIDs for every source (`app/records/ids.py`), and each source decides where they come from. Guides keep the CMS's ids, which are always UUIDv7; the guide parser and `guide_cms` refuse anything else. A source with no UUIDs of its own (a GOV.UK content item) mints them with `ids.minted_id`: UUIDv5 under a namespace of its own, from its native identity (`content_id` + locale for the document, the document + content hash for a version), so re-reading the same content gives the same ids and sources never collide. `IndexJob.version_id` is None for such a source, because the reader mints it while reading; the `ApplyJob` takes ids from the header the reader made. Keep native ids in the source's metadata type.
- **Schemas, models and ids**: `schemas.py` holds Pydantic HTTP shapes that convert to and from `models.py`, whose dataclasses convert themselves to and from Mongo records (`to_document()`/`from_document()`); routers only call the conversions. Our document and version ids are `NewType`s over `uuid.UUID` (v7, minted in `app/guidance/ids.py`), stored as native BSON UUIDs; cdp-uploader's ids stay `str`. See the README's "Where code goes".
- **Shared plumbing** (`app/common/`): lazily-initialised global singletons exposed as FastAPI dependencies — `mongo.py` (async PyMongo client, custom CA from truststore env vars), `http_client.py`, `tracing.py` (propagates the `x-cdp-request-id` header), `metrics.py` (aws-embedded-metrics, gated by `ENABLE_METRICS`), `tls.py` (loads `TRUSTSTORE_*` env vars as CA certs, mirroring CDP's node convention).
- **Local AWS** is `floci` (LocalStack-style emulator) on port 4566 with dummy credentials. Add local buckets/queues to `compose/floci/start.d/10-setup-resources.sh`, which runs at container start (it creates the source-docs and docs buckets; neither is versioned, because a document's versions are separate keys recorded in the `document_versions` collection rather than revisions of one key). `compose/mongo/10-init.js` seeds MongoDB (also a placeholder).
- **Tests** (`tests/`) mirror the `app/` package layout. HTTP-level tests use Starlette's `TestClient` against the app; there is no running-dependency requirement for the unit suite.
- **Docker**: multi-stage `Dockerfile` with `development` and `production` targets on CDP's `defradigital/python*` base images. `LOG_CONFIG` selects the logging setup (`logging-dev.json` in development, `logging.json` — ECS-formatted — in production). In CDP, config and secrets come from CDP conventions, not `.env`.
