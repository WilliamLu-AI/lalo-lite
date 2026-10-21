"""Compatibility import; voice model adapters live in model_adapters."""
from .model_adapters.voice import VoiceProviders, LiveRecognition

__all__ = ["VoiceProviders", "LiveRecognition"]
