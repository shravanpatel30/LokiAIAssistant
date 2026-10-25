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
- Understand images — paste a screenshot, a plot or a photo of handwritten work and ask about it
- Do exact symbolic calculus (integrals and derivatives) with SymPy
- Search the web — Google Maps, YouTube, Wikipedia, Google, Amazon, GitHub, and more — by voice or text
- A chat window with rendered markdown, properly typeset math (KaTeX), code blocks with copy buttons, and drag-and-drop PDF and image support, and dropdowns to pick which local models to use

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
- `qwen3-vl:8b-instruct` — the vision model, used only when you attach an image
- `nomic-embed-text` — embedding model for PDF semantic search

All run locally through Ollama. Pull them during setup. You can switch models any time from the dropdowns in the chat window.

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/shravanpatel30/LokiAIAssistant.git
cd LokiAIAssistant
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

This installs ~15 packages and takes a few minutes. PySide6 is the largest — it includes the embedded browser engine the chat window uses to render markdown and math.

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

#### Pull the vision model (for images)

```bash
ollama pull qwen3-vl:8b-instruct
```

About 6 GB. Use the **instruct** tag: the plain `qwen3-vl:8b` is a thinking model that often runs out of room before it finishes answering. Without a vision model everything else still works; Loki just tells you it can't read images.

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

### 6. Chat window libraries (already included)

The chat window renders markdown with [marked](https://marked.js.org) and math with [KaTeX](https://katex.org). Both are bundled in `web/vendor/`, so the window works fully offline and there's nothing to do here.

If `web/vendor/` is ever missing (the chat window will tell you), re-download them once with:

```bash
python setup_web_assets.py
```

### 7. Set environment variables (recommended)

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

Hold **F9** to speak, release to send. Replies print in the terminal; say "voice on" to also have them spoken aloud through your default audio device. Say "exit" or press `Ctrl+C` to quit.

### Tray mode (recommended for daily use)

```bash
python assistant.py --tray
```

Loki runs silently in your system tray. Left-click the tray icon to open the chat window. F9 still works for push-to-talk from anywhere. Right-click the tray icon and choose Quit (or type `exit` in the chat window) to exit cleanly.

The voice models load in the background at startup, so the window is usable right away — the very first F9 press may take a few extra seconds if they haven't finished loading.

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
| `clear fired`, `clean up` | Remove one-off reminders that have already fired |
| `think on`, `thinking on` | Turn on thinking mode (see below) |
| `think off`, `thinking off` | Turn off thinking mode |

### Thinking mode

Click **Think** next to the Send button (or say/type `think on`) and Loki reasons step by step before answering. It's much better at logic puzzles, multi-step math word problems, probability and "why" questions — but slower: expect roughly 10–45 seconds per reply instead of a few seconds. The status line says "Loki is reasoning…" while it works.

- **Sticky:** it stays on until you click the button again (or say `think off`). It resets to off when Loki restarts.
- **Only for chat and PDF questions** (and image questions, if the image model supports thinking — `qwen3-vl:8b-instruct` doesn't). Reminders, app launching, web search, system info, calculus, LaTeX conversion and the command router always run without thinking, so they stay fast.
- **Greyed out?** The model picked in the **Model** dropdown doesn't support thinking. `qwen3:8b` does.
- **See how it got there:** each answer has a collapsed **Show reasoning** section above it. Click to expand. The reasoning is saved with the chat history but never fed back to the model.

### Reminders

Reminders persist in `assistant.db` (a SQLite file in the project folder). They survive restarts. If Loki was off when a reminder was due, it fires — popup included — when you next launch.

Reminders show a custom-styled popup in the bottom-right of your primary monitor that stays on top until you dismiss it. A sound plays alongside.

Recurring reminders follow the calendar: monthly ones land on the same day each month (the 31st becomes the last day in shorter months), and a yearly Feb 29 reminder fires on Feb 28 in non-leap years.

Spoken times are cleaned up automatically — Whisper often writes "nine twenty-seven p.m." as `9 27 pm` or `9.27 pm`, and Loki reads those as 9:27 PM.

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

- Type `read C:\path\to\paper.pdf` (a path from Explorer's "Copy as path", quotes and all, works too)
- Drag and drop a PDF file onto the chat window

The status line under the input box shows which PDF is attached.

Once attached, ask questions about it:

- "Summarize the paper"
- "What method did the authors use?"
- "What's the error rate they report?"

Loki uses local semantic search to find relevant sections, so it works on long documents. Type `detach` to unload the PDF.

**Note:** PDF question-answering is a research aid, not an authoritative source. The local model can occasionally misread specific numbers or miss details, especially in dense technical papers. Always verify exact values against the original document.

Scanned PDFs (images of pages, with no text layer) aren't supported — Loki will tell you if a PDF has no extractable text. Screenshot the page and attach it as an image instead.

### Working with images

Attach an image (PNG, JPG, WebP, BMP or GIF) three ways:

- **Paste:** copy a screenshot (`Win+Shift+S`) or an image file and press `Ctrl+V` in the input box
- **Drag and drop** an image onto the chat window
- **Type a path:** `C:\Users\you\Pictures\circuit.png what is the current I?` (Explorer's "Copy as path", quotes and all, works too)

Pasted and dropped images show as chips above the input box. Click ✕ on a chip to remove it, add more if you like, type your question, and press Enter. Your images appear as thumbnails in the chat; click one to open it in your image viewer.

**Sending an image with no question types out everything written in it**, with equations in LaTeX — handy for a screenshot from a paper or a page of handwritten notes. Equations render in the chat, and copying them (`Ctrl+C`) gives you the LaTeX source.

Good uses:

- "Convert this equation to LaTeX" (a screenshot from a paper)
- "Type out these notes as markdown"
- "Turn this table into markdown"
- "What's the peak value in this plot?"

The image and your question go straight to the model in the **Images** dropdown (`qwen3-vl:8b-instruct` by default). If the model in the **Model** dropdown can see images itself (marked 👁), it's used instead. Follow-ups work on the text of the answer — "now simplify that" or "write it in Python" — without re-attaching the image.

8B vision models are very good at transcribing text and equations but unreliable at reasoning about diagrams: a hand-drawn circuit, for example, is often read with the wrong connections. Use them to type things out, and do the reasoning yourself (or with the main model on the transcribed text). Bigger vision models handle diagrams much better if you upgrade your GPU — pull one and pick it from the dropdown.

The vision model and `qwen3:8b` don't fit in 8 GB of VRAM together, so the vision model unloads as soon as it's done. Each image takes a few extra seconds while it loads (the status line says "Loki is looking…").

**Note:** like PDF answers, image answers are a research aid. Double-check numbers read off plots — small vision models estimate them, and can be off by a gridline.

### Converting to LaTeX

Paste text or describe an equation and ask Loki to convert it:

- "convert to latex: the integral from 0 to infinity of x squared e to the minus x dx"
- "latex this inline: alpha squared plus beta squared"

The result appears as a rendered equation, with the LaTeX source underneath in a code block with a Copy button. Note: this works on text you paste or describe, not on equations extracted from PDFs (PDF text extraction garbles math notation).

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
- `integrate x squared plus sin x` (simple expressions in words are translated by the LLM first)

Results show as a rendered equation — e.g. ∫ x² dx = x³/3 + C — with the LaTeX source in a code block underneath, ready to copy into a paper. Unlike the chat model, SymPy's answers are mathematically guaranteed correct. Note that integrals without an elementary closed form return special functions (e.g. erf, Si) — that's correct, not an error.

A bare `e` means Euler's number (`integrate e^x`, `integrate 1/x from 1 to e`), and `pi` means π.

Exact answers that aren't plain fractions also show a decimal value, e.g. `integrate 1/(1+x^2) from 0 to 1` gives π/4 ≈ 0.785398163397. Some functions (like the Gamma function) have no closed-form antiderivative at all — for those, a definite integral is computed numerically (`integrate gamma(x) from 1 to e` ≈ 1.80936766704), and an indefinite one tells you to add bounds.

### Chat window

In tray mode, left-click the tray icon to open the chat window. Features:

- Replies render markdown — headings, lists, tables, bold, links
- Math renders properly with KaTeX, both inline (`$...$`, `\(...\)`) and on its own line (`$$...$$`, `\[...\]`)
- Code blocks have a header bar with a Copy button — click to copy the code to clipboard
- Links in replies open in your normal browser
- Select text and right-click → Copy (or `Ctrl+C`) to copy any part of the conversation
- Type and press Enter to send (Send button works too)
- Drag a PDF onto the window to attach it; paste (`Ctrl+V`) or drag an image to ask about it
- **Model** and **Images** dropdowns at the top pick which installed Ollama models Loki uses (see below)
- Last 50 messages from disk are restored (with math rendered) when you reopen the window
- Close the window with X to hide it (Loki keeps running in tray)
- Conversation history is saved to `chat_history.jsonl` (rolling last ~1000 messages)

The conversation area is a small local web page (`web/chat.html`) shown inside the window. Nothing is loaded from the internet.

### Choosing models

The two dropdowns at the top of the chat window list every model you've pulled with Ollama (the list refreshes each time you open it, so a model you just pulled shows up right away):

- **Model** — the main model. It routes your commands and answers chat, PDF and LaTeX questions.
- **Images** — the model that reads attached images. Only models that can see images (marked 👁) are accepted here.

Your choice is saved in `settings.json` and kept across restarts. To try a new model, run `ollama pull <model>` and pick it from the dropdown — no code changes needed.

---

## Project structure

```text
LokiAIAssistant/
├── assistant.py            # Main entry point: routing, all features, the three run modes
├── chat_window.py          # PySide6 chat window, reminder popup
├── tray.py                 # System tray icon
├── voice.py                # Whisper (STT) and Piper (TTS)
├── db.py                   # SQLite for reminders
├── reminders.py            # Reminder scheduler (fires the popup via assistant.py)
├── pdf_handler.py          # PDF text extraction and attachment state
├── pdf_rag.py              # Local embeddings and semantic retrieval
├── system_info.py          # Local system statistics (CPU, RAM, disk, etc.)
├── symbolic_math.py        # Symbolic calculus with SymPy
├── models.py               # Which models to use (settings.json) and what each one can do
├── discover_apps.py        # Scans system for installed apps
├── setup_web_assets.py     # Re-downloads KaTeX, marked and fonts into web/vendor/ (rarely needed)
├── web/
│   ├── chat.html           # The chat page shown inside the window
│   ├── chat.css            # Chat colors, fonts, spacing (edit to restyle)
│   ├── chat.js             # Markdown + math rendering, copy buttons
│   └── vendor/             # Bundled KaTeX, marked and Google Sans fonts (offline)
├── manual_apps.json        # Manual app overrides (optional)
├── latex_preferences.md    # LaTeX style preferences (optional)
├── apps.json               # Generated app registry (auto-created)
├── assistant.db            # Reminders database (auto-created)
├── chat_history.jsonl      # Conversation log (auto-created)
├── settings.json           # Your model choices (auto-created)
├── chat_images/            # Copies of images you've attached (auto-created)
├── AI_Icon.png             # Tray and window icon
├── voices/                 # Piper voice files (you download these)
├── logs/                   # Daily log files in tray mode (auto-created)
├── .gitattributes          # Line-ending rules (keeps web/vendor/ byte-exact)
└── requirements.txt
```

### How a message flows

Every message — typed in the window, typed in the terminal, or spoken — goes through the same steps in `assistant.py`:

1. **Utility commands** (`exit`, `reset`, `voice on`...) are handled directly
2. **Attached images** go straight to the vision model (see *Working with images*)
3. **Pure arithmetic** goes to the local calculator
4. **Calculus commands** (`integrate ...`, `diff ...`) go to SymPy
5. **Everything else** goes to the LLM router, which picks an intent (open app, reminder, PDF, web search, chat...) and the matching handler does the work

To add a feature, write a handler function and add one line to `INTENT_HANDLERS` (plus a rule in the router prompt).

---

## Customization

### Change the language model

Pick it from the **Model** dropdown in the chat window (see *Choosing models*). The defaults are `qwen3:8b` and, for images, `qwen3-vl:8b-instruct`. You can use a smaller model on weaker hardware:

- `qwen2.5:7b` — slightly older, smaller, still good
- `llama3.2:3b` — much smaller (~2 GB), runs fine on CPU, less capable at chat (no thinking mode)

Run `ollama pull <model>` first to download it. The choice is stored in `settings.json`; delete that file to go back to the defaults.

### Change the voice

Download any voice from [huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices), place the `.onnx` and `.onnx.json` files in `voices/`, then update `PIPER_VOICE_PATH` in `voice.py`.

### Change the hotkey

In `assistant.py`, change `VOICE_HOTKEY = "f9"` to whatever you prefer. Examples: `"f12"`, `"pause"`, `"ctrl+shift+space"`.

### Spoken replies on by default

Voice output is off by default — replies are text only. To have Loki speak by default, set `VOICE_OUTPUT = True` near the top of `assistant.py`. You can also toggle it any time with the "voice on" / "voice off" commands or from the tray menu.

### Change the chat window's look

All colors, fonts and spacing are variables at the top of `web/chat.css` — for example `--bg` (background), `--label-user` (the "you:" color), `--code-radius` (set to `8px` for rounded code blocks), and `--font-size`. Edit, save, and restart Loki. No Python changes needed.

The chat uses Google Sans for text and Google Sans Code for code (bundled in `web/vendor/fonts/`, so they work offline). To use a different font, put any installed font's name first in `--font` or `--font-mono`, e.g. `"Inter"` or `"Segoe UI"`. Math always uses KaTeX's own fonts.

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

- **Use a smaller model**: `ollama pull qwen2.5:3b` and pick it from the **Model** dropdown
- **Use a smaller vision model**: e.g. `ollama pull qwen2.5vl:3b` and pick it from the **Images** dropdown
- **Run Whisper on CPU**: in `voice.py`, set `WHISPER_DEVICE = "cpu"` and `WHISPER_COMPUTE_TYPE = "int8"`
- **Use the smallest Whisper model**: in `voice.py`, set `WHISPER_MODEL_SIZE = "base.en"` (less accurate) or `"tiny.en"` (least accurate, fastest)

Loki will still work on a midrange laptop with no dedicated GPU — just slower.

---

## Troubleshooting

### "Already running (PID ...). Exiting."

A previous instance is still running. Check your system tray for the Loki icon and right-click → Quit. If the tray icon isn't visible, open Task Manager → Details, find `python.exe` or `pythonw.exe` with that PID, and end it.

Loki ignores leftover lock files from a crash automatically (it checks that the PID really is a Python process). If it still refuses to start, delete `%TEMP%\loki_assistant.lock`.

### Reminder popup doesn't appear

The popup needs Loki running in tray mode (in text/voice mode, reminders fall back to printed text in the terminal).

### Chat shows plain text instead of formatted replies and math

The bundled libraries in `web/vendor/` are missing — the chat window shows a notice when this happens. Run `python setup_web_assets.py` once and restart Loki.

### "Embedding failed ... Run: ollama pull nomic-embed-text"

The PDF search model isn't installed. Run `ollama pull nomic-embed-text`, then attach the PDF again.

### "Something went wrong: ..." in the chat

Usually Ollama isn't running. Start it (open the Ollama app, or run `ollama serve`) and try again. The full error is in today's file in `logs/`.

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

### Thinking-mode answers get cut off or seem to forget the PDF

Reasoning takes up room in the model's context window. If answers stop mid-way with thinking mode on — especially for PDF questions — raise Ollama's context length: set the environment variable `OLLAMA_CONTEXT_LENGTH` to `8192` (or `16384` if you have VRAM to spare) and restart Ollama. `ollama ps` shows the current context size.

### "... can't see images — pick a model marked 👁"

You picked a text-only model in the **Images** dropdown. Choose one marked 👁, or pull one: `ollama pull qwen3-vl:8b-instruct`. If no models show 👁 at all, Ollama may not be running.

### An answer about an image is wrong

Small vision models misread some images, especially diagrams. Try a sharper or tighter-cropped image, or correct the misreading in a follow-up message. For transcriptions, compare against the image — a wrong subscript or sign is the most common slip.

### Image answers stop mid-way

Images take about 1,000 tokens of the model's context window. Raise `OLLAMA_CONTEXT_LENGTH` to `8192` as described above, and make sure the **Images** model is an `-instruct` one, not a thinking one.

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
- Images you attach are read by the local vision model; copies are kept in `chat_images/` so the chat history can show them. Delete that folder any time.

The only network connections Loki makes are:
- To `127.0.0.1:11434` (Ollama, on your own machine) — never leaves your machine
- The initial download of the model and Whisper model files from their respective servers (one-time)

Two things involve the internet only when you ask for them:
- **Web search** opens a search page in your browser — the same as typing the search yourself. Loki doesn't call any search API.
- **`setup_web_assets.py`** downloads KaTeX and marked from the npm registry — only if you run it. Both are already bundled, so normally you never will.

The chat window loads nothing from the internet; markdown and math are rendered by the bundled libraries.

If you ever add the optional calendar integration (not included by default), that would change — Google Calendar or Outlook events would go through their servers. Currently no such integration exists in this codebase.

---

## License

Add your preferred license here (MIT is a reasonable default for personal projects).

---

## Acknowledgments

Loki is built on top of excellent open-source projects:

- [Ollama](https://ollama.com) for local LLM hosting
- [Qwen](https://github.com/QwenLM/Qwen3) for the language model, and [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL) for the vision model
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for speech recognition
- [Piper](https://github.com/rhasspy/piper) for text-to-speech
- [PySide6](https://wiki.qt.io/Qt_for_Python) for the GUI
- [KaTeX](https://katex.org) for math rendering
- [Google Sans and Google Sans Code](https://fonts.google.com) (SIL Open Font License, via [Fontsource](https://fontsource.org)) for the chat window's text and code
- [marked](https://marked.js.org) for markdown rendering
- [SymPy](https://www.sympy.org) for symbolic calculus
- [pdfplumber](https://github.com/jsvine/pdfplumber) for PDF text extraction