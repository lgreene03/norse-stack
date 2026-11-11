"""SigV4 known-answer tests and credential loading.

The expected signatures are the worked examples in the AWS documentation,
"Signature Calculations for the Authorization Header: Transferring Payload in a
Single Chunk (AWS Signature Version 4)", Amazon S3 API Reference. A signer that
agrees with them byte for byte is computing SigV4, not an approximation of it.
"""

import pytest
import s3client
from s3client import (
    EMPTY_SHA256,
    ConfigError,
    Credentials,
    canonical_query,
    canonical_uri,
    load_credentials,
    sign,
)

DOC_CREDENTIALS = Credentials("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
DOC_DATE = "20130524T000000Z"
DOC_HOST = "examplebucket.s3.amazonaws.com"


def _signature(authorization: str) -> str:
    return authorization.rsplit("Signature=", 1)[1]


def test_get_object_example_from_the_aws_documentation():
    auth = sign(
        method="GET",
        uri="/test.txt",
        query="",
        headers={
            "Host": DOC_HOST,
            "Range": "bytes=0-9",
            "x-amz-content-sha256": EMPTY_SHA256,
            "x-amz-date": DOC_DATE,
        },
        payload_sha256=EMPTY_SHA256,
        credentials=DOC_CREDENTIALS,
        region="us-east-1",
        amz_date=DOC_DATE,
    )
    assert _signature(auth) == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    assert "Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request" in auth
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in auth


def test_list_objects_example_from_the_aws_documentation():
    auth = sign(
        method="GET",
        uri="/",
        query=canonical_query([("prefix", "J"), ("max-keys", "2")]),
        headers={
            "Host": DOC_HOST,
            "x-amz-content-sha256": EMPTY_SHA256,
            "x-amz-date": DOC_DATE,
        },
        payload_sha256=EMPTY_SHA256,
        credentials=DOC_CREDENTIALS,
        region="us-east-1",
        amz_date=DOC_DATE,
    )
    assert _signature(auth) == "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"


def test_object_keys_are_encoded_per_segment():
    key = "/muninn-raw/events.trade/source=okx.spot.v1/instrument=DOGE-USDT/part p0.jsonl.gz"
    assert canonical_uri(key) == (
        "/muninn-raw/events.trade/source%3Dokx.spot.v1/instrument%3DDOGE-USDT/part%20p0.jsonl.gz"
    )


def test_query_values_encode_slashes_and_equals():
    assert canonical_query([("prefix", "a/b=c"), ("list-type", "2")]) == (
        "list-type=2&prefix=a%2Fb%3Dc"
    )


def test_credentials_prefer_files(tmp_path):
    (tmp_path / "access").write_text("file-access\n")
    (tmp_path / "secret").write_text("file-secret\n")
    creds = load_credentials(
        {
            "S3_ACCESS_KEY_FILE": str(tmp_path / "access"),
            "S3_SECRET_KEY_FILE": str(tmp_path / "secret"),
            "S3_ACCESS_KEY": "env-access",
            "S3_SECRET_KEY": "env-secret",
        }
    )
    assert creds == Credentials("file-access", "file-secret")


def test_credentials_fall_back_to_the_environment():
    creds = load_credentials({"S3_ACCESS_KEY": "a", "S3_SECRET_KEY": "b"})
    assert creds == Credentials("a", "b")


def test_missing_credentials_name_the_setting_not_the_value(tmp_path):
    with pytest.raises(ConfigError, match="S3_SECRET_KEY"):
        load_credentials({"S3_ACCESS_KEY": "a"})
    with pytest.raises(ConfigError, match="S3_ACCESS_KEY_FILE could not be read"):
        load_credentials({"S3_ACCESS_KEY_FILE": str(tmp_path / "absent"), "S3_SECRET_KEY": "b"})


def test_credentials_never_print_the_secret():
    text = repr(Credentials("visible-access", "very-secret"))
    assert "very-secret" not in text
    assert "visible-access" not in text


def test_endpoint_must_be_a_url():
    with pytest.raises(ConfigError):
        s3client.S3Client("minio:9000", "bucket", Credentials("a", "b"))
