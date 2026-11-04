# Scripts

- `export_openapi.py` — write the app's OpenAPI document to `docs/openapi.json`
  (`uv run task openapi`), or with `--check` fail if the committed file is stale
  (`uv run task openapi-check`, run by the pull-request workflow).
