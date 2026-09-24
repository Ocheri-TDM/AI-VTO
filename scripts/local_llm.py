"""Start the optional portable Ollama installed in .local, without a console window."""
import os
import subprocess
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    try:
        response = httpx.get('http://127.0.0.1:11434/api/version', timeout=2, trust_env=False)
        response.raise_for_status()
        print('Ollama is already running on 127.0.0.1:11434.')
        return
    except httpx.HTTPError:
        pass
    executable = ROOT / '.local/ollama/ollama.exe'
    if not executable.exists():
        raise SystemExit('Portable Ollama is not installed; follow the Ollama setup in README.')
    env = {**os.environ, 'OLLAMA_HOST': '127.0.0.1:11434',
        'OLLAMA_MODELS': str(ROOT / '.local/ollama-models'), 'OLLAMA_NUM_PARALLEL': '1',
        'OLLAMA_CONTEXT_LENGTH': '8192'}
    with (ROOT / '.local/ollama-stdout.log').open('ab') as out, (ROOT / '.local/ollama-stderr.log').open('ab') as err:
        process = subprocess.Popen([str(executable), 'serve'], env=env, stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
    print(f'Ollama started: PID {process.pid}, endpoint 127.0.0.1:11434.')


if __name__ == '__main__':
    main()
