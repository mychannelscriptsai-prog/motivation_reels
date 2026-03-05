import os
import tempfile
import uuid
import subprocess
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl

CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME")
UPLOAD_PRESET = os.environ.get("CLOUDINARY_UPLOAD_PRESET")
FOLDER = os.environ.get("CLOUDINARY_FOLDER", "merged_reels")

app = FastAPI()


class MergeRequest(BaseModel):
    scene34_url: str
    scene35_url: str
    scene37_url: str
    cta_url: str
    voice_url: str
    total_duration_sec: int = 10
    cta_duration_sec: int = 6
    music_volume: float = 0.15


def _download(url: str, out_path: Path) -> None:
    try:
        with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
    except Exception as e:
        raise RuntimeError(f"Download failed for {url}: {e}")


def _run_ffmpeg(
    scene_paths=scene_paths,
    cta_path=cta_path,
    audio_path=audio_path,
    out_path=out_path,
    total_duration=total_duration,  # dynamisch
    cta_duration=req.cta_duration_sec,
    volume=req.music_volume
) -> None:
    pexels_total = total_duration - cta_duration
    scene_dur = pexels_total / 3
    fade_duration = 1.0

    inputs = []
    for s in scene_paths:
        inputs.extend(["-stream_loop", "-1", "-i", str(s)])
    inputs.extend(["-stream_loop", "-1", "-i", str(cta_path), "-i", str(audio_path)])

    # Video filters
    vf = ""
    for i, _ in enumerate(scene_paths):
        vf += f"[{i}:v]scale=720:1280:flags=bicubic,fps=30,format=yuv420p[v{i}];"
    vf += f"[{len(scene_paths)}:v]scale=720:1280:flags=bicubic,fps=30,format=yuv420p[v{len(scene_paths)}];"

    # Xfade chain
    xfade_chain = f"[v0][v1]xfade=transition=fade:duration={fade_duration}:offset={scene_dur-fade_duration}[v01];"
    xfade_chain += f"[v01][v2]xfade=transition=fade:duration={fade_duration}:offset={scene_dur*2-fade_duration}[v02];"
    # Last Pexels scene to CTA
    offset_cta = scene_dur*3 - fade_duration
    xfade_chain += f"[v02][v3]xfade=transition=fade:duration={fade_duration}:offset={offset_cta}[v];"

    af = f"[{len(scene_paths)+1}:a]volume={volume}[a]"

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", vf + xfade_chain + af,
        "-map", "[v]",
        "-map", "[a]",
        "-t", str(total_duration),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        str(out_path),
    ]

    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{p.stderr[-2000:]}")


def _upload_to_cloudinary(mp4_path: Path) -> str:
    if not CLOUD_NAME or not UPLOAD_PRESET:
        raise RuntimeError("Missing Cloudinary env vars")
    url = f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/video/upload"
    public_id = f"{FOLDER}/{uuid.uuid4().hex}"

    with open(mp4_path, "rb") as f:
        files = {"file": f}
        data = {"upload_preset": UPLOAD_PRESET, "public_id": public_id, "resource_type": "video"}
        r = requests.post(url, files=files, data=data, timeout=120)
        r.raise_for_status()
        j = r.json()
        if "secure_url" not in j:
            raise RuntimeError(f"Cloudinary upload missing secure_url: {j}")
        return j["secure_url"]


@app.post("/merge")
def merge(req: MergeRequest):
    try:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            scene_paths = [td / "scene34.mp4", td / "scene35.mp4", td / "scene37.mp4"]
            cta_path = td / "cta.mp4"
            audio_path = td / "voice.mp3"
            out_path = td / "out.mp4"

            _download(req.scene34_url, scene_paths[0])
            _download(req.scene35_url, scene_paths[1])
            _download(req.scene37_url, scene_paths[2])
            _download(req.cta_url, cta_path)
            _download(req.voice_url, audio_path) 
            
# Bereken lengte van de voice-over met ffprobe
def get_audio_duration(audio_file: Path) -> float:
    """
    Return duration in seconds of an audio file using ffprobe
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_file)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())

voice_duration = get_audio_duration(audio_path)

# Stel totale video lengte gelijk aan voice-over
total_duration = max(voice_duration, req.total_duration_sec)
            _run_ffmpeg(
                scene_paths=scene_paths,
                cta_path=cta_path,
                audio_path=audio_path,
                out_path=out_path,
                total_duration=req.total_duration,
                cta_duration=req.cta_duration_sec,
                volume=req.music_volume
            )

            final_url = _upload_to_cloudinary(out_path)

        return {"final_url": final_url}

    except Exception as e:
        print(f"Merge failed: {e}")
        raise HTTPException(status_code=500, detail=f"Merge failed: {str(e)}")
