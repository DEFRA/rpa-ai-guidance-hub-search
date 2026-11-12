"""The command line turns its arguments into one request to the service."""

import json

import httpx2
import pytest

from app.ingestion import schemas
from scripts import send_document
from tests.support import guides

PATH = (
    f"{send_document.DEFAULT_URL}/documents/{guides.DOCUMENT_ID}"
    f"/versions/{guides.VERSION_ID}"
)


@pytest.fixture
def sent(mocker):
    """Every request the script sends, answered with 202."""
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(202, json={"operation": "index"})

    transport = httpx2.MockTransport(handle)
    real_client = httpx2.Client
    mocker.patch.object(
        httpx2,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    return requests


def _ids():
    return [
        "--document-id",
        str(guides.DOCUMENT_ID),
        "--version-id",
        str(guides.VERSION_ID),
    ]


class TestSendDocument:
    def test_sends_a_content_url(self, sent):
        url = guides.job().content_url

        code = send_document.main(
            [*_ids(), "--title", "Claims Guidance", "--content-url", url]
        )

        (request,) = sent
        body = json.loads(request.content)
        assert code == 0
        assert (request.method, str(request.url)) == ("PUT", PATH)
        request_body = schemas.IndexRequest.model_validate(body)
        assert (
            request_body.status,
            request_body.content_url,
            request_body.metadata.guide_title,
        ) == ("published", url, "Claims Guidance")
        assert "content" not in body

    def test_sends_inline_content(self, sent, tmp_path):
        guide = tmp_path / "guide.md"
        guide.write_text("# Title\n")
        section_ids = tmp_path / "sections.json"
        section_ids.write_text('{"1-overview": "section-1"}')

        send_document.main(
            [
                *_ids(),
                "--title",
                "Claims Guidance",
                "--content-file",
                str(guide),
                "--section-ids",
                str(section_ids),
                "--version-number",
                "1.2",
            ],
        )

        (request,) = sent
        body = json.loads(request.content)
        assert body["content"] == {
            "markdown": "# Title\n",
            "sectionIds": {"1-overview": "section-1"},
        }
        assert body["versionNumber"] == "1.2"
        schemas.IndexRequest.model_validate(body)

    def test_deletes(self, sent):
        send_document.main([*_ids(), "--delete"])

        (request,) = sent
        assert (request.method, str(request.url)) == ("DELETE", PATH)

    def test_fails_when_the_service_refuses(self, mocker):
        transport = httpx2.MockTransport(lambda _: httpx2.Response(422))
        real_client = httpx2.Client
        mocker.patch.object(
            httpx2,
            "Client",
            lambda **kwargs: real_client(transport=transport, **kwargs),
        )

        assert send_document.main([*_ids(), "--delete"]) == 1

    @pytest.mark.parametrize(
        "args",
        [
            pytest.param(["--content-url", "s3://b/k"], id="no-title"),
            pytest.param(["--title", "T"], id="no-content"),
            pytest.param(
                ["--title", "T", "--content-url", "s3://b/k", "--delete"],
                id="content-and-delete",
            ),
            pytest.param(
                [
                    "--title",
                    "T",
                    "--content-url",
                    "s3://b/k",
                    "--section-ids",
                    "s.json",
                ],
                id="section-ids-without-a-file",
            ),
        ],
    )
    def test_refuses_bad_arguments(self, sent, args):
        with pytest.raises(SystemExit):
            send_document.main([*_ids(), *args])

        assert sent == []
