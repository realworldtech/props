"""Custom storage backend for proxying S3 media through Django."""

from storages.backends.s3boto3 import S3Boto3Storage


class ProxiedS3Storage(S3Boto3Storage):
    """S3 storage that returns local /media/ URLs instead of S3 endpoint URLs."""

    def url(self, name):
        return f"/media/{name}"
