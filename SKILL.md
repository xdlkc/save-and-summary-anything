---
name: save-and-summary-anything
description: "Save and summarize arbitrary article-like web pages from a URL. Use when the user gives a web URL and asks to judge whether it is a text-heavy article, save/download/archive it locally as Markdown with images, or summarize it; or when troubleshooting article scraping where Chrome login state, cookies, Google account sessions, paywalls, profile locks, or profile selection are not taking effect. Defaults to a dedicated Hermes Chrome profile via real Chrome remote debugging/CDP + Playwright so scraping can run while the user's normal Chrome is open; can optionally reuse the user's default Chrome profile when explicitly requested."
---

# Save and Summary Anything

把任意网址交给大模型时，先判断它是否是“文字为主的文章页”；若是，则默认用 Hermes 专用 Chrome profile 打开页面，抓取正文、图片和元数据，保存为 Markdown，然后在聊天中用中文总结。该专用 profile 可与用户日常 Chrome 并行运行；如需复用日常 Chrome 登录态，再显式切换到 default profile 模式。

## 默认约定

- 若用户只丢一个 URL，默认执行完整流程：判定文章 → 保存 Markdown/图片 → 回复本地路径 + 中文总结。
- 若脚本判定不是文字为主文章页，不要强行保存；向用户说明原因，并可给出页面标题/候选文本长度作为依据。
- 总结默认直接回复在聊天里；不要创建 `_summary.md`，除非用户明确要求“保存总结/生成总结文件”。
- 微信公众号和 X/Twitter 链接优先使用专用技能：`wechat-article-fetch` 或 `twitter-article-fetch`。本技能用于其它普通网页。

## 环境要求

- Python 3.8+
- `playwright` 可用：`pip install playwright -i https://pypi.org/simple/ && python3 -m playwright install chrome`
- 本机安装 Google Chrome
- 需要可显示窗口；脚本默认 `headless=False`
- 默认使用 Hermes 专用 Chrome profile：`~/.hermes/chrome-profiles/save-and-summary`，端口 `9223`。这样用户日常 Chrome 开着也能抓文章，不需要 Cmd+Q 退出。
- 若网页需要登录/付费墙，在弹出的“抓取专用 Chrome”里登录一次即可；该专用 profile 会持久保存 cookie。
- 如必须复用用户日常 Chrome 登录态，可设置 `CHROME_PROFILE_MODE=default`，脚本会读取 `~/Library/Application Support/Google/Chrome/Local State` 的 `profile.last_used`；但这种模式要求日常 Chrome 先完全退出，除非它本来就是带 remote debugging 启动的。
- 脚本通过真实 Google Chrome + remote debugging + Playwright CDP 连接来复用登录态/cookie；不要用 `launch_persistent_context()` 直接自动化默认 profile（现代 Chrome 可能打开空 profile，cookie 不生效）

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `ANY_ARTICLE_OUTPUT` | 文章保存根目录 | 若设置 `ANY_ARTICLE_OUTPUT` 则使用它；否则使用 `$OBSIDIAN_VAULT_PATH/网页文章`；若两者都未设置，则使用 `~/saved-articles` |
| `CHROME_PROFILE_MODE` | Chrome profile 模式：`dedicated` 使用 Hermes 专用 profile，可与日常 Chrome 并行；`default` 复用用户日常 Chrome profile/cookies，但通常要求先退出 Chrome | `dedicated` |
| `CHROME_USER_DATA_DIR` | Chrome user data 根目录；dedicated 模式默认是 Hermes 专用目录，default 模式默认是系统 Chrome 目录 | `~/.hermes/chrome-profiles/save-and-summary`（dedicated）或 `~/Library/Application Support/Google/Chrome`（default） |
| `CHROME_PROFILE_DIRECTORY` | Chrome profile 名称；dedicated 模式默认 `Default`，default 模式可用 `auto` 读取 Local State 的 last_used | `Default`（dedicated）或 `auto`（default） |
| `CHROME_REMOTE_DEBUGGING_PORT` | 真实 Chrome remote debugging 端口 | `9223`（dedicated）或 `9222`（default） |
| `CHROME_EXECUTABLE` | Google Chrome 可执行文件路径 | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |

推荐 Obsidian 用户设置：

```bash
export OBSIDIAN_VAULT_PATH="/path/to/your/obsidian-vault"
# or:
export ANY_ARTICLE_OUTPUT="/path/to/article-archive"
```

## 用法

```bash
python3 ~/.hermes/skills/save-and-summary-anything/scripts/save_article.py "<URL>"

# 或指定输出目录
python3 ~/.hermes/skills/save-and-summary-anything/scripts/save_article.py "<URL>" "/path/to/output"
```

## 专用 Chrome 登录/验证流程

当用户要求“帮我打开登录”“让我登录这个站点”“专用 profile 登录态没保存”时，使用默认 dedicated profile 手动打开目标站点，不要索要手机号、验证码、密码等敏感信息；优先让用户扫码或在弹窗里自行完成登录：

```bash
mkdir -p "$HOME/.hermes/chrome-profiles/save-and-summary/Default"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9223 \
  --user-data-dir="$HOME/.hermes/chrome-profiles/save-and-summary" \
  --profile-directory=Default \
  --no-first-run \
  --no-default-browser-check \
  "<URL>"
```

验证端口与登录态：

```bash
python3 - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.connect_over_cdp('http://127.0.0.1:9223')
    ctx = b.contexts[0]
    page = ctx.pages[-1]
    print('url=', page.url)
    print('title=', page.title())
    text = page.locator('body').inner_text(timeout=5000)
    print('has_login_button=', '登录/注册' in text or 'Sign in' in text or 'Log in' in text)
    print('cookies=', len(ctx.cookies()))
    b.close()
PY
```

登录完成后，重新运行 `save_article.py`；cookie 会保存在 `~/.hermes/chrome-profiles/save-and-summary`，后续复用。

输出结构：

```text
<输出目录>/<站点>/<文章标题>/
    <文章标题>.md
    images/
        img_xxxxxx.jpg
```

## 执行流程

1. 对 URL 做路由：
   - `mp.weixin.qq.com`：加载 `wechat-article-fetch`，使用其脚本。
   - `x.com` / `twitter.com`：加载 `twitter-article-fetch`，使用其脚本。
   - 其它网页：继续使用本技能脚本。
2. 运行 `save_article.py`。
3. 看脚本输出：
   - `ARTICLE_SAVED=<path>` 表示成功保存，读取该 Markdown。
   - `NOT_ARTICLE=...` 表示不是文字为主文章页，直接解释并停止。
4. 用保存的 Markdown 正文生成中文总结：
   - 先用 1-2 句话概括核心观点。
   - 再用 3-8 条要点列出主要内容。
   - 保留具体事实、案例、数字、方法、结论。
   - 如果文章很长，先分块阅读再综合总结。
5. 最终回复包含：
   - Markdown 绝对路径
   - 图片数量（若脚本输出有）
   - 中文总结

## 关键实现细节

- 默认模式现在是 `CHROME_PROFILE_MODE=dedicated`：脚本用 `~/.hermes/chrome-profiles/save-and-summary` 作为隔离 user-data-dir、`Default` 作为 profile、`9223` 作为 CDP 端口。这样可以在用户日常 Chrome 正常开启时并行启动抓取专用 Chrome，不会遇到默认 profile lock，也不要求 Cmd+Q。
- dedicated 模式第一次遇到需要登录的网站时，让用户在弹出的抓取专用 Chrome 窗口登录/验证一次；cookie 会保存在该专用 profile，后续复用。
- 如确实需要复用日常 Chrome 的登录态，显式设置 `CHROME_PROFILE_MODE=default`：
  1. 读取 `CHROME_USER_DATA_DIR/Local State`，自动取 `profile.last_used` 作为 profile 目录，除非用户显式设置 `CHROME_PROFILE_DIRECTORY`。
  2. 如果 9222 端口没有已开启的 CDP Chrome，且 Chrome 当前没运行，则启动真实 Chrome：
     `Google Chrome --remote-debugging-port=9222 --user-data-dir=<Chrome root> --profile-directory=<profile>`。
  3. 用 Playwright `chromium.connect_over_cdp('http://127.0.0.1:<port>')` 连接这个真实 Chrome。
- 脚本不要用 Playwright `launch_persistent_context()` 直接打开默认 profile；它在现代 Chrome 上可能因默认 profile 自动化限制而得到空 profile，表现为 Google 未登录、cookie 缺失。
- 如果 Chrome 已经运行但没有 remote debugging，脚本会失败并提示用户先 Cmd+Q 退出 Chrome；否则新参数不会对已有 Chrome 进程生效，仍然拿不到正确登录态。
- 抓取正文时优先选择 `article`、`main`、常见 article/content 容器；若找不到再用正文评分最高的节点。
- 对知乎 `<img class="ztext-gif" src="..._b.jpg">` 这类动图封面，脚本会优先把 URL 改成 `..._b.gif` 下载真实 GIF，避免只保存静态 JPG 封面。
- 保存时保留标题、站点、作者、日期、原文 URL；正文按 DOM 顺序输出标题、段落、列表、引用、代码块、链接和图片。
- 图片下载使用页面 URL 作为 Referer；失败时保留原始远程图片链接。

## 失败处理

- Chrome 登录态/cookie 缺失：默认 dedicated 模式使用抓取专用 profile，不共享日常 Chrome cookie；在弹出的抓取专用 Chrome 窗口登录一次后重跑即可。若必须用日常 Chrome cookie，设置 `CHROME_PROFILE_MODE=default`，先 Cmd+Q 完全退出日常 Chrome 再重跑；否则 `--remote-debugging-port` 不会应用到已有 Chrome 进程。
- 登录/付费墙：先让弹出的 Chrome 窗口完成登录或验证；然后重跑脚本。
- default 模式 Chrome profile 锁定：关闭本机 Chrome 后重试；或改回默认 dedicated 模式避免锁定。
- 反爬/验证码：等待用户在弹出的 Chrome 窗口手动验证；不要尝试绕过安全验证。
- 提取正文过短：不要编造总结；说明未识别到足够正文，并给出可选方案（用户提供正文、截图、或允许用浏览器手动选择文本）。
