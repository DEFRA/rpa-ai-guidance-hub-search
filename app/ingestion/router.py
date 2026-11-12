"""HTTP routes the CMS uses to index and delete document versions.

Mounted only when `INGESTION_MODE` is `http`.
"""

import fastapi

router = fastapi.APIRouter(tags=["ingestion"])
