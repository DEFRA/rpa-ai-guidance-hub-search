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
  - [API endpoints](#api-endpoints)
  - [The indexing queue's events](#the-indexing-queues-events)
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

#### Ingestion Configuration

| Variable | Description | Default |
| --- | --- | --- |
| `INGESTION_MODE` | `http` mounts the [ingestion routes](#api-endpoints) for the CMS to push document versions to; `s3` leaves them out. The queue consumers and the `index-document` script work in both. | `http` |
| `INDEX_REFRESH_INTERVAL_SECONDS` | How often each replica checks MongoDB for a newer active index build and swaps it in. | `60` |
| `SEARCH_INDEXING_SQS_QUEUE_URL` | The indexing queue: guide events to prepare (CloudEvents; see [Indexing](#indexing)). Standard. | required |
| `SEARCH_INDEX_SYNC_SQS_QUEUE_URL` | The index sync queue: jobs for the index's single writer. Must be FIFO. Not used yet: the index is written in process (see [Indexing](#indexing)). | required |
| `SQS_SHUTDOWN_TIMEOUT_SECONDS` | How long the queue consumers wait for messages in flight on shutdown; uvicorn waits 5 seconds longer. | `20` |
| `MANAGED_DOCS_S3_BUCKET` | The only bucket a version's `contentUrl` may name; the API writes each version's `content.md` there. | `rpa-ai-guidance-hub-managed-docs` |
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
reply is `202` once the request is queued; the outcome is in the service's log.
The content must already be in the managed-docs bucket.

```bash
# content the API has stored in S3
aws --endpoint-url http://localhost:4566 s3 cp guide.md \
    s3://rpa-ai-guidance-hub-managed-docs/<doc>/<ver>/content.md
uv run task send-document --document-id <uuid7> --version-id <uuid7> \
    --title "Claims Guidance" \
    --content-url s3://rpa-ai-guidance-hub-managed-docs/<doc>/<ver>/content.md

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
- **`app/ingestion/`** is how guide versions arrive: the HTTP routes, the
  indexing queue's guide events (`models.py`) they send, and the preparation
  that handles each (see [Indexing](#indexing)).
- **`app/indexing/`** indexes a version in two steps (`stages.py`), so that
  preparing can scale out behind a single writer. `models.py` holds the index
  sync queue's events, which nothing sends yet:
  - **`prepare/`** turns an `IndexJob` into an `ApplyJob`. A `SourceReader` per
    source type reads one version into a `SourceDocument` (a guide's
    `content.md` today, with `guide_cms` modelling the CMS API's guide and
    its metadata; GOV.UK content items would be another reader with their
    own Content API models and metadata type), `sectioning`
    makes the canonical sections, and a named `ChunkingStrategy` (`packed-800-v2`
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
  active build in MongoDB, both keyed by schema version. Each replica checks
  the active build every `INDEX_REFRESH_INTERVAL_SECONDS` (default 60) and
  swaps in a newer one.
- **`app/matching/`** decides what counts as a match, for the engine and for
  excerpts alike.
- **`app/entrypoints/`** runs the app: FastAPI (`fastapi.py`) and the queue
  consumers (`queue_consumers.py`), which hand each message's event to its
  sub-domain. A queue is an entry point, not a domain.

Indexing and search never import each other, and indexing never imports
ingestion.

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
| `PUT: /documents/{documentId}/versions/{versionId}` | Queue a version to be indexed (202). The body carries `contentUrl` (`s3://<managed-docs bucket>/…/content.md`), `status`, `occurredAt`, `versionNumber` and the CMS `metadata`. Inline `content` is refused (422): a whole document can be larger than a queue message. `status` must be `published` (the index holds one version per document, so anything else is refused with 422), and `metadata.guideTitle` is required: it is the indexed title, never the Markdown's `# ` line. A version older than the one indexed is skipped. 503 if the request could not be queued. `http` mode only. |
| `DELETE: /documents/{documentId}/versions/{versionId}` | Queue a version's removal at every status (202): from the index if it is the version indexed, and its staged files. 503 if it could not be queued. `http` mode only. |

## Indexing

Guide versions arrive on the indexing queue, and each replica prepares them and
writes the index itself. The queue belongs to the sub-domain it reaches, whose
`models.py` defines its events; its consumer is an entry point
(`app/entrypoints/queue_consumers.py`), like the HTTP routes.

1. **Ingestion routes → indexing queue** (`app/ingestion/`). Each route puts a
   guide event on the indexing queue and replies 202. `rpa-ai-guidance-hub-api`
   will publish the same events to its guide-events topic, which is subscribed
   to the same queue.
2. **Preparation** (`app/ingestion/preparation.py`, every replica, up to 4
   messages at once). It reads the version's `content.md` from the managed-docs
   bucket, stores its sections, and stages its chunks as one object in the
   search-store bucket (`staged/{strategy}/{documentId}/{versionId}/{status}.jsonl`,
   written in one PUT, so it is whole or absent).
3. **Writing the index, in process** (`app/indexing/dispatch.py`). The prepared
   job goes to the replica's `BatchingDispatcher`, which publishes every job
   waiting at once, and the message is acknowledged only once its job is
   written. A deletion removes the version from the index, then deletes its
   sections and staged chunk set.

**Limitation:** as with the ingestion routes before, every replica writes the
index. With more than one replica, two publishes at once can lose one's build
swap. The index sync queue (FIFO, one message group) will make one writer of
all replicas: its events are defined below, but nothing sends or consumes
them yet.

A message whose handler fails is retried after its visibility timeout, and moved
to the queue's `-deadletter` queue after 3 receives: content that is missing or
may not be read, a malformed event, or a `contentUrl` outside the managed-docs
bucket. Every step is idempotent, so redriving a dead letter is always safe.
Failures are logged and counted (`IngestionFailed`, with `ENABLE_METRICS`).

### Events

Every message on either queue is a [CloudEvents 1.0](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md)
event in structured JSON mode: the whole event is the message body. All of them
subclass the one CloudEvents base in `app/common/cloudevents.py`. The contract is
the AsyncAPI 3.0 document [`docs/asyncapi.yaml`](docs/asyncapi.yaml): both queues,
their SQS bindings, every message with an example, and their payload schemas,
generated from the models (`uv run task asyncapi`). The pull-request workflow
checks it is current and valid.

| Queue | `type` | `data` |
| --- | --- | --- |
| indexing | `uk.gov.defra.rpa.guidancehub.guide.version.published` | `documentId`, `versionId`, `sourceType`, `status`, `contentUrl`, `versionNumber`, `metadata` (the PUT body's keys) |
| indexing | `uk.gov.defra.rpa.guidancehub.guide.version.deleted` | `documentId`, `versionId` |
| index sync | `uk.gov.defra.rpa.guidancehub.search.guide.version.prepared` | `documentId`, `versionId`, `status`, `strategy`, `chunkSet` (`bucket`, `key`) |
| index sync | `uk.gov.defra.rpa.guidancehub.search.guide.version.removal-requested` | `documentId`, `versionId` |

- Guide events are the hub's, not search's: the API will produce them, and
  search's routes send them only until it does. So `source` names whichever
  service sent one and is never checked (today always
  `/rpa-ai-guidance-hub-search/ingestion`), and their `type`s and `dataschema`s
  name the hub. The index sync queue's events are search's own.
- `dataschema` is a URN naming the `data` schema's owner, the schema and its
  major version (`urn:defra:rpa-ai-guidance-hub:events:guide-version-published:v1`);
  the AsyncAPI document gives each schema its URN as `$id`.
- `time` is when the version reached its status (the PUT body's `occurredAt`).
- `subject` is `documents/{documentId}/versions/{versionId}`.
- The `cdprequestid` extension carries the CDP trace id, so the writer's logs
  carry the id of the request that started the work.

Within v1, changes are additive and readers ignore what they don't know. A
breaking change to `data` is a new schema and URN (`…:v2`), with the same `type`.
On the indexing queue, an event of another type is acknowledged and ignored,
since the topic may carry guide events search doesn't need; on the index sync
queue, which only search writes, it is dead-lettered. An event of a known type
that doesn't validate, or names a schema version the service doesn't read, is
dead-lettered.

If the topic's subscription doesn't use raw message delivery, guide events
arrive inside an SNS envelope; setting the consumer's `unwrap_sns=True` reads
both forms.

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
