#!/usr/bin/env python3
"""
Test script for verifying Xiaomi MiMo ASR, TTS, and MiniMax VLM Cloud APIs.
Usage:
    python3 test_companion_cloud_services.py
"""

import os
import sys
import time
import tempfile

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pidog.companion.config import CompanionConfig, load_dotenv
from pidog.companion.adapters.factory import AdapterFactory

# Ensure .env is loaded
load_dotenv()


def print_section(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def test_minimax_vlm():
    print_section("1. Testing MiniMax VLM (Text & Multimodal)")
    config = CompanionConfig()
    vlm = AdapterFactory.create_vlm(config.vlm)

    if not vlm or not vlm.is_available():
        print("❌ MiniMax API Key missing or not configured in .env (MINIMAX_API_KEY)")
        return False

    print(f"• Model: {vlm.model}")
    print(f"• Base URL: {vlm.base_url}")
    print("• Sending prompt: '你好旺财，自我介绍一下，并做出一个开心的动作...'")

    start_time = time.time()
    response = vlm.generate(
        prompt="你好旺财，自我介绍一下，并做出一个开心的动作。",
        system_prompt=(
            "你是智能伴侣机器狗PiDog（旺财）。"
            "你必须在回复开头使用 [emotion:xxx] 和 [action:xxx] 标签表明你的情绪和动作。"
            "例如：[emotion:happy][action:wag_tail] 汪汪！我是旺财！"
        )
    )
    elapsed = time.time() - start_time

    if response:
        print(f"✅ MiniMax VLM Success (Response Time: {elapsed:.2f}s):")
        print("-" * 50)
        print(response)
        print("-" * 50)
        return True
    else:
        print(f"❌ MiniMax VLM failed to return a response.")
        return False


def test_xiaomi_tts():
    print_section("2. Testing Xiaomi MiMo-V2.5-TTS (Streaming & Audio Output)")
    config = CompanionConfig()
    tts = AdapterFactory.create_tts(config.tts)

    if not tts or not tts.is_available():
        print("❌ Xiaomi API Key missing or not configured in .env (MIMO_API_KEY)")
        return False

    test_text = "主人你好！我是你的智能伴侣小狗旺财，很高兴认识你！汪汪！"
    print(f"• Model: {tts.model}")
    print(f"• Voice: {tts.voice}")
    print(f"• Style: {tts.default_style_prompt}")
    print(f"• Target Text: {test_text}")

    # 1. Test SSE Stream
    print("\n[A] Testing SSE Streaming Synthesis (synthesize_stream)...")
    start_time = time.time()
    first_chunk_time = None
    total_bytes = 0
    chunk_count = 0

    for chunk in tts.synthesize_stream(test_text):
        if first_chunk_time is None:
            first_chunk_time = time.time() - start_time
        total_bytes += len(chunk)
        chunk_count += 1

    if chunk_count > 0:
        print(f"✅ TTS Stream Success: {chunk_count} chunks received, total {total_bytes} bytes PCM data.")
        print(f"• First Chunk Latency (TTFB): {first_chunk_time:.2f}s, Total: {time.time() - start_time:.2f}s")
    else:
        print("❌ TTS Stream returned no data.")

    # 2. Test Non-streaming WAV generation and save
    print("\n[B] Testing Complete Audio Generation (synthesize -> wav)...")
    output_wav = os.path.join(tempfile.gettempdir(), "pidog_test_tts.wav")
    audio_data = tts.synthesize(test_text, output_path=output_wav, format="wav")

    if audio_data and os.path.exists(output_wav):
        file_size = os.path.getsize(output_wav)
        print(f"✅ WAV Saved: {output_wav} ({file_size} bytes)")
        return output_wav
    else:
        print("❌ TTS synthesis failed.")
        return None


def test_xiaomi_asr(audio_file_path: str = None):
    print_section("3. Testing Xiaomi MiMo-V2.5-ASR (Speech-to-Text)")
    config = CompanionConfig()
    asr = AdapterFactory.create_asr(config.asr)

    if not asr or not asr.is_available():
        print("❌ Xiaomi API Key missing or not configured in .env (MIMO_API_KEY)")
        return False

    print(f"• Model: {asr.model}")
    print(f"• Base URL: {asr.base_url}")

    if not audio_file_path or not os.path.exists(audio_file_path):
        print("⚠️ No input audio file available for ASR test.")
        return False

    print(f"• Feeding generated audio from TTS into ASR: {audio_file_path}")
    start_time = time.time()
    with open(audio_file_path, "rb") as f:
        audio_bytes = f.read()

    transcribed_text = asr.transcribe(audio_bytes, format="wav")
    elapsed = time.time() - start_time

    if transcribed_text:
        print(f"✅ Xiaomi ASR Success (Latency: {elapsed:.2f}s):")
        print("-" * 50)
        print(f"Recognition Result: '{transcribed_text}'")
        print("-" * 50)
        return True
    else:
        print(f"❌ ASR returned empty result.")
        return False


if __name__ == "__main__":
    print("\n🚀 Starting PiDog Companion Cloud Services API Test...\n")

    # 1. Test MiniMax VLM
    vlm_ok = test_minimax_vlm()

    # 2. Test Xiaomi TTS
    saved_wav_path = test_xiaomi_tts()

    # 3. Test Xiaomi ASR (Using the TTS generated audio as input for end-to-end loop)
    asr_ok = test_xiaomi_asr(saved_wav_path)

    print_section("Test Summary")
    print(f"1. MiniMax M2.7 VLM : {'✅ PASS' if vlm_ok else '❌ FAIL'}")
    print(f"2. Xiaomi MiMo TTS  : {'✅ PASS' if saved_wav_path else '❌ FAIL'}")
    print(f"3. Xiaomi MiMo ASR  : {'✅ PASS' if asr_ok else '❌ FAIL'}")
    print("=" * 60 + "\n")
