# Scripts

- `export_openapi.py` — write the app's OpenAPI document to `docs/openapi.json`
  (`uv run task openapi`), or with `--check` fail if the committed file is stale
  (`uv run task openapi-check`, run by the pull-request workflow).
- `index_document.py` — local development only, not how CDP indexes documents.
  Indexes one document version into the shared index: download the active
  build, split the guide's `content.md` into sections and reindex it, write its
  section file to the search-store bucket, then upload a new build and make it
  active
  (`uv run task index-document --source-type guide --document-id … --version-id … --status published
  --content-url s3://…/content.md`; reads `.env`, so copy `.env.example` first).
