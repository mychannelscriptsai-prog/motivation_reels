import os
import tempfile
import uuid
import subprocess
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME")
UPLOAD_PRESET = os.environ.get("CLOUDINARY_UPLOAD_PRESET")
FOLDER = os.environ.get("CLOUDINARY_FOLDER", "merged_reels")


class MergeRequest(BaseModel):
    scene34_url: str
    scene35_url: str
    scene37_url: str
    cta_url: str
    voice_url: str
    total_duration_sec: int = 10
    cta_duration_sec: int = 6
    music_volume: float = 0.15


@app.get("/")
def health():
    return {"status": "ok"}


def download(url: str, out: Path):
    try:
        r = requests.get(url, stream=True, timeout=120)
        r.raise_for_status()

        with open(out, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)

    except Exception as e:
        raise RuntimeError(f"Download failed: {url} -> {e}")


def get_audio_duration(audio_file: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_file),
    ]

    p = subprocess.run(cmd, capture_output=True, text=True)

    if p.returncode != 0:
        raise RuntimeError("ffprobe failed")

    try:
        return float(p.stdout.strip())
    except:
        return 10.0


def run_ffmpeg(scene_paths, cta_path, audio_path, out_path, total_duration, cta_duration, volume):
    total_duration = max(total_duration, cta_duration + 1)
    scenes_total = total_duration - cta_duration
    scene_len = scenes_total / 3
    fade = 1.0

    inputs = []
    for s in scene_paths:
        inputs += ["-stream_loop", "-1", "-i", str(s)]
    inputs += ["-stream_loop", "-1", "-i", str(cta_path)]
    inputs += ["-i", str(audio_path)]

    vf = ""
    for i in range(4):
        vf += f"[{i}:v]scale=720:1280,fps=30,format=yuv420p[v{i}];"

    vf += f"[v0][v1]xfade=transition=fade:duration={fade}:offset={scene_len-fade}[v01];"
    vf += f"[v01][v2]xfade=transition=fade:duration={fade}:offset={scene_len*2-fade}[v02];"

    offset_cta = max(scene_len * 3 - fade, 0)
    vf += f"[v02][v3]xfade=transition=fade:duration={fade}:offset={offset_cta}[v]"

    af = f"[4:a]volume={volume},apad[a]"

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex",
        vf + ";" + af,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        str(out_path),
    ]

    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-2000:])


def upload_cloudinary(video_path: Path):
    if not CLOUD_NAME or not UPLOAD_PRESET:
        raise RuntimeError("Cloudinary env vars missing")

    url = f"https://api.cloudinary.com/v1_1/{CLOUD_NAME}/video/upload"
    public_id = f"{FOLDER}/{uuid.uuid4().hex}"

    with open(video_path, "rb") as f:
        r = requests.post(
            url,
            files={"file": f},
            data={
                "upload_preset": UPLOAD_PRESET,
                "public_id": public_id,
                "resource_type": "video",
            },
            timeout=300,
        )

    r.raise_for_status()
    j = r.json()
    if "secure_url" not in j:
        raise RuntimeError("Upload failed")
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

            download(req.scene34_url, scene_paths[0])
            download(req.scene35_url, scene_paths[1])
            download(req.scene37_url, scene_paths[2])
            download(req.cta_url, cta_path)
            download(req.voice_url, audio_path)

            voice_duration = get_audio_duration(audio_path)
            total_duration = max(voice_duration, req.total_duration_sec)

            run_ffmpeg(
                scene_paths,
                cta_path,
                audio_path,
                out_path,
                total_duration,
                req.cta_duration_sec,
                req.music_volume,
            )

            final_url = upload_cloudinary(out_path)
            return {"final_url": final_url}

    except Exception as e:
        print("MERGE ERROR:", str(e))
        raise HTTPException(status_code=500, detail=str(e))
