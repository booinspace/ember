"""Session speech preferences belong to the audio adapter, outside memory and inference."""

from pydantic import Field

from .config import RuntimeModel


class SpeechSelection(RuntimeModel):
    """Select an installed voice and an approximate speaking pace for subsequent replies."""

    voice: str = Field(min_length=1, max_length=128)
    speed: float = Field(default=1.0, ge=0.75, le=1.5, allow_inf_nan=False)


class SpeechOptions(RuntimeModel):
    """An explicitly capable provider's offline choices and current session selection."""

    model: str = Field(min_length=1, max_length=128)
    voices: tuple[str, ...] = Field(min_length=1, max_length=256)
    selection: SpeechSelection
