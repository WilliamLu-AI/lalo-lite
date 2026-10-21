"""NumPy-only log-mel frontend for the fixed Smart Turn v3.2 input contract.

Uses the Whisper frontend parameters: 16 kHz, 400-point periodic Hann,
160-sample hop, 80 Slaney-normalized mel bands and 8 seconds of audio.
"""
import numpy as np


class SpeechFeatures:
    def __init__(self):
        frequencies = np.linspace(0, 8000, 201)
        maximum_mel = 15 + np.log(8000 / 1000) / (np.log(6.4) / 27)
        mel = np.linspace(0, maximum_mel, 82)
        edges = mel * (200 / 3)
        high = mel >= 15
        edges[high] = 1000 * np.exp((mel[high] - 15) * (np.log(6.4) / 27))
        lower = (frequencies[:, None] - edges[:-2]) / (edges[1:-1] - edges[:-2])
        upper = (edges[2:] - frequencies[:, None]) / (edges[2:] - edges[1:-1])
        self.filters = np.maximum(0, np.minimum(lower, upper)) * (2 / (edges[2:] - edges[:-2]))
        self.window = np.hanning(401)[:-1]

    def __call__(self, pcm):
        samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        samples = samples[-128000:]
        samples = np.pad(samples, (max(0, 128000 - len(samples)), 0))
        samples = (samples - samples.mean()) / np.sqrt(samples.var() + 1e-7)
        padded = np.pad(samples.astype(np.float64), (200, 200), mode='reflect')
        frames = np.lib.stride_tricks.sliding_window_view(padded, 400)[::160]
        spectrum = np.fft.rfft(frames * self.window, axis=1).astype(np.complex64)
        power = np.abs(spectrum, dtype=np.float64) ** 2
        mel = np.maximum(self.filters.T @ power.T, 1e-10)
        log = np.log10(mel)[:, :-1]
        log = (np.maximum(log, log.max() - 8) + 4) / 4
        return log[None].astype(np.float32)
