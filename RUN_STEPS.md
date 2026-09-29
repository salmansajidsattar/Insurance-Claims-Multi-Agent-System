# Run steps (Windows, nothing big on C:)

Everything below uses **PowerShell**. Change the three paths in step 0 if you want other folders.

## 0. Pick your folders

| What | Folder used below |
|---|---|
| Ollama program | `F:\Ollama\app` |
| Ollama models (~11 GB) | `F:\Ollama\models` |
| This project | `F:\Insurance Claims Multi-Agent System` |

## 1. Install Python 3.11+ (skip if you have it)

Download from python.org → run installer → **Customize installation** → set the install
location to e.g. `F:\Python311` → tick **Add python.exe to PATH**.

```powershell
python --version        # should print 3.11 or newer
```

## 2. Install Ollama on F: (not C:)

Download `OllamaSetup.exe` from ollama.com, then in the folder where it was downloaded:

```powershell
.\OllamaSetup.exe /DIR="F:\Ollama\app"
```

## 3. Tell Ollama to keep models on F:

```powershell
mkdir F:\Ollama\models
setx OLLAMA_MODELS "F:\Ollama\models"
```

Then **quit Ollama from the system tray** (right-click the llama icon → Quit), **close
PowerShell**, open a new PowerShell and start Ollama again from the Start menu. Check:

```powershell
echo $env:OLLAMA_MODELS        # must print F:\Ollama\models
```

> Already downloaded models on C:? Quit Ollama, move the folder
> `C:\Users\<you>\.ollama\models` to `F:\Ollama\models`, then do step 3.

## 4. Download the 3 models

```powershell
ollama pull qwen2.5vl:7b          # vision, ~6 GB   (low VRAM: qwen2.5vl:3b)
ollama pull qwen2.5:7b            # text,   ~4.7 GB
ollama pull nomic-embed-text      # embeddings, ~0.3 GB
ollama list                       # all 3 should be listed
dir F:\Ollama\models              # files should be here, not on C:
```

## 5. Create the Python environment (inside the project, on F:)

```powershell
cd "F:\Insurance Claims Multi-Agent System"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activate script, run this once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Install packages with the pip cache on F: too:

```powershell
$env:PIP_CACHE_DIR = "F:\pip-cache"
pip install -r requirements.txt
```

## 6. Create the settings file

```powershell
copy .env.example .env
```

If you used `qwen2.5vl:3b` in step 4, open `.env` and set `VLM_MODEL=qwen2.5vl:3b`.

## 7. Run the tests (no models needed)

```powershell
pytest
```

Expected: `123 passed, 3 skipped`.

## 8. Quick check with the real models

```powershell
$env:RUN_LIVE = "1"
pytest tests/test_live.py -v -s
$env:RUN_LIVE = ""
```

## 9. Full eval with the real models (gives the pass rate)

```powershell
python -m tests.eval
```

It prints PASS/FAIL per claim and the path of the report in `reports\`.
On CPU expect 30–60 s per claim. Re-run only some claims:

```powershell
python -m tests.eval --only CLM-EVAL-010 CLM-EVAL-011
```

## 10. Start the app (two PowerShell windows)

Window 1 — backend:

```powershell
cd "F:\Insurance Claims Multi-Agent System"
.\.venv\Scripts\Activate.ps1
uvicorn app.api:app --reload
```

Window 2 — frontend:

```powershell
cd "F:\Insurance Claims Multi-Agent System"
.\.venv\Scripts\Activate.ps1
streamlit run app/ui.py
```

Open http://localhost:8501 (UI) — API docs at http://localhost:8000/docs.

## 11. Try a claim

(To understand the code, read `CODE_WALKTHROUGH.md`.)

In the UI: **Submit claim** → pick sample `CLM-EVAL-001` → the package table appears →
**Submit claim** → wait → verdict + agent timeline. Other good samples: `CLM-EVAL-010`
(photo doesn't match story → review), `CLM-EVAL-017` (drunk driving → reject).

Sample incident dates are fixed; as time passes some samples become "reported too late".
Change the date in the form if that happens.

## Troubleshooting

| Problem | Fix |
|---|---|
| Models still go to C: | `OLLAMA_MODELS` not picked up — quit Ollama from the tray, open a new PowerShell, start Ollama again (step 3) |
| Every claim goes to human review with `agent_failure` | Ollama not running or a model missing — run `ollama list`; look at the claim's timeline for the error |
| Timeouts on CPU | In `.env` raise `LLM_TIMEOUT_S=300` |
| Out of GPU memory | Use `qwen2.5vl:3b` (step 4 + step 6) |
