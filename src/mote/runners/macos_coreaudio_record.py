"""Multi-buffered macOS CoreAudio AudioQueue microphone recorder piped to 16kHz mono WAV via ffmpeg."""

from __future__ import annotations

import ctypes
import json
import signal
import subprocess
import sys
import threading
from typing import Any

cfg = json.loads(sys.argv[1])
ffmpeg_bin = cfg["ffmpeg_bin"]
out_wav = cfg["out_wav"]
af_str = cfg.get("af_str", "")
mic_device = cfg.get("mic_device", "default")

ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
at = ctypes.CDLL("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox")
cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
cf.CFStringGetCString.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_long,
    ctypes.c_uint32,
]
cf.CFStringGetCString.restype = ctypes.c_bool


class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


def fourcc(s: str) -> int:
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


def get_cfstr(val_ptr: Any) -> str:
    if not val_ptr:
        return ""
    buf = ctypes.create_string_buffer(512)
    if cf.CFStringGetCString(val_ptr, buf, 512, 0x08000100):
        return buf.value.decode("utf-8", "replace")
    return ""


selected_uid_ptr: int | None = None
try:
    addr_devs = AudioObjectPropertyAddress(fourcc("dev#"), fourcc("glob"), 0)
    sz = ctypes.c_uint32(0)
    ca.AudioObjectGetPropertyDataSize(1, ctypes.byref(addr_devs), 0, None, ctypes.byref(sz))
    count = sz.value // 4
    devs = (ctypes.c_uint32 * count)()
    ca.AudioObjectGetPropertyData(1, ctypes.byref(addr_devs), 0, None, ctypes.byref(sz), devs)
    input_devs: list[tuple[int, str, int | None]] = []
    for d in devs:
        addr_in = AudioObjectPropertyAddress(fourcc("stm#"), fourcc("inpt"), 0)
        sz_in = ctypes.c_uint32(0)
        ca.AudioObjectGetPropertyDataSize(d, ctypes.byref(addr_in), 0, None, ctypes.byref(sz_in))
        if sz_in.value > 0:
            addr_nm = AudioObjectPropertyAddress(fourcc("lnam"), fourcc("glob"), 0)
            nm_ptr = ctypes.c_void_p()
            sz_p = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
            ca.AudioObjectGetPropertyData(
                d, ctypes.byref(addr_nm), 0, None, ctypes.byref(sz_p), ctypes.byref(nm_ptr)
            )
            name = get_cfstr(nm_ptr.value)
            addr_uid = AudioObjectPropertyAddress(fourcc("uid "), fourcc("glob"), 0)
            uid_ptr = ctypes.c_void_p()
            sz_u = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
            ca.AudioObjectGetPropertyData(
                d, ctypes.byref(addr_uid), 0, None, ctypes.byref(sz_u), ctypes.byref(uid_ptr)
            )
            input_devs.append((d, name, uid_ptr.value))
            # Reset nominal sample rate to 48000 Hz if left at 96000 Hz by avfoundation
            addr_sr = AudioObjectPropertyAddress(fourcc("nsrt"), fourcc("glob"), 0)
            sr = ctypes.c_double(0.0)
            sz_sr = ctypes.c_uint32(8)
            ca.AudioObjectGetPropertyData(
                d, ctypes.byref(addr_sr), 0, None, ctypes.byref(sz_sr), ctypes.byref(sr)
            )
            if sr.value > 48000.0:
                new_sr = ctypes.c_double(48000.0)
                ca.AudioObjectSetPropertyData(
                    d, ctypes.byref(addr_sr), 0, None, 8, ctypes.byref(new_sr)
                )

    clean_req = (mic_device or "").strip().lstrip(":")
    if clean_req and clean_req not in ("default", ""):
        if clean_req.isdigit():
            idx = int(clean_req)
            if 0 <= idx < len(input_devs):
                selected_uid_ptr = input_devs[idx][2]
        else:
            for _d, nm, u_ptr in input_devs:
                if clean_req.lower() in nm.lower():
                    selected_uid_ptr = u_ptr
                    break
except Exception:
    pass

af_args = ["-af", af_str] if af_str else []
ff_cmd = [
    ffmpeg_bin,
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    "-fflags",
    "+nobuffer",
    "-flags",
    "low_delay",
    "-f",
    "s16le",
    "-ar",
    "16000",
    "-ac",
    "1",
    "-i",
    "pipe:0",
    *af_args,
    "-ac",
    "1",
    "-ar",
    "16000",
    "-c:a",
    "pcm_s16le",
    "-flush_packets",
    "1",
    out_wav,
]
ff_proc = subprocess.Popen(
    ff_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
)


class AudioStreamBasicDescription(ctypes.Structure):
    _fields_ = [
        ("mSampleRate", ctypes.c_double),
        ("mFormatID", ctypes.c_uint32),
        ("mFormatFlags", ctypes.c_uint32),
        ("mBytesPerPacket", ctypes.c_uint32),
        ("mFramesPerPacket", ctypes.c_uint32),
        ("mBytesPerFrame", ctypes.c_uint32),
        ("mChannelsPerFrame", ctypes.c_uint32),
        ("mBitsPerChannel", ctypes.c_uint32),
        ("mReserved", ctypes.c_uint32),
    ]


class AudioQueueBuffer(ctypes.Structure):
    _fields_ = [
        ("mAudioDataBytesCapacity", ctypes.c_uint32),
        ("mAudioData", ctypes.c_void_p),
        ("mAudioDataByteSize", ctypes.c_uint32),
        ("mUserData", ctypes.c_void_p),
        ("mPacketDescriptionCapacity", ctypes.c_uint32),
        ("mPacketDescriptions", ctypes.c_void_p),
        ("mPacketDescriptionCount", ctypes.c_uint32),
    ]


AudioQueueInputCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.POINTER(AudioQueueBuffer),
    ctypes.c_void_p,
    ctypes.c_uint32,
    ctypes.c_void_p,
)

stop_evt = threading.Event()


def input_callback(
    _in_user_data: Any,
    in_aq: Any,
    in_buffer: Any,
    _in_start_time: Any,
    _in_num_packets: int,
    _in_packet_desc: Any,
) -> None:
    if stop_evt.is_set():
        return
    buf = in_buffer.contents
    if buf.mAudioDataByteSize > 0 and ff_proc.stdin:
        raw = ctypes.string_at(buf.mAudioData, buf.mAudioDataByteSize)
        try:
            ff_proc.stdin.write(raw)
            ff_proc.stdin.flush()
        except Exception:
            stop_evt.set()
            return
    if not stop_evt.is_set():
        at.AudioQueueEnqueueBuffer(in_aq, in_buffer, 0, None)


cb = AudioQueueInputCallback(input_callback)
fmt = AudioStreamBasicDescription(
    mSampleRate=16000.0,
    mFormatID=0x6C70636D,
    mFormatFlags=0x0C,
    mBytesPerPacket=2,
    mFramesPerPacket=1,
    mBytesPerFrame=2,
    mChannelsPerFrame=1,
    mBitsPerChannel=16,
    mReserved=0,
)

q = ctypes.c_void_p()
res = at.AudioQueueNewInput(ctypes.byref(fmt), cb, None, None, None, 0, ctypes.byref(q))
if res != 0:
    sys.exit(res)

if selected_uid_ptr:
    uid_val = ctypes.c_void_p(selected_uid_ptr)
    at.AudioQueueSetProperty(
        q, fourcc("aqcd"), ctypes.byref(uid_val), ctypes.sizeof(ctypes.c_void_p)
    )

bufs = (ctypes.POINTER(AudioQueueBuffer) * 16)()
for i in range(16):
    at.AudioQueueAllocateBuffer(q, 3200, ctypes.byref(bufs[i]))
    at.AudioQueueEnqueueBuffer(q, bufs[i], 0, None)


def _sig_handler(_signum: int, _frame: Any) -> None:
    stop_evt.set()


signal.signal(signal.SIGINT, _sig_handler)
signal.signal(signal.SIGTERM, _sig_handler)


def _stdin_watcher() -> None:
    try:
        for line in sys.stdin:
            if "q" in line.lower():
                stop_evt.set()
                break
    except Exception:
        pass
    stop_evt.set()


threading.Thread(target=_stdin_watcher, daemon=True).start()

res = at.AudioQueueStart(q, None)
if res != 0:
    sys.exit(res)

while not stop_evt.wait(0.05):
    if ff_proc.poll() is not None:
        break

at.AudioQueueStop(q, True)
at.AudioQueueDispose(q, True)
if ff_proc.stdin:
    try:
        ff_proc.stdin.close()
    except Exception:
        pass
try:
    ff_proc.wait(timeout=3)
except Exception:
    ff_proc.kill()
