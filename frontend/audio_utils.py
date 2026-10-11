"""Audio processing utilities for voice recording without pydub dependency."""

import logging
import wave
import io
from typing import Optional, List

logger = logging.getLogger(__name__)


def convert_audio_frames_to_wav(audio_frames: List, sample_rate: int = 16000, channels: int = 1) -> Optional[bytes]:
    """
    Convert audio frames from PyAV to WAV format.

    Args:
        audio_frames: List of av.AudioFrame objects from streamlit-webrtc
        sample_rate: Sample rate in Hz (default 16000 for Whisper)
        channels: Number of audio channels (default 1 for mono)

    Returns:
        WAV file as bytes, or None if conversion fails
    """
    if not audio_frames:
        logger.warning("No audio frames to convert")
        return None

    try:
        # Create in-memory WAV file
        wav_buffer = io.BytesIO()

        # Calculate sample width from the first frame
        sample_width = 2  # 16-bit audio = 2 bytes per sample

        # Create WAV file writer
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)

            # Write audio data from frames
            for frame in audio_frames:
                try:
                    # Get raw audio data from frame
                    audio_data = frame.to_ndarray().tobytes()
                    wav_file.writeframes(audio_data)
                except Exception as e:
                    logger.warning(f"Error processing audio frame: {str(e)}")
                    continue

        wav_bytes = wav_buffer.getvalue()
        logger.info(f"Successfully converted {len(audio_frames)} frames to WAV ({len(wav_bytes)} bytes)")

        return wav_bytes

    except Exception as e:
        logger.error(f"Error converting audio frames to WAV: {type(e).__name__}: {str(e)}")
        return None


def audio_frames_available(webrtc_ctx) -> bool:
    """
    Check if audio frames are available from WebRTC context.

    Args:
        webrtc_ctx: WebRTC streamer context from streamlit-webrtc

    Returns:
        True if audio processor and frames are available
    """
    try:
        return (
            webrtc_ctx is not None
            and hasattr(webrtc_ctx, 'audio_processor')
            and webrtc_ctx.audio_processor is not None
            and hasattr(webrtc_ctx.audio_processor, 'frames')
            and len(webrtc_ctx.audio_processor.frames) > 0
        )
    except Exception as e:
        logger.warning(f"Error checking audio frames: {str(e)}")
        return False
