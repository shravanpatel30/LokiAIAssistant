# Loki — A Private Local AI Assistant

Loki is a desktop AI assistant that runs entirely on your own machine. No cloud APIs, no data sent to any external service. It can:

- Open apps and websites by voice or text ("open Spotify", "go to youtube.com in Chrome")
- Set reminders with natural-language times ("remind me in 30 minutes to check the oven")
- Schedule recurring events (birthdays, anniversaries, weekly tasks)
- Answer questions about your computer's status (CPU, RAM, disk, battery, uptime, local IP)
- Evaluate arithmetic expressions instantly and locally
- Answer general questions and help with coding via a local LLM
- Listen to push-to-talk voice commands and speak responses
- Live quietly in your system tray, available whenever you press the hotkey
- Read PDFs and answer questions about them, with local semantic search (RAG)
- Convert text and equations to LaTeX
- Search the web — Google Maps, YouTube, Wikipedia, Google, Amazon, GitHub, and more — by voice or text
- A chat window with rendered markdown, code blocks with copy buttons, and drag-and-drop PDF support

Built for Windows. The LLM, speech recognition, and text-to-speech all run locally.

---

## What you need

### Hardware

- **Windows 10 or 11** (64-bit)
- **16 GB RAM** minimum
- **NVIDIA GPU with 8 GB VRAM** strongly recommended (Loki was built and tested on an RTX 3070 Ti laptop GPU). CPU-only inference works but is much slower.
- **15 GB free disk space** for the language model and dependencies

If your hardware is significantly weaker, see the **Smaller hardware** section at the bottom for tweaks.

### Software prerequisites

These must be installed before setting up Loki:

1. **Python 3.10 or newer** — [python.org](https://www.python.org/downloads/). During install, check "Add Python to PATH."
2. **Ollama** — [ollama.com](https://ollama.com). One-click installer. After install, open a terminal and verify with `ollama --version`.
3. **A Piper voice model** for text-to-speech (download instructions below).

### Models used

- `qwen3:8b` — the main language model (routing + chat + PDF Q&A + LaTeX)
- `nomic-embed-text` — embedding model for PDF semantic search

Both run locally through Ollama. Pull both during setup.

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/loki.git
cd loki
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

This installs ~15 packages and takes a few minutes.

If you have an NVIDIA GPU and want Whisper to run on it (faster transcription), additionally install:

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Then edit `voice.py` and change `WHISPER_DEVICE = "cpu"` to `WHISPER_DEVICE = "cuda"`.

### 3. Pull the language model

```bash
ollama pull qwen3:8b
```

This downloads ~5 GB. The model is the brain of the assistant — it handles command interpretation and chat.

#### Pull the embedding model (for PDF reading)

Loki uses a local embedding model to search within PDFs:

```bash
ollama pull nomic-embed-text
```

This is small (~270 MB) and runs locally. Without it, PDF question-answering won't work.

### 4. Download a Piper voice

Loki needs a voice file for text-to-speech. Download from the Piper voices repository:

- Go to [huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/amy/medium](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/amy/medium)
- Download both files: `en_US-amy-medium.onnx` and `en_US-amy-medium.onnx.json`
- Create a folder named `voices` in the project root
- Place both files in that folder

Other voices work too — browse the parent directory for options. If you use a different voice, update `PIPER_VOICE_PATH` in `voice.py`.

### 5. Discover installed applications

This scans your system for installed apps so Loki can launch them by name:

```bash
python discover_apps.py
```

Output goes to `apps.json` (one entry per app). Re-run this script anytime you install or uninstall software.

If specific apps don't appear or need special launch options (like RustDesk's working directory), edit `manual_apps.json` — entries here override anything in `apps.json`.

### 6. Set environment variables (recommended)

For best performance, set these in your Windows environment variables (System Properties → Advanced → Environment Variables):

| Variable | Value | What it does |
|---|---|---|
| `OLLAMA_KEEP_ALIVE` | `2h` | Keeps the LLM in VRAM longer, faster responses |
| `HF_HUB_DISABLE_SYMLINKS_WARNING` | `1` | Silences a benign Hugging Face warning on Windows |

Restart any terminal after setting these.

---

## Running Loki

Three modes, depending on how you want to use it:

### Text mode (for testing)

```bash
python assistant.py
```

Opens a REPL in your terminal. Type commands, see responses inline. Best for verifying everything works.

### Voice mode (terminal stays open)

```bash
python assistant.py --voice
```

Hold **F9** to speak, release to send. Responses are spoken aloud through your default audio device. Type `exit` or close the terminal to quit.

### Tray mode (recommended for daily use)

```bash
python assistant.py --tray
```

Loki runs silently in your system tray. Left-click the tray icon to open the chat window. F9 still works for push-to-talk from anywhere. Right-click the tray icon and choose Quit to exit cleanly.

For fully silent operation with no console window, run with `pythonw.exe` instead of `python.exe`:

```bash
pythonw assistant.py --tray
```

In this mode, all output goes to dated log files in the `logs/` folder.

### Auto-launch at login (optional)

To have Loki start automatically when you log into Windows:

1. Press `Win+R`, type `taskschd.msc`, press Enter
2. **Create Task** (not "Create Basic Task")
3. **General**: Name "Loki", check "Run only when user is logged on"
4. **Triggers**: New → At log on
5. **Actions**: New → Start a program. Program: full path to your `pythonw.exe`. Arguments: full path to `assistant.py --tray`. Start in: the Loki folder.
6. **Settings**: "If the task fails, restart every 1 minute, up to 3 times"

Log out and back in to verify it launches.

---

## Using Loki

### Voice commands

Press and hold **F9**, speak, release. Examples:

- "Open Spotify"
- "Go to youtube.com on Chrome"
- "Close Notepad"
- "Remind me to take out the trash tomorrow at 8 PM"
- "Set a yearly reminder for my anniversary on October 12 at 9 AM"
- "List my reminders"
- "Cancel reminders 4, 5, and 6"
- "What's the speed of light?"
- "Write a Python function to reverse a string"

### Text commands

Same as above, but typed into the chat window or the terminal REPL.

### Utility commands

These work in any mode and don't go through the LLM:

| Command | What it does |
|---|---|
| `exit`, `quit`, `bye`, `goodbye` | Stop the assistant |
| `voice off`, `stop talking`, `be quiet` | Disable spoken replies |
| `voice on`, `speak again` | Re-enable spoken replies |
| `reset`, `clear`, `new chat` | Forget the current conversation |

### Reminders

Reminders persist in `assistant.db` (a SQLite file in the project folder). They survive restarts. If Loki was off when a reminder was due, it fires when you next launch.

Reminders show a custom-styled popup in the bottom-right of your primary monitor and stays on top until you dismiss it. A sound plays alongside.

### Web search

Loki opens search results for various services in your browser. No external API calls — it just constructs the right URL and lets your browser handle the search.

- "open the map of Madison, Wisconsin" → Google Maps
- "show me hotels near times square on maps" → Google Maps
- "search youtube for jazz playlists" → YouTube
- "look up superconductivity on wikipedia" → Wikipedia
- "google the latest qwen3 benchmarks" → Google
- "find images of aurora borealis" → Google Images
- "search github for python pdf libraries" → GitHub
- "find pandas dataframe groupby on stackoverflow" → Stack Overflow
- "search amazon for usb-c hubs" → Amazon

You can also specify a browser: "open the map of Chicago in Firefox" routes the search through Firefox specifically. Without a browser named, your system default is used.

Supported services: google, maps, youtube, wikipedia, amazon, github, stackoverflow, images.

### Working with PDFs

Attach a PDF in the chat window two ways:

- Type `read C:\path\to\paper.pdf`
- Drag and drop a PDF file onto the chat window

Once attached, ask questions about it:

- "Summarize the paper"
- "What method did the authors use?"
- "What's the error rate they report?"

Loki uses local semantic search to find relevant sections, so it works on long documents. Type `detach` to unload the PDF.

**Note:** PDF question-answering is a research aid, not an authoritative source. The local model can occasionally misread specific numbers or miss details, especially in dense technical papers. Always verify exact values against the original document.

### Converting to LaTeX

Paste text or describe an equation and ask Loki to convert it:

- "convert to latex: the integral from 0 to infinity of x squared e to the minus x dx"
- "latex this inline: alpha squared plus beta squared"

The result appears in a code block with a copy button. Note: this works on text you paste or describe, not on equations extracted from PDFs (PDF text extraction garbles math notation).

You can customize the LaTeX style by creating a `latex_preferences.md` file in the project folder with your preferences (e.g. "use \dfrac instead of \frac").

### System information

Ask about your computer's current status:

- "How much disk space do I have?"
- "What's my RAM usage?"
- "Am I running low on memory?"
- "What's my battery at?"
- "How long has my PC been on?"
- "What's my local IP?"

Loki gathers the data locally and answers in plain language. It interprets the numbers too — asking "am I running low on memory?" gets a judgment, not just a percentage.

### Quick calculations

Type a pure arithmetic expression and Loki evaluates it instantly without involving the language model:

- `27 * 4500 * 0.27`
- `sqrt(2) + pi`
- `2^10`

Supported functions include sqrt, sin, cos, tan, log, ln, exp, abs, round, floor, ceil, factorial, and constants pi, e, tau. For word-based math questions ("what's the molar mass of glucose?"), Loki uses the language model instead.

### Symbolic calculus

Loki does exact symbolic math via SymPy:

- `integrate x^2`
- `integrate x*exp(x) from 0 to 1` (definite)
- `differentiate x^2 * sin(x)`
- `diff x^3 at 2` (evaluates the derivative at a point)
- `integrate \frac{1}{1+x^2}` (LaTeX input works too)

Results are exact and come back as LaTeX you can copy into a paper. Unlike the chat model, SymPy's answers are mathematically guaranteed correct. Note that integrals without an elementary closed form return special functions (e.g. erf, Si) — that's correct, not an error.

### Chat window

In tray mode, left-click the tray icon to open the chat window. Features:

- Code blocks have a header bar with a Copy link — click to copy the code to clipboard
- Type and press Enter to send (Send button works too)
- Last 50 messages from disk are restored when you reopen the window
- Close the window with X to hide it (Loki keeps running in tray)
- Conversation history is saved to `chat_history.jsonl` (rolling last ~1000 messages)

---

## Project structure

```text
loki/
├── assistant.py            # Main entry point, command dispatch
├── chat_window.py          # PySide6 chat UI
├── tray.py                 # System tray icon
├── voice.py                # Whisper (STT) and Piper (TTS)
├── db.py                   # SQLite for reminders
├── reminders.py            # Scheduler and custom-styled popup reminders
├── pdf_handler.py          # PDF text extraction and attachment state
├── pdf_rag.py              # Local embeddings and semantic retrieval
├── system_info.py          # Local system statistics (CPU, RAM, disk, etc.)
├── symbolic_math.py        # For performing symbolic math using sympy
├── discover_apps.py        # Scans system for installed apps
├── manual_apps.json        # Manual app overrides (optional)
├── latex_preferences.md    # LaTeX style preferences (optional)
├── apps.json               # Generated app registry (auto-created)
├── assistant.db            # Reminders database (auto-created)
├── chat_history.jsonl      # Conversation log (auto-created)
├── AI_Icon.png             # Tray and window icon
├── voices/                 # Piper voice files (you download these)
├── logs/                   # Daily log files in tray mode (auto-created)
└── requirements.txt
``` 
---

## Customization

### Change the language model

Edit `assistant.py` — find `ROUTER_MODEL` and `CHAT_MODEL`. Both default to `qwen3:8b`. You can use a smaller model on weaker hardware:

- `qwen2.5:7b` — slightly older, smaller, still good
- `llama3.2:3b` — much smaller (~2 GB), runs fine on CPU, less capable at chat

Run `ollama pull <model>` first to download it.

### Change the voice

Download any voice from [huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices), place the `.onnx` and `.onnx.json` files in `voices/`, then update `PIPER_VOICE_PATH` in `voice.py`.

### Change the hotkey

In `assistant.py`, change `VOICE_HOTKEY = "f9"` to whatever you prefer. Examples: `"f12"`, `"pause"`, `"ctrl+shift+space"`.

### Default voice output off

If you'd rather Loki only speak when you ask it to, set `VOICE_OUTPUT = False` near the top of `assistant.py`. Re-enable per session with the "voice on" command.

### Custom icon

Replace `AI_Icon.png` with your own 256×256 PNG (transparent background, high contrast for visibility at 16×16 in the tray).

### Optional configuration

Loki includes example config files. To use them, copy and edit:

```bash
copy manual_apps.json.example manual_apps.json
copy latex_preferences.md.example latex_preferences.md
```

Then edit the copies with your own app paths and LaTeX style. Both files are optional — Loki works without them.

---

## Smaller hardware

If you don't have a GPU or have less than 8 GB VRAM:

- **Use a smaller model**: `ollama pull qwen2.5:3b` and set `ROUTER_MODEL = "qwen2.5:3b"` and `CHAT_MODEL = "qwen2.5:3b"` in `assistant.py`
- **Run Whisper on CPU**: in `voice.py`, set `WHISPER_DEVICE = "cpu"` and `WHISPER_COMPUTE_TYPE = "int8"`
- **Use the smallest Whisper model**: in `voice.py`, set `WHISPER_MODEL_SIZE = "base.en"` (less accurate) or `"tiny.en"` (least accurate, fastest)

Loki will still work on a midrange laptop with no dedicated GPU — just slower.

---

## Troubleshooting

### "Already running, exiting"

A previous instance is still running. Check your system tray for the Loki icon and right-click → Quit. If the tray icon isn't visible, open Task Manager, find `python.exe` or `pythonw.exe`, and end the process. Then delete `%TEMP%\loki.lock` if it persists.

### Reminder popup doesn't appear

The popup needs Loki running in tray mode (in text/voice mode, reminders fall back to printed text in the terminal).

### "Couldn't understand the time"

Loki uses `dateparser` for natural-language times. It handles most phrasings but occasionally trips on unusual ones. Try rephrasing: "in 30 minutes" instead of "in a half hour"; "tomorrow at 5pm" instead of "five tomorrow."

### Voice transcription is inaccurate

Edit `voice.py` and change `WHISPER_MODEL_SIZE = "small.en"` to `"medium.en"`. Larger model, more accurate, slower.

### Apps in `apps.json` don't launch

The discovery script can't perfectly detect every installer's quirks. Add the app to `manual_apps.json` with the correct path:

```json
{
  "appname": {
    "type": "exe",
    "path": "C:\\Full\\Path\\To\\app.exe",
    "args": []
  }
}
```

Some apps need to launch with admin rights (RustDesk's GUI, for instance). Add `"elevated": true` to the entry.

### LLM responses are very slow

First call after Loki sits idle is slow because the model has to load into VRAM. Subsequent calls are fast. Set `OLLAMA_KEEP_ALIVE=2h` in your environment to keep it loaded longer.

If responses are consistently slow even after warmup, the model is probably running on CPU because VRAM is full. Close other GPU-heavy applications (browsers with hardware acceleration count), or run `ollama ps` to see the model's status.

---

## Privacy

Everything runs locally:

- The language model runs through Ollama on your machine
- Speech recognition uses Whisper locally
- Text-to-speech uses Piper locally
- Reminders are stored in a local SQLite file
- Conversation history is in a local file

The only network connections Loki makes are:
- To `localhost:11434` (Ollama) — never leaves your machine
- The initial download of the model and Whisper model files from their respective servers (one-time)

If you ever add the optional calendar integration (not included by default), that would change — Google Calendar or Outlook events would go through their servers. Currently no such integration exists in this codebase.

---

## License

Add your preferred license here (MIT is a reasonable default for personal projects).

---

## Acknowledgments

Loki is built on top of excellent open-source projects:

- [Ollama](https://ollama.com) for local LLM hosting
- [Qwen](https://github.com/QwenLM/Qwen3) for the language model
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for speech recognition
- [Piper](https://github.com/rhasspy/piper) for text-to-speech
- [PySide6](https://wiki.qt.io/Qt_for_Python) for the GUI