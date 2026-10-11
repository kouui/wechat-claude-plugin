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

## Failure causes

- "环境校验": WeChat risk control. Change IP or slow down; do not retry in a loop.
- "已被删除或违规下架": the article is gone.
- "页面结构可能已变化": the page layout changed; the selectors in `parse_meta` need updating.

Only `mp.weixin.qq.com/s/` article links are supported. One article per call.
