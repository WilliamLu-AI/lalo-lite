from collections import deque
import numpy as np
import webrtcvad


class Segmenter:
    """Accept 20ms PCM16 mono 16kHz frames. No audio is persisted."""
    def __init__(self):
        self.vad = webrtcvad.Vad(3)
        self.window = np.hanning(320)
        self.pre = deque(maxlen=15)
        self.frames = []
        self.active = False
        self.voiced = 0
        self.silent = 0
        self.revision = 0

    def feed(self, frame):
        if len(frame) != 640:
            raise ValueError('Expected 640 byte PCM frame')
        pcm = np.frombuffer(frame, dtype='<i2').astype(np.float32) / 32768
        voice = self.vad.is_speech(frame, 16000) and float(np.sqrt(np.mean(pcm * pcm))) > .006
        if voice:
            # Reject almost-pure sub-150Hz rumble (e.g. floor/desk impacts).
            # Keep harmonics so low-pitched speech is not rejected just for its pitch.
            energy = np.abs(np.fft.rfft(pcm * self.window)) ** 2
            voice = float(energy[3:].sum()) >= .03 * max(float(energy.sum()), 1e-12)
        self.revision += 1
        self.voiced = self.voiced + 1 if voice else 0
        self.silent = 0 if voice else self.silent + 1
        if not self.active:
            self.pre.append(frame)
            if self.voiced >= 4:
                self.active = True
                self.frames = list(self.pre)
                self.pre.clear()
                return 'start'
        else:
            self.frames.append(frame)
            if len(self.frames) >= 1500:
                return 'limit'
            if self.silent >= 35 and self.silent % 15 == 5:
                return 'endpoint'
        return None

    def take(self):
        audio = b''.join(self.frames)
        self.frames = []
        self.pre.clear()
        self.active = False
        self.voiced = self.silent = 0
        return audio


class TurnDetector:
    def __init__(self, model):
        import onnxruntime as ort
        from .features import SpeechFeatures
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), sess_options=options,
                                           providers=['CPUExecutionProvider'])
        self.extractor = SpeechFeatures()

    def probability(self, pcm):
        result = self.session.run(None, {'input_features': self.extractor(pcm)})
        return float(result[0].reshape(-1)[0])
