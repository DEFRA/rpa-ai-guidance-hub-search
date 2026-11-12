# Scripts

- `export_openapi.py` — write the app's OpenAPI document to `docs/openapi.json`
  (`uv run task openapi`), or with `--check` fail if the committed file is stale
  (`uv run task openapi-check`, run by the pull-request workflow).
- `index_document.py` — local development only, not how CDP indexes documents.
  Indexes one document version: split its `content.md` into
  sections, write the section file to the search-index bucket and reindex a
  local Tantivy directory
  (`uv run task index-document --source-type guide --index-dir DIR
  --document-id … --version-id … --status published
  --content-url s3://…/content.md`; reads `.env`, so copy `.env.example` first).
