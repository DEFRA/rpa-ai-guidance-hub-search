import os

# AppConfig environment defaults (use the same names pydantic-settings will look up)
os.environ.setdefault("PYTHON_ENV", "test")
os.environ.setdefault("AWS_REGION", "eu-west-2")
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "8085")
os.environ.setdefault("LOG_CONFIG", "logging-dev.json")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DATABASE", "rpa-ai-guidance-hub-search")
os.environ.setdefault("MONGO_TRUSTSTORE", "TRUSTSTORE_CDP_ROOT_CA")
os.environ.setdefault("ENABLE_METRICS", "false")
os.environ.setdefault("SOURCE_DOCS_S3_BUCKET", "rpa-ai-guidance-hub-source-docs")
os.environ.setdefault("MANAGED_DOCS_S3_BUCKET", "rpa-ai-guidance-hub-managed-docs")
os.environ.setdefault("SEARCH_STORE_S3_BUCKET", "rpa-ai-guidance-hub-search-store")
os.environ.setdefault(
    "SEARCH_INDEXING_SQS_QUEUE_URL",
    "http://localhost:4566/000000000000/rpa_ai_guidance_hub_search_indexing",
)
os.environ.setdefault(
    "SEARCH_INDEX_SYNC_SQS_QUEUE_URL",
    "http://localhost:4566/000000000000/rpa_ai_guidance_hub_search_index_sync.fifo",
)

pytest_plugins = [
    "tests.support.mongo",
    "tests.support.s3",
    "tests.support.floci",
    "tests.support.sqs",
]
