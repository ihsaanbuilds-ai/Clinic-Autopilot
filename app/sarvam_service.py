import os
import base64
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("sarvam_service")

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"

LANGUAGE_CODE_MAP = {
    "malayalam": "ml-IN",
    "ml": "ml-IN",
    "hindi": "hi-IN",
    "hi": "hi-IN",
    "tamil": "ta-IN",
    "ta": "ta-IN",
    "marathi": "mr-IN",
    "mr": "mr-IN",
    "english": "en-IN",
    "en": "en-IN"
}

def transcribe_audio_sarvam(audio_bytes: bytes, filename: str = "caller_audio.wav", language_code: str = "unknown") -> str | None:
    if not SARVAM_API_KEY:
        logger.error("SARVAM_API_KEY is not configured.")
        return None

    headers = {
        "api-subscription-key": SARVAM_API_KEY
    }

    data = {
        "model": "saaras:v3"
    }
    if language_code in LANGUAGE_CODE_MAP.values():
        data["language_code"] = language_code

    files = {
        "file": (filename, audio_bytes, "audio/wav")
    }

    try:
        response = requests.post(SARVAM_STT_URL, headers=headers, data=data, files=files, timeout=15)
        if response.status_code == 200:
            result = response.json()
            transcript = result.get("transcript", "").strip()
            detected_lang = result.get("language_code", "unknown")
            logger.info(f"Sarvam STT success ({detected_lang}): {transcript}")
            return transcript
        else:
            logger.error(f"Sarvam STT failed with status {response.status_code}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error calling Sarvam STT: {e}")
        return None


def text_to_speech_sarvam(text: str, language_code: str = "en-IN", speaker: str = "kavya") -> bytes | None:
    if not SARVAM_API_KEY:
        logger.error("SARVAM_API_KEY is not configured.")
        return None

    headers = {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json"
    }

    target_lang = LANGUAGE_CODE_MAP.get(language_code.lower(), language_code)

    payload = {
        "inputs": [text[:500]],
        "target_language_code": target_lang,
        "speaker": speaker,
        "pitch": 0,
        "pace": 1.0,
        "loudness": 1.5,
        "speech_sample_rate": 8000,
        "enable_preprocessing": True,
        "model": "bulbul:v3"
    }

    try:
        response = requests.post(SARVAM_TTS_URL, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            result = response.json()
            audios = result.get("audios", [])
            if audios:
                audio_bytes = base64.b64decode(audios[0])
                logger.info(f"Sarvam TTS generated {len(audio_bytes)} bytes of audio ({target_lang})")
                return audio_bytes
        else:
            logger.error(f"Sarvam TTS failed with status {response.status_code}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Error calling Sarvam TTS: {e}")
        return None
