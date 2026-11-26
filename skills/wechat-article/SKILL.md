---
name: wechat-article
description: Download a WeChat Official Account article (mp.weixin.qq.com/s/...) to Markdown, optionally with images and offline HTML. Use when the user gives a mp.weixin.qq.com link and wants it saved, read, summarized or archived.
---

# WeChat article downloader

Run the bundled script with `uv` (PEP 723 script, dependencies are installed on demand; nothing else is needed):

```bash
uv run "${CLAUDE_SKILL_DIR}/scripts/download_wechat_article.py" "<url>"
```

If `${CLAUDE_SKILL_DIR}` is not substituted, use the absolute path of the `scripts/` folder next to this file.

## Output

Files are written under the **current working directory** (where the command runs), not the skill folder. Pass `--out <dir>` to change it.

| Mode | Files in `<out>/<title>/` |
| --- | --- |
| default | `index.md` (images still point at WeChat URLs), `meta.json` |
| `--full` | also `index.html` and `images/` (images downloaded, paths rewritten) |

- `--full` is tens of times larger than text only. Use it only when the user needs offline reading.
- `--format html,md` selects the formats and only applies with `--full`.
- Exit code 1 and a one-line reason on stderr on failure.
- `meta.json` has `type`: `"article"` for a normal article, `"picture"` for a picture post.

## Picture posts (图片消息)

Some links are picture posts: a carousel of images with a short caption and no article body. The script detects them and prints `图片消息: 正文在 N 张图片里`.

- `index.md` holds the caption and the images in order. `meta.json` also has `image_count`.
- With `--full`, images are saved as `images/01.jpeg`, `images/02.jpeg`, … in carousel order.
- The text is only in the images. If the user needs the text, download with `--full` and transcribe (OCR) the images in order.

## Failure causes

- "环境校验": WeChat risk control. Change IP or slow down; do not retry in a loop.
- "已被删除或违规下架": the article is gone.
- "页面结构可能已变化": the page layout changed; the selectors in `parse_meta` need updating.

Only `mp.weixin.qq.com/s/` article links are supported. One article per call.
