#!/usr/bin/env python3
"""
Save a text-heavy article-like web page as local Markdown with images.

Usage:
  python3 save_article.py <url> [output_dir]

Signals:
  ARTICLE_SAVED=<absolute md path>
  NOT_ARTICLE=<reason>
"""

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else None
# Output precedence:
#   1. CLI argument: save_article.py <url> <output_dir>
#   2. ANY_ARTICLE_OUTPUT
#   3. OBSIDIAN_VAULT_PATH/网页文章
#   4. ~/saved-articles
#
# Do not hardcode a personal vault path in the public skill. Users who want an
# Obsidian/iCloud/Dropbox archive should set OBSIDIAN_VAULT_PATH or
# ANY_ARTICLE_OUTPUT in their shell/Hermes environment.
_obsidian_vault = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
DEFAULT_ARTICLE_OUTPUT = (
    str(Path(_obsidian_vault).expanduser() / "网页文章")
    if _obsidian_vault
    else os.path.expanduser("~/saved-articles")
)
OUTPUT_DIR = sys.argv[2] if len(sys.argv) > 2 else os.environ.get(
    "ANY_ARTICLE_OUTPUT", DEFAULT_ARTICLE_OUTPUT
)
# Profile modes:
# - dedicated (default): use an isolated Hermes-owned Chrome user-data-dir so the
#   scraper can run even while the user's normal Chrome is open. Login once in
#   the popped-up scraper Chrome window if a site needs cookies.
# - default: use the user's real Chrome profile/cookies. This preserves existing
#   login state but requires Chrome to be quit first unless it was already started
#   with remote debugging.
CHROME_PROFILE_MODE = os.environ.get("CHROME_PROFILE_MODE", "dedicated").strip().lower()
_DEDICATED_CHROME_USER_DATA_DIR = os.path.expanduser(
    "~/.hermes/chrome-profiles/save-and-summary"
)
CHROME_USER_DATA_DIR = os.environ.get(
    "CHROME_USER_DATA_DIR",
    _DEDICATED_CHROME_USER_DATA_DIR
    if CHROME_PROFILE_MODE == "dedicated"
    else os.path.expanduser("~/Library/Application Support/Google/Chrome"),
)
# "auto" means: read Chrome Local State profile.last_used. For the dedicated
# scraper profile, use the normal "Default" subprofile and let Chrome create it.
CHROME_PROFILE_DIRECTORY = os.environ.get(
    "CHROME_PROFILE_DIRECTORY", "Default" if CHROME_PROFILE_MODE == "dedicated" else "auto"
)
CHROME_REMOTE_DEBUGGING_PORT = int(
    os.environ.get("CHROME_REMOTE_DEBUGGING_PORT", "9223" if CHROME_PROFILE_MODE == "dedicated" else "9222")
)
CHROME_EXECUTABLE = os.environ.get(
    "CHROME_EXECUTABLE",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def sanitize_filename(name: str, fallback: str = "article", max_len: int = 90) -> str:
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", (name or "")).strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = fallback
    return name[:max_len].strip(" ._") or fallback


def normalize_space(s: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", s or "").strip()


def abs_url(src: str, base: str) -> str:
    if not src:
        return ""
    return urllib.parse.urljoin(base, src)


def guess_ext(url: str, content_type: str = "") -> str:
    # WeChat image URLs often encode the true format in the query string, e.g.
    # ...?wx_fmt=png, while the path itself has no extension. Prefer that when
    # present so local files keep the correct type.
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    wx_fmt = (q.get("wx_fmt") or [""])[0].lower()
    if wx_fmt in ("jpg", "jpeg", "png", "gif", "webp"):
        return ".jpg" if wx_fmt == "jpeg" else f".{wx_fmt}"
    ct = (content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "gif" in ct:
        return ".gif"
    if "webp" in ct:
        return ".webp"
    if "svg" in ct:
        return ".svg"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    path = urllib.parse.urlparse(url).path.lower()
    for ext in (".png", ".gif", ".webp", ".svg", ".jpg", ".jpeg"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def download_image(url: str, save_dir: Path, referer: str) -> Optional[str]:
    try:
        if not url.startswith(("http://", "https://")):
            return None
        h = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            ext = guess_ext(url, resp.headers.get("Content-Type", ""))
        filename = f"img_{h}{ext}"
        path = save_dir / filename
        if not path.exists():
            path.write_bytes(data)
        return filename
    except Exception as e:
        print(f"[warn] image download failed: {url[:120]} -> {e}")
        return None


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


def chrome_is_running() -> bool:
    try:
        r = subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True, text=True)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def is_wechat_url(url: str) -> bool:
    try:
        return urllib.parse.urlparse(url).hostname == "mp.weixin.qq.com"
    except Exception:
        return False


WECHAT_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
window.chrome = window.chrome || { runtime: {} };
"""


def detect_profile_directory(user_data_dir: Path, requested: str) -> str:
    if requested and requested != "auto":
        return requested
    local_state = user_data_dir / "Local State"
    try:
        data = json.loads(local_state.read_text(encoding="utf-8", errors="ignore"))
        prof = data.get("profile", {})
        # last_used is the best match for the Chrome profile the user normally sees.
        last_used = prof.get("last_used")
        if last_used:
            return last_used
        active = prof.get("last_active_profiles") or []
        if active:
            return active[0]
    except Exception as e:
        print(f"[warn] Could not read Chrome Local State for profile detection: {e}")
    return "Default"


def launch_or_connect_chrome(
    p,
    user_data_dir: Path,
    profile_dir: str,
    port: int,
    allow_parallel_chrome: bool = False,
    create_profile_if_missing: bool = False,
):
    """Connect to real Google Chrome via CDP.

    In dedicated mode we use an isolated Hermes-owned Chrome profile, so it is
    safe to launch while the user's normal Chrome is already running. In default
    mode we use the user's real Chrome profile/cookies; that profile cannot be
    safely re-opened with remote debugging while normal Chrome is already using
    it, so the user must quit Chrome first.
    """
    started_proc = None
    if not port_open(port):
        if chrome_is_running() and not allow_parallel_chrome:
            raise RuntimeError(
                "Google Chrome is already running but remote debugging is not enabled. "
                "Quit all Chrome windows (Cmd+Q) and rerun, or start Chrome yourself with "
                f"--remote-debugging-port={port}. To avoid this requirement, run with "
                "CHROME_PROFILE_MODE=dedicated."
            )
        if not Path(CHROME_EXECUTABLE).exists():
            raise RuntimeError(f"Chrome executable not found: {CHROME_EXECUTABLE}")
        if create_profile_if_missing:
            (user_data_dir / profile_dir).mkdir(parents=True, exist_ok=True)
        elif not (user_data_dir / profile_dir).exists():
            raise RuntimeError(f"Chrome profile directory not found: {user_data_dir / profile_dir}")
        cmd = [
            CHROME_EXECUTABLE,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "about:blank",
        ]
        started_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            if port_open(port):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Timed out waiting for Chrome remote debugging port to open")
    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, context, started_proc


EXTRACT_JS = r"""
() => {
  const BLOCK_TAGS = new Set(['P','DIV','SECTION','ARTICLE','MAIN','HEADER','FOOTER','LI','UL','OL','BLOCKQUOTE','PRE','H1','H2','H3','H4','H5','H6','FIGURE']);
  const BAD_RE = /(comment|comments|footer|header|nav|menu|sidebar|related|recommend|share|social|advert|ads|promo|cookie|modal|subscribe|newsletter|breadcrumb|pagination|toolbar)/i;
  const GOOD_RE = /(article|content|entry|post|story|main|body|text|richtext|markdown|prose|news|detail)/i;

  function visible(el) {
    if (!el || !el.isConnected) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  function textOf(el) { return (el.innerText || el.textContent || '').replace(/[ \t\r\f\v]+/g, ' ').trim(); }
  function linkDensity(el, text) {
    const linkText = Array.from(el.querySelectorAll('a')).map(a => textOf(a)).join(' ');
    return text ? Math.min(1, linkText.length / text.length) : 1;
  }
  function paragraphCount(el) {
    return Array.from(el.querySelectorAll('p, li')).filter(p => textOf(p).length >= 30).length;
  }
  function score(el) {
    if (!visible(el)) return -1e9;
    const idc = `${el.id || ''} ${el.className || ''}`;
    if (BAD_RE.test(idc)) return -1e9;
    const text = textOf(el);
    const len = text.length;
    if (len < 200) return -1e6 + len;
    const pcount = paragraphCount(el);
    const ld = linkDensity(el, text);
    let s = len + pcount * 180 - ld * len * 0.9;
    if (GOOD_RE.test(idc)) s += 600;
    if (el.tagName === 'ARTICLE' || el.tagName === 'MAIN') s += 800;
    return s;
  }
  const isWechat = location.hostname === 'mp.weixin.qq.com';
  const preferredSelector = isWechat
    ? '#js_content, article, main, [role="main"], .article, .post, .entry-content, .post-content, .article-content, .content, #content, .markdown-body, .prose'
    : 'article, main, [role="main"], .article, .post, .entry-content, .post-content, .article-content, .content, #content, .markdown-body, .prose';
  const preferred = Array.from(document.querySelectorAll(preferredSelector));
  const broad = Array.from(document.body.querySelectorAll('article, main, section, div'));
  let candidates = [...new Set([...preferred, ...broad])].filter(visible);
  candidates.sort((a,b) => score(b) - score(a));
  const best = candidates[0] || document.body;
  const text = textOf(best);
  const meta = (sel, attr='content') => {
    const n = document.querySelector(sel);
    return n ? ((attr === 'text') ? textOf(n) : (n.getAttribute(attr) || '')) : '';
  };
  const title = meta('meta[property="og:title"]') || meta('meta[name="twitter:title"]') || textOf(document.querySelector('#activity-name, .rich_media_title')) || textOf(document.querySelector('h1')) || document.title || '';
  const site = meta('meta[property="og:site_name"]') || (isWechat ? '微信公众平台' : location.hostname);
  const author = meta('meta[name="author"]') || meta('meta[property="article:author"]') || textOf(document.querySelector('#js_name, .account_nickname_inner')) || meta('[rel="author"]', 'text') || '';
  const date = meta('meta[property="article:published_time"]') || meta('meta[name="date"]') || textOf(document.querySelector('#publish_time, em#post-date')) || meta('time[datetime]', 'datetime') || meta('time', 'text') || '';
  const description = meta('meta[name="description"]') || meta('meta[property="og:description"]') || '';
  const pcount = paragraphCount(best);
  const ld = linkDensity(best, text);

  function isProbablyBoilerplate(el) {
    if (el.nodeType !== 1) return false;
    const idc = `${el.id || ''} ${el.className || ''}`;
    return BAD_RE.test(idc) || ['SCRIPT','STYLE','NOSCRIPT','IFRAME','NAV','ASIDE','FORM','BUTTON','SVG'].includes(el.tagName);
  }
  function imgSrc(el) {
    const attrs = ['data-src','data-original','data-lazy-src','data-url','src'];
    for (const a of attrs) {
      let v = el.getAttribute(a);
      if (v && !v.startsWith('data:')) {
        // Zhihu renders animated GIFs as <img class="ztext-gif" src="..._b.jpg">
        // where the real animation is usually available at the same URL with
        // _b.gif. Prefer the animated asset so Markdown keeps the GIF.
        if ((el.className || '').toString().includes('ztext-gif') && /_b\.jpg(\?.*)?$/.test(v)) {
          v = v.replace(/_b\.jpg(\?.*)?$/, '_b.gif$1');
        }
        return v;
      }
    }
    const srcset = el.getAttribute('srcset') || el.getAttribute('data-srcset') || '';
    if (srcset) {
      const parts = srcset.split(',').map(x => x.trim().split(/\s+/)[0]).filter(Boolean);
      if (parts.length) return parts[parts.length - 1];
    }
    return '';
  }
  function pushText(parts, type, value, extra={}) {
    value = (value || '').replace(/[ \t\r\f\v]+/g, ' ').trim();
    if (!value) return;
    const last = parts[parts.length - 1];
    if (last && last.type === type && last.value === value) return;
    parts.push({type, value, ...extra});
  }
  function walk(node, parts) {
    if (node.nodeType === Node.TEXT_NODE) {
      const t = node.textContent.replace(/[ \t\r\f\v]+/g, ' ').trim();
      if (t.length >= 2) pushText(parts, 'text', t);
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node;
    if (!visible(el) || isProbablyBoilerplate(el)) return;
    const tag = el.tagName;
    if (/^H[1-6]$/.test(tag)) { pushText(parts, 'heading', textOf(el), {level: Number(tag[1])}); return; }
    if (tag === 'IMG') {
      const src = imgSrc(el);
      if (src) parts.push({type: 'img', value: src, alt: el.getAttribute('alt') || ''});
      return;
    }
    if (tag === 'A') {
      const href = el.href || el.getAttribute('href') || '';
      const t = textOf(el);
      if (href && /^https?:/.test(href) && t && t.length <= 160) { parts.push({type:'link', value:t, href}); return; }
    }
    if (tag === 'PRE' || tag === 'CODE') { pushText(parts, 'code', el.innerText || el.textContent || ''); return; }
    if (tag === 'BLOCKQUOTE') { pushText(parts, 'quote', textOf(el)); return; }
    if (tag === 'P' || tag === 'LI') { pushText(parts, tag === 'LI' ? 'li' : 'paragraph', textOf(el)); return; }
    if (tag === 'BR') { parts.push({type:'break'}); return; }
    for (const child of el.childNodes) walk(child, parts);
  }
  const parts = [];
  walk(best, parts);
  return {
    url: location.href,
    title, site, author, date, description,
    textLength: text.length,
    paragraphCount: pcount,
    linkDensity: ld,
    score: score(best),
    containerTag: best.tagName,
    containerId: best.id || '',
    containerClass: String(best.className || '').slice(0, 200),
    parts
  };
}
"""


def is_article(data: dict) -> Tuple[bool, str]:
    text_len = int(data.get("textLength") or 0)
    pcount = int(data.get("paragraphCount") or 0)
    ld = float(data.get("linkDensity") or 1)
    if text_len >= 1200 and pcount >= 3 and ld <= 0.45:
        return True, "text>=1200 paragraphs>=3 link_density<=0.45"
    if text_len >= 800 and pcount >= 5 and ld <= 0.35:
        return True, "text>=800 paragraphs>=5 link_density<=0.35"
    if text_len >= 1800 and ld <= 0.55:
        return True, "long_text link_density<=0.55"
    return False, f"textLength={text_len}, paragraphCount={pcount}, linkDensity={ld:.2f}"


def parts_to_markdown(parts: List[dict], img_dir: Path, base_url: str) -> Tuple[str, int]:
    lines = []
    image_count = 0
    seen_imgs = set()
    for part in parts:
        typ = part.get("type")
        val = normalize_space(part.get("value", "")) if typ != "code" else (part.get("value") or "").strip()
        if not val and typ not in ("img", "break"):
            continue
        if typ == "heading":
            level = max(1, min(6, int(part.get("level") or 2)))
            lines.append(f"{'#' * level} {val}\n")
        elif typ == "paragraph" or typ == "text":
            if len(val) >= 2:
                lines.append(val + "\n")
        elif typ == "li":
            lines.append(f"- {val}\n")
        elif typ == "quote":
            q = "\n".join("> " + x for x in val.splitlines() if x.strip())
            lines.append(q + "\n")
        elif typ == "code":
            lines.append(f"```\n{part.get('value','').strip()}\n```\n")
        elif typ == "link":
            href = part.get("href", "")
            if href and val:
                lines.append(f"[{val}]({href})\n")
        elif typ == "img":
            src = abs_url(part.get("value", ""), base_url)
            if not src or src in seen_imgs:
                continue
            seen_imgs.add(src)
            local = download_image(src, img_dir, base_url)
            alt = normalize_space(part.get("alt", "")) or "img"
            if local:
                lines.append(f"![{alt}](images/{local})\n")
                image_count += 1
            else:
                lines.append(f"![{alt}]({src})\n")
        elif typ == "break":
            lines.append("\n")
    md = "\n".join(lines)
    md = re.sub(r"\n{4,}", "\n\n\n", md).strip()
    return md, image_count


def main() -> int:
    if not URL:
        print("Usage: python3 save_article.py <url> [output_dir]")
        return 2

    out_root = Path(OUTPUT_DIR).expanduser().resolve()
    chrome_dir = Path(CHROME_USER_DATA_DIR).expanduser()
    dedicated_mode = CHROME_PROFILE_MODE == "dedicated"
    profile_dir = (
        CHROME_PROFILE_DIRECTORY
        if dedicated_mode and CHROME_PROFILE_DIRECTORY != "auto"
        else detect_profile_directory(chrome_dir, CHROME_PROFILE_DIRECTORY)
    )
    print(f"Chrome profile mode: {CHROME_PROFILE_MODE}")
    print(f"Opening real Chrome profile via CDP: {chrome_dir} / {profile_dir}")
    print(f"Remote debugging port: {CHROME_REMOTE_DEBUGGING_PORT}")
    print(f"URL: {URL}")

    started_proc = None
    browser = None
    try:
        with sync_playwright() as p:
            browser, context, started_proc = launch_or_connect_chrome(
                p,
                chrome_dir,
                profile_dir,
                CHROME_REMOTE_DEBUGGING_PORT,
                allow_parallel_chrome=dedicated_mode,
                create_profile_if_missing=dedicated_mode,
            )
            wechat_mode = is_wechat_url(URL)
            if wechat_mode:
                # Keep the persistent Chrome/CDP profile, but borrow the WeChat-
                # specific browser behavior from the old wechat-article-fetch
                # skill: mobile viewport plus a few low-risk automation-signal
                # shims before page scripts run.
                context.add_init_script(WECHAT_STEALTH_JS)
            page = context.new_page()
            if wechat_mode:
                page.set_viewport_size({"width": 375, "height": 812})
            page.goto(URL, timeout=45000, wait_until="domcontentloaded")
            time.sleep(3 if wechat_mode else 2)
            if wechat_mode:
                try:
                    if "appmsgcaptcha" in page.url or "环境异常" in page.content():
                        print("[warn] WeChat verification page detected; waiting 20s for manual verification...")
                        time.sleep(20)
                except Exception:
                    pass
                try:
                    page.wait_for_selector("#js_content", timeout=10000)
                except Exception:
                    print("[warn] Waiting for WeChat #js_content timed out; continuing with generic extraction...")
            for _ in range(8):
                page.evaluate("window.scrollBy(0, Math.max(500, window.innerHeight * 0.75))")
                time.sleep(0.35)
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(0.8)
            data = page.evaluate(EXTRACT_JS)
            final_url = page.url
            page.close()
            browser.close()
    except Exception as e:
        print(f"ERROR={type(e).__name__}: {e}")
        print(
            "Hint: by default this script uses a dedicated Hermes Chrome profile, "
            "so it can run while your normal Chrome is open. If a site needs login, "
            "log in once in the popped-up scraper Chrome window and rerun. To reuse "
            "your normal Chrome cookies instead, set CHROME_PROFILE_MODE=default and "
            "quit Chrome completely (Cmd+Q) before running. Override profile with "
            "CHROME_PROFILE_DIRECTORY='Profile 1' if needed."
        )
        return 1
    finally:
        # Only terminate Chrome if this script started it. If the script connected
        # to an already-debuggable Chrome, leave the user's browser alone.
        if started_proc is not None and started_proc.poll() is None:
            try:
                started_proc.terminate()
            except Exception:
                pass

    ok, reason = is_article(data)
    print(f"Article judgment: {ok} ({reason})")
    if not ok:
        print(f"NOT_ARTICLE={reason}")
        print(f"TITLE={data.get('title','')}")
        return 3

    parsed = urllib.parse.urlparse(final_url or URL)
    site = sanitize_filename(data.get("site") or parsed.netloc or "site", fallback="site", max_len=60)
    title = sanitize_filename(data.get("title") or "article", fallback="article", max_len=90)
    article_dir = out_root / site / title
    img_dir = article_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    markdown, image_count = parts_to_markdown(data.get("parts") or [], img_dir, final_url or URL)
    if len(re.sub(r"!\[[^\]]*\]\([^)]*\)", "", markdown).strip()) < 400:
        print("NOT_ARTICLE=extracted markdown text too short after conversion")
        return 3

    md_path = article_dir / f"{title}.md"
    front = [
        f"# {data.get('title') or title}",
        "",
        f"**站点**: {data.get('site','')}",
        f"**作者**: {data.get('author','')}",
        f"**日期**: {data.get('date','')}",
        f"**原文**: {URL}",
    ]
    if final_url and final_url != URL:
        front.append(f"**最终URL**: {final_url}")
    if data.get("description"):
        front.append(f"**摘要**: {data.get('description')}")
    front.extend(["", "---", ""])
    md_path.write_text("\n".join(front) + markdown + "\n", encoding="utf-8")

    meta_path = article_dir / "metadata.json"
    meta = {k: data.get(k) for k in ["url", "title", "site", "author", "date", "description", "textLength", "paragraphCount", "linkDensity", "score", "containerTag", "containerId", "containerClass"]}
    meta["source_url"] = URL
    meta["final_url"] = final_url
    meta["markdown_path"] = str(md_path)
    meta["image_count"] = image_count
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("DONE")
    print(f"ARTICLE_SAVED={md_path}")
    print(f"ARTICLE_DIR={article_dir}")
    print(f"IMAGE_COUNT={image_count}")
    print(f"METADATA={meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
