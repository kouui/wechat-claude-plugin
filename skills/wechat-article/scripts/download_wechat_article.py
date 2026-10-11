#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests",
#     "beautifulsoup4",
#     "markdownify",
# ]
# ///
"""
微信公众号单篇文章下载器

用法(默认输出到运行命令时所在的目录,即当前工作目录):
    uv run download_wechat_article.py https://mp.weixin.qq.com/s/xxxxxx
    uv run download_wechat_article.py <url> --full --format html,md
    uv run download_wechat_article.py <url> --out some/dir

输出(默认,仅文本),<out> 默认为当前工作目录:
    <out>/<标题>/index.md     Markdown(图片仍指向微信原始 URL)
    <out>/<标题>/meta.json    标题/作者/公众号/发布时间/原始链接

输出(--full,离线归档):
    额外产出 index.html 与 images/,图片下载到本地并改写为相对路径。
    整篇下来体积是纯文本的几十倍,只在需要离线阅读时用。

依赖由上方 PEP 723 内联元数据声明,uv 自动装到临时环境,与项目环境隔离。
"""

# 依赖只存在于 uv 为本脚本建的临时环境里,pyright 按项目 .venv 解析会误报缺包。
# pyright: reportMissingImports=false, reportMissingModuleSource=false

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from bs4 import BeautifulSoup
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 图片有防盗链,必须带 Referer
IMG_HEADERS = {**HEADERS, "Referer": "https://mp.weixin.qq.com/"}


def safe_name(s, maxlen=80):
    """把标题变成合法目录名"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s).strip(" .")
    return s[:maxlen] or "untitled"


def fetch_html(url, retries=3):
    for i in range(retries):
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.encoding = r.apparent_encoding or "utf-8"
        html = r.text
        # 微信在触发风控时会返回验证页 / 内容已删除页
        if "环境异常" in html or "去验证" in html:
            raise RuntimeError("触发微信环境校验,请更换 IP 或降低抓取频率")
        if "该内容已被发布者删除" in html or "此内容因违规无法查看" in html:
            raise RuntimeError("文章已被删除或违规下架")
        if 'id="js_content"' in html or 'id="js_article"' in html:
            return html
        time.sleep(2 * (i + 1))
    raise RuntimeError("未能取到正文,页面结构可能已变化")


def parse_meta(soup, url):
    def pick(*selectors):
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                txt = el.get("content") or el.get_text(strip=True)
                if txt:
                    return txt
        return ""

    # 发布时间在内联 JS 里,用正则挖
    publish_time = ""
    m = re.search(r'var\s+ct\s*=\s*"?(\d{10})"?', soup.decode())
    if m:
        publish_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(m.group(1))))

    return {
        "title": pick("#activity-name", "h1.rich_media_title", 'meta[property="og:title"]'),
        "author": pick("#js_author_name", "span.rich_media_meta_text", 'meta[name="author"]'),
        "account": pick("#js_name", "a#js_name", 'meta[property="og:article:author"]'),
        "publish_time": publish_time,
        "digest": pick('meta[name="description"]', 'meta[property="og:description"]'),
        "url": url,
    }


def localize_images(content, img_dir: Path, rel_prefix="images"):
    """下载正文里的图片并把 src 改成本地相对路径"""
    img_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for img in content.find_all("img"):
        # 关键:真实地址在 data-src 上,src 往往是占位图
        src = img.get("data-src") or img.get("src")
        if not src or src.startswith("data:"):
            continue
        try:
            r = requests.get(src, headers=IMG_HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as e:
            print(f"  [warn] 图片下载失败 {src[:60]}... ({e})", file=sys.stderr)
            continue

        # 从 wx_fmt 参数或 Content-Type 推断扩展名
        ext = "jpg"
        m = re.search(r"wx_fmt=(\w+)", src)
        if m:
            ext = m.group(1)
        elif "png" in r.headers.get("Content-Type", ""):
            ext = "png"
        elif "gif" in r.headers.get("Content-Type", ""):
            ext = "gif"

        name = hashlib.md5(src.encode()).hexdigest()[:16] + "." + ext
        (img_dir / name).write_bytes(r.content)

        img["src"] = f"{rel_prefix}/{name}"
        img.attrs.pop("data-src", None)
        # 去掉懒加载留下的样式,否则离线打开图片不显示
        img.attrs.pop("data-ratio", None)
        img["style"] = "max-width:100%;height:auto;"
        count += 1
        time.sleep(0.3)
    return count


def keep_remote_images(content):
    """不下载图片,只把懒加载的 data-src 提到 src,让 md 里的图片指向微信原图"""
    count = 0
    for img in content.find_all("img"):
        src = img.get("data-src")
        if src:
            img["src"] = src
            img.attrs.pop("data-src", None)
        if img.get("src"):
            count += 1
    return count


def to_markdown(content, meta):
    try:
        from markdownify import markdownify
    except ImportError:
        return None
    body = markdownify(str(content), heading_style="ATX")
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    head = (
        f"# {meta['title']}\n\n> {meta['account']} · {meta['author']} · "
        f"{meta['publish_time']}\n>\n> 原文: {meta['url']}\n\n---\n\n"
    )
    return head + body


HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{max-width:720px;margin:0 auto;padding:24px 16px;
 font:16px/1.8 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#222}}
h1{{font-size:22px;line-height:1.4}}
.meta{{color:#888;font-size:13px;margin-bottom:24px}}
img{{max-width:100%;height:auto}}
</style></head><body>
<h1>{title}</h1>
<div class="meta">{account} · {author} · {publish_time}<br>
<a href="{url}">查看原文</a></div>
{content}
</body></html>"""


def download(url, outdir, formats, full):
    print("[1/4] 抓取页面 …")
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    meta = parse_meta(soup, url)
    if not meta["title"]:
        raise RuntimeError("解析不到标题,页面结构可能已变化")
    print(f"      标题: {meta['title']}")
    print(f"      公众号: {meta['account']}  发布: {meta['publish_time']}")

    content = soup.select_one("#js_content")
    if content is None:
        raise RuntimeError("找不到正文容器 #js_content")
    # 正文默认 visibility:hidden,靠 JS 显示,离线要去掉
    content.attrs.pop("style", None)
    content.attrs.pop("visibility", None)

    workdir = Path(outdir) / safe_name(meta["title"])
    workdir.mkdir(parents=True, exist_ok=True)

    if full:
        print("[2/4] 下载图片 …")
        n = localize_images(content, workdir / "images")
        print(f"      共 {n} 张,已本地化")
    else:
        print("[2/4] 跳过图片下载(未指定 --full)…")
        n = keep_remote_images(content)
        print(f"      共 {n} 张,保留微信原始 URL")

    print("[3/4] 写出文件 …")
    (workdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    if "html" in formats:
        (workdir / "index.html").write_text(HTML_TMPL.format(content=str(content), **meta), encoding="utf-8")

    if "md" in formats:
        md = to_markdown(content, meta)
        if md is None:
            print("      [warn] 未安装 markdownify,跳过 md", file=sys.stderr)
        else:
            (workdir / "index.md").write_text(md, encoding="utf-8")

    print(f"[4/4] 完成 → {workdir}")
    return workdir


def main():
    ap = argparse.ArgumentParser(description="微信公众号文章下载器")
    ap.add_argument("url", help="mp.weixin.qq.com/s/ 开头的文章链接")
    ap.add_argument("--out", default=".", help="输出目录,默认当前工作目录")
    ap.add_argument("--full", action="store_true", help="下载图片并产出离线 HTML;不加则只写 index.md 和 meta.json")
    ap.add_argument("--format", default="html,md", help="html,md;仅在 --full 下生效")
    args = ap.parse_args()
    # Windows 中文系统控制台默认 GBK,避免打印标题时因个别字符抛 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # 非 --full 时没有本地图片,离线 HTML 没有意义,强制只出 md
    formats = {x.strip() for x in args.format.split(",")} if args.full else {"md"}
    try:
        download(args.url, args.out, formats, args.full)
    except Exception as e:
        print(f"失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
