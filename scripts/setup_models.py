"""Explicit online preparation only. Application never calls this downloader."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
ASR_REV = '536b0662742c02347bc0e980a01041f333bce120'
LLM_REV = 'bc640142c66e1fdd12af0bd68f40445458f3869b'
LLAMA_SHA = 'de1f437d53c74cdbda8e2071e3f468e31eee26b12a9d73b12d4969c4f0271372'
JOBS = [
    (f'https://huggingface.co/Systran/faster-whisper-small/resolve/{ASR_REV}/{name}',
     f'models/whisper-small/{name}')
    for name in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt', 'README.md')
] + [
    (f'https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/{LLM_REV}/Qwen3-4B-Q4_K_M.gguf',
     'models/qwen/Qwen3-4B-Q4_K_M.gguf'),
    (f'https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/{LLM_REV}/LICENSE', 'models/qwen/Qwen3-LICENSE'),
    ('https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx',
     'models/speaker/wespeaker_en_voxceleb_resnet34_LM.onnx'),
    ('https://github.com/ggml-org/llama.cpp/releases/download/b11125/llama-b11125-bin-win-cpu-x64.zip',
     'runtime/llama-b11125.zip'),
]

def download(job):
    url, relative = job
    target = ROOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.partial')
    if not target.exists():
        request = urllib.request.Request(url, headers={'User-Agent': 'QonimAI-model-setup/1'})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open('wb') as out:
            expected = response.headers.get('Content-Length')
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
        if expected is not None and partial.stat().st_size != int(expected):
            raise RuntimeError('Incomplete download: ' + relative)
        partial.replace(target)
    with target.open('rb') as content:
        digest = hashlib.file_digest(content, 'sha256').hexdigest()
    expected_hashes = {
        'models/whisper-small/model.bin': '3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671',
        'models/qwen/Qwen3-4B-Q4_K_M.gguf': '7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5',
        'models/speaker/wespeaker_en_voxceleb_resnet34_LM.onnx': 'e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012',
        'runtime/llama-b11125.zip': LLAMA_SHA,
    }
    if relative in expected_hashes and digest != expected_hashes[relative]:
        raise RuntimeError('SHA-256 mismatch: ' + relative)
    print(relative, target.stat().st_size, digest, flush=True)
    return {'path': relative, 'url': url, 'bytes': target.stat().st_size, 'sha256': digest}

def main():
    print('Online setup: downloads public packages/weights only; no meeting data is read.', flush=True)
    with ThreadPoolExecutor(max_workers=3) as executor:
        manifest = list(executor.map(download, JOBS))
    archive = ROOT / 'runtime/llama-b11125.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != LLAMA_SHA:
        raise RuntimeError('llama.cpp archive SHA-256 mismatch')
    destination = ROOT / 'runtime/llama'
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            resolved = (destination / info.filename).resolve()
            if not resolved.is_relative_to(destination.resolve()):
                raise RuntimeError('Unsafe archive path')
            if info.is_dir():
                resolved.mkdir(parents=True, exist_ok=True)
                continue
            if resolved.exists():
                if resolved.stat().st_size == info.file_size and zlib.crc32(resolved.read_bytes()) & 0xffffffff == info.CRC:
                    continue  # Do not rewrite DLLs already loaded by the server.
                raise RuntimeError('Existing runtime file differs; stop the server and use a fresh runtime/llama directory.')
            z.extract(info, destination)
    (ROOT / 'models/download-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Models downloaded. Run the smoke check before using the application.', flush=True)

if __name__ == '__main__':
    main()
