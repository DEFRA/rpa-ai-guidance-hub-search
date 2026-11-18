import logging

from app.common import redaction, tracing


# Adds additional ECS fields to the logger.
class ExtraFieldsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = tracing.ctx_trace_id.get("")
        req = tracing.ctx_request.get(None)
        resp = tracing.ctx_response.get(None)

        if trace_id:
            record.trace = {"id": trace_id}  # type: ignore[attr-defined]

        http: dict[str, object] = {}
        if req:
            record.url = {"full": req.get("url", None)}  # type: ignore[attr-defined]
            http["request"] = {"method": req.get("method", None)}
        if resp:
            http["response"] = resp
        if http:
            record.http = http  # type: ignore[attr-defined]
        return True


class EndpointFilter(logging.Filter):
    def __init__(self, path: str, name: str = "") -> None:
        super().__init__(name)
        self._path = path

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find(self._path) == -1


class AccessLogRedactionFilter(logging.Filter):
    """Redacts search text from uvicorn's access log lines.

    For the `uvicorn.access` logger, whose records carry `(client, method,
    path_with_query, http_version, status)` as their arguments.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args

        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (*args[:2], redaction.redact_query(args[2]), *args[3:])

        return True
