"""What the voice routes accept (docs/design/immersive-ui.md §6, §6.1)."""

from __future__ import annotations

MAX_SECONDS = 60
"""Longest recording the screen makes. The server cannot read a recording's length without decoding it, so it
checks the size, and the size does not follow from the length: it depends on the bit rate."""
MAX_AUDIO_BYTES = 2 * 1024 * 1024
"""Sixty seconds at up to about 280 kbit/s: above any bit rate a browser records speech at (Opus is 32 or
less, raw 16 kHz mono PCM is 256). It still allows a much longer recording at a low bit rate, which the
transcript limit below then catches."""
MAX_TRANSCRIPT_CHARS = 2000
"""A question is at most 500 characters; a longer transcript is a recording that was not a question."""
MAX_SPEECH_CHARS = 1200
MAX_HINT_CHARS = 600
"""A recognition hint is a list of words, not a document."""
SOUND_TYPES = frozenset({"audio/webm", "audio/mp4", "audio/mpeg", "audio/wav", "audio/ogg"})
FILE_SUFFIX = {
    "audio/webm": "webm",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
}
"""The provider tells the format by the file name."""
