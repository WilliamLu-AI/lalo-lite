"""Incremental JSON speech extraction; only complete sentences are released."""
import json
import re


class SpeechStream:
    def __init__(self):
        self.raw = ''
        self.emitted = 0

    def feed(self, delta, final=False):
        self.raw += delta
        if len(self.raw) > 32000:
            raise ValueError('Answer exceeded streaming limit')
        match = re.search(r'"speech"\s*:\s*"', self.raw)
        if not match:
            return []
        source = self.raw[match.end():]
        text = ''
        index = 0
        while index < len(source):
            char = source[index]
            if char == '"':
                break
            if char == '\\':
                length = 6 if source[index:index + 2] == '\\u' else 2
                if index + length > len(source):
                    break
                text += json.loads('"' + source[index:index + length] + '"')
                index += length
            else:
                text += char
                index += 1
        # A provider may split a JSON UTF-16 surrogate pair across token events.
        if text and 0xD800 <= ord(text[-1]) <= 0xDBFF:
            text = text[:-1]
        text = text.encode('utf-16-le', errors='surrogatepass').decode('utf-16-le')
        if len(text) > 1600:
            raise ValueError('Speech exceeded limit')
        sentences = []
        # Keep sentence boundaries stable even across provider token chunks.
        for end in re.finditer(r'[。！？!?\n]|\.(?=\s)', text[self.emitted:]):
            stop = self.emitted + end.end()
            sentences.append(stop)
        result = []
        for stop in sentences:
            part = text[self.emitted:stop]
            self.emitted = stop
            if part.strip():
                result.append(part.strip())
        if final and text[self.emitted:].strip():
            result.append(text[self.emitted:].strip())
            self.emitted = len(text)
        return result

    def result(self):
        text = self.raw.strip()
        if text.startswith('```'):
            text = '\n'.join(text.splitlines()[1:-1])
        result = json.loads(text)
        if not isinstance(result, dict) or not isinstance(result.get('speech'), str):
            raise ValueError('Invalid streamed answer')
        return result


def is_backchannel(text):
    normalized = re.sub(r'[\s，。！？,.!?、]', '', text).lower()
    return normalized in {'嗯', '嗯嗯', '哦', '噢', '对', '对对', '是的', '好的', '好', 'ok', 'okay', 'uhhuh', 'yeah'}
