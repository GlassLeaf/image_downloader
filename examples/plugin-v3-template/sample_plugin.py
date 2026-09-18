"""Template only: sign the matching manifest before installing it."""

from image_downloader import ImageArtifact, RequestSpec


class SamplePlugin:
    def validate_config(self, config, app_settings) -> None:
        return None

    def matches(self, url: str) -> bool:
        return False

    async def inspect(self, url, context):
        raise NotImplementedError

    async def create_image_request(self, image, context):
        return RequestSpec(image.url)

    async def recover_image_request(self, image, failed, response, context):
        return None

    def auth_flow(self, context):
        return None

    async def transform_image(self, artifact: ImageArtifact, context):
        return artifact
