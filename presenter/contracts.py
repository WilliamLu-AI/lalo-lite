"""Shared model and media ports. No vendor SDK or transport dependency."""
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Callable, Awaitable
from typing import Protocol


class ProviderError(Exception):
    """An actionable, bounded provider error safe to show to the reader."""

    def __init__(self, message, *, stage=None):
        super().__init__(message)
        self.stage = stage


class GenerationBackend(Protocol):
    def answer_stream(self, messages: list[dict]) -> AsyncIterator[str]: ...


class BatchGenerationBackend(Protocol):
    async def answer(self, messages: list[dict]) -> dict: ...


class SpeechRecognition(Protocol):
    async def transcribe(self, pcm: bytes) -> str: ...


class SpeechSynthesis(Protocol):
    def speech(self, text: str) -> AsyncIterator[bytes]: ...


class AudioOutput(Protocol):
    """PCM16 mono 32 kHz, monotonic sentence IDs; completion needs client ACK."""

    async def audio(self, index: int, pcm: bytes) -> None: ...
    async def sentence_end(self, index: int) -> None: ...


@dataclass(frozen=True)
class Started:
    """First model output has arrived."""


@dataclass(frozen=True)
class Navigation:
    block_id: str | None


@dataclass(frozen=True)
class Sentence:
    index: int
    text: str


@dataclass(frozen=True)
class Completed:
    answer: dict


BusinessEvent = Started | Navigation | Sentence | Completed
Synthesize = Callable[[str], AsyncIterator[bytes]]
BusinessObserver = Callable[[BusinessEvent], Awaitable[None]]


class VoiceRuntime(Protocol):
    name: str

    async def run(self, events: AsyncIterator[BusinessEvent], speech: Synthesize,
                  output: AudioOutput, business: BusinessObserver) -> tuple[dict, int]: ...
    async def cancel(self) -> None: ...
    async def close(self) -> None: ...
    def sentence_played(self, index: int) -> None: ...
    def playback_done(self) -> None: ...
