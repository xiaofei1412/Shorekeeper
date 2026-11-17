"""Download only the pinned files used by ordinary CosyVoice3 inference."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

MODEL_ID = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
REVISION = "29e01c4e8d000f4bcd70751be16fa94bf3d85a18"
REQUIRED = {
    "README.md", "config.json", "configuration.json", "cosyvoice3.yaml",
    "campplus.onnx", "speech_tokenizer_v3.onnx", "llm.pt", "flow.pt", "hift.pt",
}


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    url = f"https://huggingface.co/api/models/{MODEL_ID}/revision/{REVISION}?blobs=true"
    with urllib.request.urlopen(url, timeout=60) as response:
        metadata = json.load(response)
    if metadata["sha"] != REVISION:
        raise ValueError("Model revision differs from pinned revision")
    files = [item for item in metadata["siblings"]
             if item["rfilename"] in REQUIRED or item["rfilename"].startswith("CosyVoice-BlankEN/")]

    def download(item):
        name = item["rfilename"]
        path = args.model_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        expected = item.get("lfs", {}).get("sha256")
        size = item["size"]
        if path.exists():
            digest = sha256(path)
            if path.stat().st_size != size or (expected and digest != expected):
                raise ValueError(f"Existing file differs from upstream: {name}")
        else:
            print(f"Downloading {name}: {size} bytes", flush=True)
            part = path.with_name(path.name + ".part")
            request = urllib.request.Request(f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}")
            with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as f:
                shutil.copyfileobj(response, f, length=8 * 1024 * 1024)
            digest = sha256(part)
            if part.stat().st_size != size or (expected and digest != expected):
                raise ValueError(f"Downloaded file hash/size mismatch: {name}")
            part.rename(path)
        print(f"Verified {name}: {digest}", flush=True)
        return {"path": name, "size_bytes": size, "sha256": digest, "upstream_lfs_sha256": expected}

    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(download, files))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"model_id": MODEL_ID, "revision": REVISION,
                                      "metadata_url": url, "files": records}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
