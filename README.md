# rpa-ai-guidance-hub-search

This is work-in-progress. See [To Do List](./TODO.md)

- [rpa-ai-guidance-hub-search](#rpa-ai-guidance-hub-search)
  - [Requirements](#requirements)
    - [Python](#python)
    - [Linting and Formatting](#linting-and-formatting)
    - [Docker](#docker)
  - [Local development](#local-development)
    - [Setup & Configuration](#setup--configuration)
    - [Development](#development)
    - [Testing](#testing)
    - [Production Mode](#production-mode)
  - [Where code goes](#where-code-goes)
    - [Schemas and models](#schemas-and-models)
    - [Ids](#ids)
  - [API endpoints](#api-endpoints)
  - [Custom Cloudwatch Metrics](#custom-cloudwatch-metrics)
  - [Pipelines](#pipelines)
    - [Dependabot](#dependabot)
    - [SonarCloud](#sonarcloud)
  - [Licence](#licence)
    - [About the licence](#about-the-licence)

## Requirements

### Python

Please install python `>= 3.14` (see `.python-version`) and `pipx` in your environment. This project uses [uv](https://github.com/astral-sh/uv) to manage the environment and dependencies.

```python
# install uv via pipx
pipx install uv

# sync dependencies
uv sync

# source python venv
source .venv/bin/activate

# install the pre-commit hooks
pre-commit install
```

This opinionated template uses the [`Fast API`](https://fastapi.tiangolo.com/) Python API framework.

### Environment Variable Configuration

The application uses Pydantic's `BaseSettings` for configuration management in `app/config.py`, automatically mapping environment variables to configuration fields. Configuration is loaded using `python-dotenv` which supports both `.env` files and environment variables.

In CDP, environment variables and secrets need to be set using CDP conventions. See links below:
- [CDP App Config](https://github.com/DEFRA/cdp-documentation/blob/main/how-to/config.md)
- [CDP Secrets](https://github.com/DEFRA/cdp-documentation/blob/main/how-to/secrets.md)

For local development - see [instructions below](#local-development).

#### Bedrock Configuration

The application uses AWS Bedrock for LLM access. The following environment variables are required:

| Variable | Description | Example |
| --- | --- | --- |
| `AWS_REGION` | AWS region for Bedrock service | `eu-west-2` |
| `AWS_ACCESS_KEY_ID` | AWS access key ID | Set via CDP secrets |
| `AWS_SECRET_ACCESS_KEY` | AWS secret access key | Set via CDP secrets |
| `CLAUDE_SONNET_MODEL_CONFIG` | Bedrock model configuration | `anthropic.claude-sonnet-4-6,arn:aws:bedrock:eu-west-2:123456789012:application-inference-profile/fake-profile-test` |

The `CLAUDE_SONNET_MODEL_CONFIG` format is: `model_id,inference_profile[,guardrail_id:guardrail_version]`

#### Ingestion Configuration

| Variable | Description | Default |
| --- | --- | --- |
| `INGESTION_MODE` | `http` mounts the [ingestion routes](#api-endpoints) for the CMS to push document versions to; `s3` leaves them out. The `index-document` script works in both. | `http` |
| `MAX_INLINE_CONTENT_BYTES` | The largest inline `content.markdown` an index request may carry; larger is refused with 413, and should be sent as `contentUrl` | `2097152` (2 MiB) |
| `SEARCH_STORE_S3_BUCKET` | Where sections, staged chunk sets and index builds are kept | `rpa-ai-guidance-hub-search-store` |

### Linting and Formatting

This project uses [Ruff](https://github.com/astral-sh/ruff) for linting and formatting Python code.

#### Running Ruff

To run Ruff from the command line:

```bash
# Run linting with auto-fix
uv run ruff check . --fix

# Run formatting
uv run ruff format .
```

#### Pre-commit Hooks

This project uses [pre-commit](https://pre-commit.com/) to run linting and formatting checks automatically before each commit.

The pre-commit configuration is defined in `.pre-commit-config.yaml`

To set up pre-commit hooks:

```bash
# Set up the git hooks
pre-commit install
```

To run the hooks manually on all files:

```bash
pre-commit run --all-files
```

#### VS Code Configuration

For the best development experience, configure VS Code to use Ruff:

1. Install the [Ruff extension](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff) for VS Code
2. Configure your VS Code settings (`.vscode/settings.json`):

```json
{
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
        "source.fixAll.ruff": "explicit",
        "source.organizeImports.ruff": "explicit"
    },
    "ruff.lint.run": "onSave",
    "[python]": {
        "editor.defaultFormatter": "charliermarsh.ruff",
        "editor.formatOnSave": true,
        "editor.codeActionsOnSave": {
            "source.fixAll.ruff": "explicit",
            "source.organizeImports.ruff": "explicit"
        }
    }
}
```

This configuration will:

- Format your code with Ruff when you save a file
- Fix linting issues automatically when possible
- Organize imports according to isort rules

#### Ruff Configuration

Ruff is configured in the `.ruff.toml` file

### Docker

This repository uses Docker throughput its lifecycle i.e. both for local development and the environments. A benefit of this is that environment variables & secrets are managed consistently throughout the lifecycle

See the `Dockerfile` and `compose.yml` for details

## Local development

### Setup & Configuration

Follow the convention below for environment variables and secrets in local development.

**Environment variables:** `.env` file in the project root.

**Compose directory:** The `compose/` directory contains helper scripts and environment configurations:
- `compose/floci/start.d/10-setup-resources.sh` - This script is used to set up the necessary AWS resources in the local Floci environment. It runs when the Floci container starts, ensuring that your local AWS environment is ready for development.

**Libraries:** Ensure the python virtual environment is configured and libraries are installed using `uv sync`, [as above](#python)

**Pre-Commit Hooks:** Ensure you install the pre-commit hooks, as above

### Development

This app can be run locally by either using the Docker Compose project or project scripts.

#### Using Docker Compose

To run the application using Docker Compose, you can use the following command:

```bash
docker compose up --build
```

If you want to enable hot-reloading, you can press the `w` key once the compose project is running to enable `watch` mode.

#### Running on the host

To run the application directly on the host with its dependencies in Docker:

```bash
# start the dependencies only
docker compose up -d floci mongodb redis

# start the application
uv run --env-file .env rpa-ai-guidance-hub-search
```

This loads configuration from `.env`, and enables hot-reload when `PYTHON_ENV=development`.

The service will then run on `http://localhost:8085`

#### Sending it a document

With the service running, send it a guide version the way the CMS will. The
reply is `202`; the outcome is in the service's log.

```bash
# inline content, optionally with the API's section ids
uv run task send-document --document-id <uuid7> --version-id <uuid7> \
    --title "Claims Guidance" --content-file guide.md [--section-ids sections.json]

# content the API has stored in S3
uv run task send-document --document-id <uuid7> --version-id <uuid7> \
    --title "Claims Guidance" --content-url s3://<bucket>/<doc>/<ver>/content.md

# remove the version
uv run task send-document --document-id <uuid7> --version-id <uuid7> --delete
```

See [`scripts/README.md`](scripts/README.md) for `index-document`, which indexes a
version from S3 without the HTTP routes.

### Testing

Ensure the python virtual environment is configured and libraries are installed using `uv sync`, [as above](#python)

Testing follows the [FastApi documented approach](https://fastapi.tiangolo.com/tutorial/testing/); using pytest & starlette.

To test the application run:

```bash
uv run pytest
```

## Where code goes

### Domains

```text
prepare:  source reader -> SourceDocument -> sectioning -> SectionRecord (stored)
                                                               |
                         chunking strategy -> ChunkSet (staged) -> ApplyJob
apply:    ApplyJob -> staged ChunkSet -> index build (published)

indexing (write)   search (read)  ->  search/v1 (GET /v1/search)
        \              /
     records, index, matching
```

- **`app/records/`** is the canonical, source-neutral model: a
  `DocumentHeader` per version (with the `url` a reader views it at, and
  `source`, the source's own details typed per source type in
  `source_metadata`: `GuideMetadata` today),
  `SectionRecord`s (whole sections, kept as blocks, with a section
  `content_hash`) and the `ChunkRecord`s a strategy derives from them. Sections
  are stored as `sections/{documentId}/{versionId}.jsonl`, and are what every
  rebuild starts from. A `ChunkSet` is one strategy's chunks of one version at
  one status, staged as `staged/{strategy}/{documentId}/{versionId}/{status}.jsonl`
  for the apply step.
- **Ids**: every document and version id is a UUID. Guides use the CMS's
  (always UUIDv7). A source without its own, such as GOV.UK content, mints
  deterministic UUIDv5s under its own namespace (`ids.minted_id`), so the
  same content always gets the same ids and sources never collide.
- **`app/indexing/`** indexes a version in two steps (`stages.py`), so that
  preparing can later scale out while a single writer applies:
  - **`prepare/`** turns an `IndexJob` into an `ApplyJob`. A `SourceReader` per
    source type reads one version into a `SourceDocument` (a guide's
    `content.md` today, with `guide_cms` modelling the CMS API's guide and
    its metadata; GOV.UK content items would be another reader with their
    own Content API models and metadata type), `sectioning`
    makes the canonical sections, and a named `ChunkingStrategy` (`packed-800`
    for the keyword index) chunks them. `DocumentPreparer` stores the sections
    and stages the chunk set. It never touches the index.
  - **`apply/`** writes an `ApplyJob`'s staged chunk set to the index.
    `DocumentApplier` refuses chunks from any strategy but
    `index_schema.CHUNK_STRATEGY`, and `publish` applies a job to the active
    build and publishes the result. It never reads a source or chunks.
- **`app/search/`** answers queries from the index. `app/search/v1/` is the HTTP
  contract; a breaking change is a new `v2/` beside it.
- **`app/index/`** is the index itself: its fields and `SCHEMA_VERSION`, the
  `ChunkIndex` protocols, the Tantivy engine, and its builds in S3 and the
  active build in MongoDB, both keyed by schema version.
- **`app/matching/`** decides what counts as a match, for the engine and for
  excerpts alike.

Indexing and search never import each other.

### Schemas and models

Each layer knows only the one directly below it:

```text
HTTP JSON  <-  schemas.py   Pydantic: from_model() and conversions to models
                   |
               models.py    dataclasses and enums: to_document() / from_document()
                   |
               Mongo record (a dict)
```

- **`schemas.py`** holds the shapes that cross the HTTP boundary: request bodies,
  response bodies, and payloads other services send us (such as cdp-uploader's
  callback). They are Pydantic, which validates them, writes them into the OpenAPI
  document and spells them in camelCase on the wire. A schema converts to and from
  models, and knows nothing of how anything is stored.
- **`models.py`** holds what the service works with: plain dataclasses and enums,
  with no HTTP in them. A model converts itself to and from the record Mongo stores
  (`to_document()` and `from_document()`), so the stored field names are written
  down once. A model never imports a schema.
- **Routers** only call the conversions; they don't build shapes themselves.
- The role decides the file, not the library: `AppConfig` is Pydantic and lives in
  `config.py`.

### Ids

`app/records/ids.py` gives each kind of id a type of its own (`typing.NewType`), so
mypy refuses one where another belongs:

- **`DocumentId` and `VersionId`** are uuids. A guide's are the CMS's, always
  version 7 (time-ordered), and refused otherwise: a path id that isn't one is
  answered with 422. A source without ids of its own mints them (`minted_id`).
- **`SectionId` and `ChunkId`** are `str`, hashed from what they identify unless
  the source supplies its own.

## API endpoints

| Endpoint                        | Description                    |
|:--------------------------------| :----------------------------- |
| `GET: /docs`                    | Automatic API Swagger docs (the same document is committed as `docs/openapi.json`; regenerate with `uv run task openapi`) |
| `GET: /health`                  | Health check endpoint          |
| `GET: /v1/search`               | Word search (`q`, optionally inside one document with `tag`, paged by `limit` and `offset`; `group=document` pages documents instead, each with its best `sections`) or title search (`title`); published documents only for now |
| `PUT: /documents/{documentId}/versions/{versionId}` | Index a version (202). The body carries exactly one of `contentUrl` (`s3://…/content.md`) or `content` (`markdown`, optional `sectionIds`), plus `status`, `occurredAt`, `versionNumber` and the CMS `metadata`. `status` must be `published` (the index holds one version per document, so anything else is refused with 422), and `metadata.guideTitle` is required: it is the indexed title, never the Markdown's `# ` line. A version older than the one indexed is skipped. `http` mode only. |
| `DELETE: /documents/{documentId}/versions/{versionId}` | Remove a version at every status (202): from the index if it is the version indexed, and its staged files. `http` mode only. |

Both ingestion routes reply once the request is valid and do the work afterwards,
one request at a time per replica. A failure is logged and counted
(`IngestionFailed`, with `ENABLE_METRICS`), not retried: resending the same request
is always safe.

## Custom Cloudwatch Metrics

Uses the [aws embedded metrics library](https://github.com/awslabs/aws-embedded-metrics-python). An example can be found in `metrics.py`

In order to make this library work in the environments, the environment variable `AWS_EMF_ENVIRONMENT=local` is set in the app config. This tells the library to use the local cloudwatch agent that has been configured in CDP, and uses the environment variables set up in CDP `AWS_EMF_AGENT_ENDPOINT`, `AWS_EMF_LOG_GROUP_NAME`, `AWS_EMF_LOG_STREAM_NAME`, `AWS_EMF_NAMESPACE`, `AWS_EMF_SERVICE_NAME`

## Pipelines

### Dependabot

We have added an example dependabot configuration file to the repository. You can enable it by renaming
the [.github/example.dependabot.yml](.github/example.dependabot.yml) to `.github/dependabot.yml`

### SonarCloud

Instructions for setting up SonarCloud can be found in [sonar-project.properties](./sonar-project.properties)

## Licence

THIS INFORMATION IS LICENSED UNDER THE CONDITIONS OF THE OPEN GOVERNMENT LICENCE found at:

<http://www.nationalarchives.gov.uk/doc/open-government-licence/version/3>

The following attribution statement MUST be cited in your products and applications when using this information.

> Contains public sector information licensed under the Open Government license v3

### About the licence

The Open Government Licence (OGL) was developed by the Controller of Her Majesty's Stationery Office (HMSO) to enable
information providers in the public sector to license the use and re-use of their information under a common open
licence.

It is designed to encourage use and re-use of information freely and flexibly, with only a few conditions.
