"""Session speech preferences belong to the audio adapter, outside memory and inference."""

from pydantic import Field

from .config import RuntimeModel, SpeechSpeed


class SpeechSelection(RuntimeModel):
    """Select an installed voice and an approximate speaking pace for subsequent replies."""

    voice: str = Field(min_length=1, max_length=128)
    speed: SpeechSpeed = 1.0


class SpeechOptions(RuntimeModel):
    """An explicitly capable provider's offline choices and current session selection."""

    model: str = Field(min_length=1, max_length=128)
    voices: tuple[str, ...] = Field(min_length=1, max_length=256)
    selection: SpeechSelection
