from .native import NativeRuntime
from ..contracts import VoiceRuntime


def create_runtime(name: str) -> VoiceRuntime:
    if name == "native":
        return NativeRuntime()
    if name == "livekit":
        try:
            from .livekit import LiveKitRuntime
        except ImportError as error:
            raise ValueError("LiveKit runtime requires requirements-voice.txt") from error
        return LiveKitRuntime()
    raise ValueError("VOICE_RUNTIME must be native or livekit")
