import pytest
from app.services.processing import rms_db

np = pytest.importorskip("numpy")  # installed with faster-whisper


def test_rms_db_of_known_signals():
    t = np.arange(16000) / 16000
    sine = (0.5 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    assert rms_db(sine) == pytest.approx(-9.03, abs=0.05)  # 0.5 / sqrt(2) RMS
    assert rms_db(sine * 0.1) == pytest.approx(-29.03, abs=0.05)  # 10x quieter = -20 dB
    assert rms_db(np.zeros(100, dtype="float32")) < -100
    assert rms_db(np.array([], dtype="float32")) is None
