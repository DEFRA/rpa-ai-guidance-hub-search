"""Preparing a job stores the version's sections and stages its chunks."""

import dataclasses
import uuid

import pytest

from app import config as app_config
from app.index import index_schema
from app.indexing.prepare import (
    document_preparer,
    guide_markdown,
    guide_source,
    packed_chunking,
    sources,
)
from app.records import chunk_sets, ids, jsonl, models
from tests.fakes import chunk_set_store as fake_chunk_set_store
from tests.fakes import guide_content as fake_guide_content
from tests.fakes import section_store as fake_section_store
from tests.support import chunks as fixtures
from tests.support import guides

OTHER_DOCUMENT_ID = ids.DocumentId(uuid.UUID("0199b8a2-0000-7000-8000-00000000f0aa"))
MARKDOWN = "# Claims Guidance\n\n## 1 Overview\n\nHold a holding number.\n"


@pytest.fixture
def source():
    return fake_guide_content.InMemoryContent(
        {
            guides.job().content_url: MARKDOWN,
            guides.job(document_id=OTHER_DOCUMENT_ID).content_url: "No title here.",
        },
    )


@pytest.fixture
def sections():
    return fake_section_store.InMemorySectionStore()


@pytest.fixture
def staged():
    return fake_chunk_set_store.InMemoryChunkSetStore()


def _preparer(source, sections, staged, readers=None):
    return document_preparer.DocumentPreparer(
        readers=(
            {
                models.SourceType.GUIDE: guide_source.GuideSource(
                    source,
                    source,
                    guide_markdown.GuideMarkdownParser(),
                ),
            }
            if readers is None
            else readers
        ),
        sections=sections,
        strategy=packed_chunking.PackedChunking(),
        chunk_sets=staged,
    )


@pytest.fixture
def preparer(source, sections, staged):
    return _preparer(source, sections, staged)


class TestPrepare:
    async def test_stores_the_sections(self, preparer, sections):
        await preparer.prepare(guides.job())

        header, stored = sections.files[guides.DOCUMENT_ID, guides.VERSION_ID]

        assert (header.title, header.url) == (
            "Claims Guidance",
            f"/guides/{guides.DOCUMENT_ID}",
        )
        assert [section.anchor for section in stored] == ["1-overview"]

    async def test_stages_the_chunks_and_says_where(self, preparer, staged):
        job = guides.job()

        apply_job = await preparer.prepare(job)

        chunk_set = staged.sets[apply_job.chunk_set_key]

        assert [chunk.plain_text for chunk in chunk_set.chunks] == [
            "Hold a holding number.",
        ]
        assert apply_job.chunk_set_key == chunk_sets.key(
            "packed-800",
            job.document_id,
            job.version_id,
            job.status,
        )
        assert (
            apply_job.document_id,
            apply_job.version_id,
            apply_job.status,
            apply_job.occurred_at,
            apply_job.strategy,
        ) == (
            job.document_id,
            job.version_id,
            job.status,
            job.occurred_at,
            "packed-800",
        )

    async def test_preparing_again_stages_the_same_chunks(self, preparer, staged):
        first = await preparer.prepare(guides.job())
        chunk_set = staged.sets[first.chunk_set_key]

        again = await preparer.prepare(guides.job())

        assert (again, staged.sets) == (first, {first.chunk_set_key: chunk_set})

    async def test_uses_the_apis_section_ids(self, preparer, source, sections):
        section = ids.SectionId("0199b8a2-0000-7000-8000-0000000000e1")
        source.section_ids[guides.job().content_url] = {"1-overview": section}

        await preparer.prepare(guides.job())

        _, stored = sections.files[guides.DOCUMENT_ID, guides.VERSION_ID]

        assert stored[0].section_id == section

    async def test_stores_nothing_for_a_malformed_guide(
        self,
        preparer,
        sections,
        staged,
    ):
        with pytest.raises(guide_markdown.MalformedGuideError):
            await preparer.prepare(guides.job(document_id=OTHER_DOCUMENT_ID))

        assert (sections.files, staged.sets) == ({}, {})

    async def test_refuses_a_source_with_no_reader(self, source, sections, staged):
        preparer = _preparer(source, sections, staged, readers={})

        with pytest.raises(document_preparer.UnsupportedSourceError):
            await preparer.prepare(guides.job())

        assert (sections.files, staged.sets) == ({}, {})


class MintingReader:
    """A source with no version ids: it mints one from what it reads."""

    NAMESPACE = uuid.UUID("6f2c1a8e-3b4d-4c5e-9f60-718293a4b5c6")

    async def read(self, job):
        version_id = ids.VersionId(
            ids.minted_id(self.NAMESPACE, str(job.document_id), "content-hash"),
        )

        return sources.SourceDocument(
            header=fixtures.header(document_id=job.document_id, version_id=version_id),
            sections=[
                sources.SourceSection(
                    heading="Overview",
                    section_number="1",
                    level=1,
                    anchor="1-overview",
                    blocks=(fixtures.paragraph("Minted."),),
                ),
            ],
        )


class TestASourceThatMintsVersionIds:
    async def test_hands_the_minted_id_to_apply(self, sections, staged):
        preparer = _preparer(
            None,
            sections,
            staged,
            readers={models.SourceType.GUIDE: MintingReader()},
        )
        job = dataclasses.replace(guides.job(), version_id=None)

        apply_job = await preparer.prepare(job)

        minted = ids.minted_id(
            MintingReader.NAMESPACE,
            str(job.document_id),
            "content-hash",
        )

        assert apply_job.version_id == minted
        assert staged.sets[apply_job.chunk_set_key].header.version_id == minted
        assert (job.document_id, minted) in sections.files


class TestTheStrategy:
    def test_is_the_one_the_index_holds(self):
        assert packed_chunking.PackedChunking().name == index_schema.CHUNK_STRATEGY


class TestFromConfig:
    async def test_prepares_through_the_configured_bucket(self, mocker, moto_s3):
        mocker.patch("app.common.s3.get_s3_client", return_value=moto_s3)
        bucket = app_config.get_config().search_store_s3_bucket
        for name in (guides.BUCKET, bucket):
            moto_s3.create_bucket(
                Bucket=name,
                CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
            )
        moto_s3.put_object(
            Bucket=guides.BUCKET, Key=guides.content_key(), Body=MARKDOWN
        )

        apply_job = await document_preparer.from_config().prepare(guides.job())

        stored = {
            item["Key"] for item in moto_s3.list_objects_v2(Bucket=bucket)["Contents"]
        }
        assert stored == {
            apply_job.chunk_set_key,
            jsonl.key(guides.DOCUMENT_ID, guides.VERSION_ID),
        }
        assert apply_job.strategy == index_schema.CHUNK_STRATEGY
