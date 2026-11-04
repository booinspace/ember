"""Private phase 3 room protocol; no engine or model-provider types cross this boundary."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from ..runtime.config import RuntimeModel

AGENT_IDENTITY = "ember-agent"
USER_IDENTITY = "ember-user"
TURN_METHOD = "ember.turn"
REMEMBER_METHOD = "ember.remember"
READY_METHOD = "ember.ready"
REPLY_STARTED_METHOD = "ember.reply_started"
SPEECH_FINISHED_METHOD = "ember.speech_finished"
STOP_SPEAKING_METHOD = "ember.stop_speaking"
TASK_NOTICE_TEXT = "A task result is ready in Tasks."


class ReadyInput(RuntimeModel):
    """Opt into acknowledged reply identities without changing older text participants."""

    speech_control: Literal[True]


class SpeechTarget(RuntimeModel):
    """An interruption capability scoped to one actor, session and reply."""

    instance_id: UUID
    character_id: UUID
    context_id: UUID
    request_id: UUID


class StopSpeakingResult(RuntimeModel):
    """A content-free receipt after the targeted speech and generation have drained."""

    request_id: UUID
    outcome: Literal["stopped", "not_current"]


class RoomBinding(RuntimeModel):
    """Private discovery metadata without credentials or bearer tokens."""

    version: Literal["0.1.0"] = "0.1.0"
    instance_id: UUID
    character_id: UUID
    room: Annotated[str, Field(pattern=r"^ember-[a-f0-9]{32}$")]
    endpoint: str


class TurnInput(RuntimeModel):
    """One explicit local participant turn, never automatically retried."""

    request_id: UUID
    text: Annotated[str, Field(min_length=1, max_length=1600)] = Field(repr=False)


class TurnResult(RuntimeModel):
    """Content-free acknowledgement after SDK text playout has finished."""

    request_id: UUID
    outcome: Literal["complete", "error"]
    retained_memories: int = 0
    code: str | None = None


class RememberInput(RuntimeModel):
    """Explicit human authoring command; dialogue text cannot invoke it."""

    key: Annotated[str, Field(min_length=1, max_length=200)]
    text: Annotated[str, Field(min_length=1, max_length=1600)] = Field(repr=False)
