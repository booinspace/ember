"""Bounded inline raster parts for the coding gateway, never URLs or files."""

import base64
import binascii
import re
from io import BytesIO
from typing import Annotated, Literal, Self

from PIL import Image
from pydantic import Field, model_validator

from ..runtime.config import RuntimeModel

PROMPT_MESSAGE_CHARS = 100_000
MAX_CODING_REQUEST_BYTES = 1024 * 1024
MAX_CODING_IMAGES = 4
MAX_IMAGE_BYTES = 512 * 1024
MAX_IMAGE_PIXELS = 4_000_000
_IMAGE_URL = re.compile(r"data:image/(png|jpeg);base64,[A-Za-z0-9+/]+={0,2}")
_FORMATS = {"png": "PNG", "jpeg": "JPEG"}


class PromptTextPart(RuntimeModel):
    """Ordered text in a multimodal user message."""

    type: Literal["text"]
    text: Annotated[str, Field(max_length=MAX_CODING_REQUEST_BYTES, repr=False)]


class _InlineImage(RuntimeModel):
    url: Annotated[str, Field(max_length=4 * ((MAX_IMAGE_BYTES + 2) // 3) + 32, repr=False)]
    detail: Literal["auto", "low", "high"] = Field(default="auto", exclude=True)

    @model_validator(mode="after")
    def _inline_only(self) -> Self:
        if not _IMAGE_URL.fullmatch(self.url):
            raise ValueError("Only inline PNG and JPEG images are supported.")
        return self

    def verify_raster(self) -> None:
        """Check format and size before decoding a bounded raster."""
        header, payload = self.url.split(",", 1)
        expected = _FORMATS[header.removeprefix("data:image/").removesuffix(";base64")]
        try:
            raw = base64.b64decode(payload, validate=True)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("Image exceeds byte limit.")
            with Image.open(BytesIO(raw), formats=[expected]) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS or getattr(
                    image, "is_animated", False
                ):
                    raise ValueError("Image exceeds pixel limit or is animated.")
                image.load()
        except (binascii.Error, OSError, SyntaxError, Image.DecompressionBombError) as error:
            raise ValueError("Invalid inline raster.") from error


class PromptImagePart(RuntimeModel):
    """An inline screenshot restricted to the coding vision path."""

    type: Literal["image_url"]
    image_url: _InlineImage = Field(repr=False)


type PromptContentPart = Annotated[PromptTextPart | PromptImagePart, Field(discriminator="type")]


def validate_images(images: tuple[PromptImagePart, ...]) -> None:
    """Validate one gateway job's images before sending any upstream."""
    for part in images:
        part.image_url.verify_raster()
