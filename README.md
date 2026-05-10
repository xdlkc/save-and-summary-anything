# save-and-summary-anything

A Hermes Agent skill for saving article-like web pages as local Markdown, downloading inline images/GIFs, and then summarizing the saved article in chat.

This skill is designed for “drop me a URL” workflows: when a user sends an ordinary web article URL, Hermes can open it in a real Chrome session, extract the article body, save a Markdown archive with local media, and reply with a Chinese summary.

## What it does

- Detects whether a URL is a text-heavy article page before saving.
- Opens pages with real Google Chrome + Playwright CDP instead of a bare browser context.
- Saves article metadata and body as Markdown.
- Downloads images into a local `images/` folder and rewrites Markdown links to local files.
- Preserves Zhihu animated images by converting `ztext-gif` thumbnail URLs like `..._b.jpg` to real `..._b.gif` downloads when available.
- Defaults to an isolated Chrome profile so normal Chrome can stay open while scraping.
- Keeps summaries in chat by default; it does **not** create `_summary.md` unless a user explicitly asks for a summary file.

## Repository layout

```text
.
├── SKILL.md                 # Hermes skill instructions
├── scripts/
│   └── save_article.py      # Article detection/extraction/downloader
└── agents/
    └── openai.yaml          # Optional agent config
```

## Requirements

- macOS or another system with Google Chrome installed. The defaults are macOS-oriented.
- Python 3.8+
- Playwright:

```bash
pip install playwright -i https://pypi.org/simple/
python3 -m playwright install chrome
```

## Usage

```bash
python3 scripts/save_article.py "https://example.com/article"
```

Or specify an output directory:

```bash
python3 scripts/save_article.py "https://example.com/article" "/path/to/output"
```

On success the script prints machine-readable signals:

```text
DONE
ARTICLE_SAVED=/absolute/path/to/article.md
ARTICLE_DIR=/absolute/path/to/article-dir
IMAGE_COUNT=10
METADATA=/absolute/path/to/metadata.json
```

If the page does not look like an article, it prints:

```text
NOT_ARTICLE=<reason>
```

## Output structure

```text
<output-root>/<site>/<article-title>/
├── <article-title>.md
├── metadata.json
└── images/
    ├── img_xxxxxx.jpg
    └── gif_xxxxxx.gif
```

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `ANY_ARTICLE_OUTPUT` | Article archive root | `$OBSIDIAN_VAULT_PATH/网页文章`, or `/Users/lkc/Library/Mobile Documents/iCloud~md~obsidian/Documents/lkc/网页文章` if `OBSIDIAN_VAULT_PATH` is unset |
| `CHROME_PROFILE_MODE` | `dedicated` uses an isolated Hermes Chrome profile; `default` reuses the normal Chrome profile/cookies | `dedicated` |
| `CHROME_USER_DATA_DIR` | Chrome user-data directory | `~/.hermes/chrome-profiles/save-and-summary` in dedicated mode; `~/Library/Application Support/Google/Chrome` in default mode |
| `CHROME_PROFILE_DIRECTORY` | Chrome profile directory | `Default` in dedicated mode; `auto` in default mode |
| `CHROME_REMOTE_DEBUGGING_PORT` | CDP port | `9223` in dedicated mode; `9222` in default mode |
| `CHROME_EXECUTABLE` | Google Chrome executable | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |

## Chrome profile modes

### Dedicated mode, default

Dedicated mode uses:

```text
~/.hermes/chrome-profiles/save-and-summary
```

This avoids Chrome profile locking and lets the scraper run while your normal Chrome is open.

If a site requires login, log in once in the scraper Chrome window. Cookies remain in the dedicated profile and are reused later.

### Default mode

Use default mode only when you must reuse your normal Chrome cookies:

```bash
CHROME_PROFILE_MODE=default python3 scripts/save_article.py "https://example.com/article"
```

In this mode, Chrome must usually be fully quit first unless it was already started with the requested remote debugging port.

## Installing as a Hermes skill

Clone this repository into your Hermes skills directory:

```bash
mkdir -p ~/.hermes/skills
cd ~/.hermes/skills
git clone https://github.com/xdlkc/save-and-summary-anything.git
```

Then use it by sending Hermes a normal article URL. For WeChat public-account articles or X/Twitter links, prefer specialized skills if installed.

## Notes

- The extraction heuristic is conservative: it requires enough text, enough paragraphs, and acceptable link density.
- The script intentionally avoids `launch_persistent_context()` for normal Chrome profiles because modern Chrome profile protections can produce empty/automation profiles with missing cookies.
- For sites with CAPTCHA or verification, complete the check manually in the visible Chrome window. Do not bypass security checks.

## License

MIT
