"""Turn structured generation into business events, never into audio."""
import json

from ..streaming import SpeechStream
from ..contracts import (ProviderError, Started, Navigation, Sentence, Completed,
                         GenerationBackend, BatchGenerationBackend)


async def prepared_answer(answer):
    """A located batch answer enters the voice path without regenerating."""
    text = answer.get("speech", "").strip()
    if not text:
        raise ProviderError("Model returned an empty answer")
    yield Sentence(0, text)
    yield Completed(answer)


async def explain(backend: GenerationBackend | BatchGenerationBackend, messages: list[dict]):
    """Stream business events from either a streaming or a batch model adapter."""
    parser = SpeechStream()
    index = 0
    started = navigated = False

    async def deltas():
        if hasattr(backend, "answer_stream"):
            source = backend.answer_stream(messages)
            try:
                async for delta in source:
                    yield delta
            finally:
                await source.aclose()
        else:
            yield json.dumps(await backend.answer(messages), ensure_ascii=False)

    source = deltas()
    try:
        async for delta in source:
            if not started:
                started = True
                yield Started()
            parts = parser.feed(delta)
            if not navigated and '"speech"' in parser.raw:
                prefix = parser.raw.split('"speech"', 1)[0].rstrip().rstrip(",")
                try:
                    target = json.loads(prefix + "}").get("blockId")
                    navigated = True
                    yield Navigation(target if isinstance(target, str) else None)
                except (ValueError, AttributeError):
                    pass
            for text in parts:
                yield Sentence(index, text)
                index += 1
        try:
            answer = parser.result()
        except (ValueError, TypeError) as error:
            raise ProviderError("Invalid structured model answer") from error
        for text in parser.feed("", final=True):
            yield Sentence(index, text)
            index += 1
        if not index:
            raise ProviderError("Model returned an empty answer")
        yield Completed(answer)
    except Exception as error:
        if isinstance(error, ProviderError):
            error.stage = 'model'
            raise
        raise ProviderError(type(error).__name__, stage='model') from error
    finally:
        await source.aclose()
