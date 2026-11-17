"""Persistent live-chunk Whisper STT + language-detection worker over JSON lines on stdin/stdout."""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

import torch
import whisperx
from faster_whisper import WhisperModel
from whisperx.audio import load_audio


def main() -> int:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    device = cfg.get("device") or ("cuda" if torch.cuda.is_available() else "cpu")
    compute_type = cfg.get("compute_type", "int8")
    raw_lang = cfg.get("language") or "no"
    eff_lang = None if str(raw_lang).lower() in ("auto", "") else str(raw_lang)
    model_name = cfg["model"]
    detect_lang_model = cfg.get("detect_lang_model") or ""
    initial_prompt = cfg.get("initial_prompt") or None

    asr_options = {
        "beam_size": 1,
        "patience": 1.0,
        "length_penalty": 1.0,
        "temperatures": [0.0],
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        "condition_on_previous_text": False,
        "initial_prompt": initial_prompt,
        "suppress_tokens": [-1],
        "suppress_numerals": False,
    }
    model = whisperx.load_model(
        model_name,
        device=device,
        compute_type=compute_type,
        language=eff_lang,
        asr_options=asr_options,
    )
    det_fw_model: Any = None
    print("LIVE_WORKER:READY", flush=True)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        if line == "QUIT":
            break
        try:
            req = json.loads(line)
            wav_path = req["wav_path"]
            audio = load_audio(wav_path)
            detected_lang = ""
            lang_prob = 0.0
            if req.get("detect_language") or eff_lang is None:
                try:
                    if (
                        "nb-whisper" not in model_name.lower()
                        and hasattr(model, "model")
                        and hasattr(model.model, "detect_language")
                    ):
                        d_lang, d_prob, _ = model.model.detect_language(audio)
                        detected_lang = str(d_lang)
                        lang_prob = round(float(d_prob), 3)
                    elif detect_lang_model:
                        if det_fw_model is None:
                            det_fw_model = WhisperModel(
                                detect_lang_model, device=device, compute_type=compute_type
                            )
                        d_lang, d_prob, _ = det_fw_model.detect_language(audio)
                        detected_lang = str(d_lang)
                        lang_prob = round(float(d_prob), 3)
                except Exception:
                    pass

            if req.get("only_detect_language"):
                out_det = {
                    "start": req.get("start", 0.0),
                    "end": req.get("end", 0.0),
                    "text": "",
                    "detected_language": detected_lang or (eff_lang or "no"),
                    "language_probability": lang_prob,
                }
                print("LIVE_RESULT:" + json.dumps(out_det, ensure_ascii=False), flush=True)
                continue

            if (
                detected_lang
                and detected_lang not in ("no", "nb", "nn")
                and lang_prob >= 0.70
                and "nb-whisper" in model_name.lower()
                and det_fw_model is not None
            ):
                segs_iter, _ = det_fw_model.transcribe(
                    audio, language=detected_lang, beam_size=1
                )
                text = " ".join(s.text.strip() for s in segs_iter if s.text.strip()).strip()
            else:
                res = model.transcribe(
                    audio,
                    batch_size=4,
                    language=eff_lang,
                    print_progress=False,
                )
                segs = res.get("segments", [])
                text = " ".join(
                    str(s.get("text", "")).strip()
                    for s in segs
                    if isinstance(s, dict) and str(s.get("text", "")).strip()
                ).strip()
                if not detected_lang and res.get("language"):
                    detected_lang = str(res.get("language"))

            out = {
                "start": req.get("start", 0.0),
                "end": req.get("end", 0.0),
                "text": text,
                "detected_language": detected_lang,
                "language_probability": lang_prob,
            }
            print("LIVE_RESULT:" + json.dumps(out, ensure_ascii=False), flush=True)
        except Exception as exc:
            err = {
                "start": 0.0,
                "end": 0.0,
                "text": "",
                "error": str(exc),
            }
            print("LIVE_RESULT:" + json.dumps(err, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
