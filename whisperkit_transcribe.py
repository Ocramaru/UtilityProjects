#!/usr/bin/env python3
"""
Simple Audio Transcription with MLX Whisper

Installation:
pip install mlx-whisper pydub
brew install ffmpeg
"""

import os
import sys
import tempfile
from pathlib import Path
from pydub import AudioSegment
import mlx_whisper


def transcribe_audio(audio_path, chunk_length_sec=60, overlap_sec=2):
    """Transcribe an audio file with overlapping chunks."""

    print(f"Loading {audio_path}...")

    # Load audio
    audio = AudioSegment.from_file(audio_path)
    audio = audio.set_frame_rate(16000).set_channels(1)

    # Create overlapping chunks
    chunk_length_ms = chunk_length_sec * 1000
    overlap_ms = overlap_sec * 1000
    chunks = []

    start = 0
    while start < len(audio):
        end = min(start + chunk_length_ms, len(audio))
        chunks.append(audio[start:end])
        start += chunk_length_ms - overlap_ms

    print(f"Split into {len(chunks)} chunks (1 min each, 2 sec overlap)")
    print("Loading model (downloading on first run)...")

    # Transcribe chunks
    transcripts = []
    for i, chunk in enumerate(chunks, 1):
        print(f"Transcribing chunk {i}/{len(chunks)}...")

        # Export chunk to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            chunk.export(f.name, format="wav")

            # Transcribe
            result = mlx_whisper.transcribe(
                f.name,
                path_or_hf_repo="mlx-community/whisper-large-v3-mlx",
                verbose=False
            )

            transcripts.append(result["text"])
            os.unlink(f.name)

    # Combine
    full_transcript = " ".join(transcripts)

    # Save
    output_path = Path(audio_path).with_suffix('.txt')
    with open(output_path, 'w') as f:
        f.write(full_transcript)

    print(f"\nSaved to: {output_path}")
    print(f"Length: {len(full_transcript)} characters")

    return full_transcript


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 transcribe.py audio_file.mp3")
        sys.exit(1)

    transcribe_audio(sys.argv[1])