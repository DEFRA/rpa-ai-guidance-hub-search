# Scripts

- `export_openapi.py` — write the app's OpenAPI document to `docs/openapi.json`
  (`uv run task openapi`), or with `--check` fail if the committed file is stale
  (`uv run task openapi-check`, run by the pull-request workflow). It builds the
  app in `http` ingestion mode, so the document always has the ingestion routes.
- `index_document.py` — local development only, not how CDP indexes documents.
  Indexes one document version from S3 without the HTTP routes. It prepares the
  version (stores its sections and stages its chunks in the search-store
  bucket), then applies it: downloads the active index build, writes the staged
  chunks into it, uploads the result as a new build and makes it active. Works
  in either ingestion mode; needs S3 and MongoDB.
  (`uv run task index-document --source-type guide --document-id … --version-id … --status published
  --content-url s3://…/content.md`; reads `.env`, so copy `.env.example` first.)
- `send_document.py` — local development only. Sends one version to a running
  service's ingestion routes, as the CMS will: `--content-url s3://…` or
  `--content-file guide.md [--section-ids sections.json]` to index, `--delete` to
  remove. `--url` defaults to `http://localhost:8085`; the service must be in
  `http` mode (the default).
  Versions are sent as `published`, with `--title` as the CMS's `guideTitle`.
  (`uv run task send-document --document-id … --version-id … --title "…"
  --content-file guide.md`.)
