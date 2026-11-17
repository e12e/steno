"""WhisperX ASR, Alignment, and Pyannote Diarization stage runner executed in the WhisperX environment."""

from __future__ import annotations

import gc
import json
import sys
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

import torch
import whisperx
from whisperx.audio import load_audio
from whisperx.diarize import DiarizationPipeline, assign_word_speakers


def save_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    audio_path = cfg["audio_path"]
    ckpt_dir = Path(cfg["ckpt_dir"])
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    stages = cfg["stages"]
    device = cfg.get("device") or ("cuda" if torch.cuda.is_available() else "cpu")
    compute_type = cfg.get("compute_type", "int8")
    language = cfg.get("language") or "no"
    model_name = cfg["model"]
    hf_token = cfg.get("hf_token") or None
    min_spk = cfg.get("min_speakers")
    max_spk = cfg.get("max_speakers")
    initial_prompt = cfg.get("initial_prompt") or None

    asr_json = ckpt_dir / "1_asr.json"
    align_json = ckpt_dir / "2_align.json"
    diarize_json = ckpt_dir / "3_diarize.json"

    audio_arr: Any = None

    def get_audio() -> Any:
        nonlocal audio_arr
        if audio_arr is None:
            audio_arr = load_audio(audio_path)
        return audio_arr

    result: dict[str, Any] | None = None

    if "asr" in stages or not asr_json.is_file():
        print("STAGE:ASR:START", flush=True)
        eff_lang = None if (not language or str(language).lower() == "auto") else language
        asr_options = {
            "beam_size": 5,
            "patience": 1.0,
            "length_penalty": 1.0,
            "repetition_penalty": 1.15,
            "temperatures": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
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
        result = model.transcribe(
            get_audio(),
            batch_size=8,
            language=eff_lang,
            print_progress=True,
        )
        assert result is not None
        detected_lang = str(result.get("language") or eff_lang or "no")
        result["language"] = detected_lang
        print(f"STAGE:DETECTED_LANG:{detected_lang}", flush=True)
        save_atomic_json(asr_json, result)
        print("STAGE:ASR:DONE", flush=True)
        del model
        gc.collect()
    else:
        result = json.loads(asr_json.read_text(encoding="utf-8"))

    if "align" in stages or ("diarize" in stages and not align_json.is_file()):
        print("STAGE:ALIGN:START", flush=True)
        if result is None:
            result = json.loads(asr_json.read_text(encoding="utf-8"))
        assert result is not None
        if result.get("segments"):
            raw_align_lang = result.get("language") or language
            align_lang = "no" if str(raw_align_lang).lower() in ("auto", "") else str(raw_align_lang)
            align_model_name = None
            if align_lang in ("no", "nb", "nn") and any(
                tag in model_name.lower() for tag in ("small", "base", "tiny")
            ):
                align_model_name = "NbAiLab/nb-wav2vec2-300m-bokmaal"
            model_a, metadata = whisperx.load_align_model(
                language_code=align_lang, device=device, model_name=align_model_name
            )
            result = whisperx.align(
                result["segments"],
                model_a,
                metadata,
                get_audio(),
                device,
                return_char_alignments=False,
                print_progress=True,
            )
            assert result is not None
            result["language"] = align_lang
            del model_a
            gc.collect()
        save_atomic_json(align_json, result)
        print("STAGE:ALIGN:DONE", flush=True)
    elif align_json.is_file():
        result = json.loads(align_json.read_text(encoding="utf-8"))

    if "diarize" in stages:
        print("STAGE:DIARIZE:START", flush=True)
        if result is None:
            source_file = align_json if align_json.is_file() else asr_json
            result = json.loads(source_file.read_text(encoding="utf-8"))
        assert result is not None

        if min_spk == 1 and max_spk == 1:
            print(
                "En-talers modus (1 taler): tilordner SPEAKER_00 direkte uten Pyannote-ventetid.",
                flush=True,
            )
            for seg in result.get("segments", []):
                seg["speaker"] = "SPEAKER_00"
                for w in seg.get("words", []):
                    if isinstance(w, dict):
                        w["speaker"] = "SPEAKER_00"
        else:
            diarize_model = DiarizationPipeline(
                model_name="pyannote/speaker-diarization-community-1",
                token=hf_token,
                device=device,
            )

            def _on_diarize_progress(pct: float) -> None:
                print(f"DiarizeProgress: {pct:.2f}%", flush=True)

            diarize_segments = diarize_model(
                get_audio(),
                min_speakers=min_spk,
                max_speakers=max_spk,
                progress_callback=_on_diarize_progress,
            )
            result = assign_word_speakers(diarize_segments, result)
            del diarize_model
            gc.collect()

        assert result is not None
        save_atomic_json(diarize_json, result)
        print("STAGE:DIARIZE:DONE", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
