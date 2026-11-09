"""Streaming 16 kHz resampler for the heartbeat pipeline: continuity across callbacks + anti-aliasing."""

from __future__ import annotations

import numpy as np
import pytest

from deadbymapper.audio.loopback import MonoResampler16k


def _stream(x: np.ndarray, r: MonoResampler16k, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out, i = [], 0
    while i < len(x):
        n = int(rng.integers(64, 1200))  # irregular WASAPI-like block sizes
        out.append(r.process(x[i : i + n]))
        i += n
    return np.concatenate(out)


@pytest.mark.parametrize("sr", [48000, 44100, 16000])
def test_in_band_tone_survives_blocking_without_glitches(sr):
    t = np.arange(sr * 2) / sr
    x = np.sin(2 * np.pi * 100.0 * t)
    y = _stream(x, MonoResampler16k(sr))
    assert abs(len(y) - 32000) <= 2  # 2 s at 16 kHz
    ref = np.sin(2 * np.pi * 100.0 * np.arange(len(y)) / 16000)
    # align the filter's small group delay, then compare sample-by-sample (glitches would show up here)
    best = min(np.max(np.abs(y[200:-200] - np.roll(ref, k)[200:-200])) for k in range(0, 40))
    assert best < 0.02
    assert np.sqrt(np.mean(y[200:-200] ** 2)) == pytest.approx(np.sqrt(0.5), rel=0.01)


@pytest.mark.parametrize("sr", [48000, 44100])
def test_high_frequency_content_does_not_alias_into_the_heartbeat_band(sr):
    t = np.arange(sr * 2) / sr
    f = 16000.0 - 100.0 if sr == 48000 else 15900.0  # would fold down to ~100 Hz without filtering
    x = np.sin(2 * np.pi * f * t)
    y = _stream(x, MonoResampler16k(sr))
    assert np.sqrt(np.mean(y[200:-200] ** 2)) < 0.01  # >= 37 dB suppression of the would-be alias


def test_stereo_downmix_survives_blocks_that_split_frames():
    sr = 48000
    t = np.arange(sr) / sr
    left = np.sin(2 * np.pi * 60.0 * t)
    stereo = np.stack([left, left], axis=1).reshape(-1)
    y = _stream(stereo, MonoResampler16k(sr, channels=2))  # random block sizes, often odd
    assert abs(len(y) - 16000) <= 2  # no samples lost to split frames
    assert np.sqrt(np.mean(y[200:-200] ** 2)) == pytest.approx(np.sqrt(0.5), rel=0.01)
