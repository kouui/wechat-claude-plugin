# wechat-claude-plugin

Claude Code plugin for WeChat Official Account articles.

## Skills

| Skill | Purpose |
| --- | --- |
| `wechat-article` | Download one `mp.weixin.qq.com/s/...` article to Markdown (`--full` for offline HTML and images) |

## Install

```bash
claude plugin marketplace add kouui/wechat-claude-plugin
claude plugin install wechat@wechat-claude-plugin
```

Requires [`uv`](https://docs.astral.sh/uv/). The script declares its own dependencies (PEP 723).

## Use directly

```bash
uv run skills/wechat-article/scripts/download_wechat_article.py "https://mp.weixin.qq.com/s/xxxx"
```

Output goes to the directory you run the command from: `./<title>/index.md` and `meta.json`.

Picture posts (图片消息, an image carousel with no article body) are supported too: `index.md` lists the images in order, and `--full` saves them as `images/01.jpeg`, `images/02.jpeg`, …

## Layout

```text
.claude-plugin/plugin.json
.claude-plugin/marketplace.json
skills/wechat-article/SKILL.md
skills/wechat-article/scripts/download_wechat_article.py
```
