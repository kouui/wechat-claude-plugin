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

图片消息(轮播图帖子,页面里没有 #js_content):
    正文在图片里。index.md 只有帖子的简短文字和按顺序排列的图片,
    meta.json 里 type 为 "picture",image_count 为图片张数。
    需要文字时再对图片做 OCR。

依赖由上方 PEP 723 内联元数据声明,uv 自动装到临时环境,与项目环境隔离。
"""

# 依赖只存在于 uv 为本脚本建的临时环境里,pyright 按项目 .venv 解析会误报缺包。
# pyright: reportMissingImports=false, reportMissingModuleSource=false

import argparse
import hashlib
import html as htmllib
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
        if 'id="js_content"' in html or 'id="js_article"' in html or PICTURE_LIST_KEY in html:
            return html
        time.sleep(2 * (i + 1))
    raise RuntimeError("未能取到正文,页面结构可能已变化")


PICTURE_LIST_KEY = "picture_page_info_list: ["


JS_ESCAPE_RE = re.compile(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)", re.S)
JS_SIMPLE_ESCAPES = {"n": "\n", "r": "", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def js_unescape(s):
    """还原内联 JS 单引号字符串里的转义(\\xHH / \\uHHHH / \\n / \\' 等)

    每个转义只解码一次,所以 \\x5cn 得到的是反斜杠加 n,不会再被当成换行。
    \\uD83D\\uDE00 这样的 UTF-16 代理对会合成一个字符(😀),
    否则写 UTF-8 文件时会因孤立代理字符报 UnicodeEncodeError。
    """

    def one(m):
        e = m.group(1)
        if e[0] in "xu" and len(e) > 1:
            return chr(int(e[1:], 16))
        return JS_SIMPLE_ESCAPES.get(e, e)

    s = JS_ESCAPE_RE.sub(one, s)
    # 合成代理对;残留的孤立代理字符换成 U+FFFD
    return s.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def js_str(html, key):
    """取内联 JS 里第一个 `key: '...'` 的值"""
    m = re.search(rf"\b{key}:\s*'((?:[^'\\]|\\.)*)'", html)
    return js_unescape(m.group(1)) if m else ""


def parse_picture_urls(html):
    """图片消息: 按顺序取 picture_page_info_list 每一项自己的 cdn_url。

    每一项里还嵌着 share_cover / watermark_info / original_info 等对象,
    它们也有 cdn_url(分享封面、水印、原图链接),不能当正文图片。
    所以按括号层级只取第 2 层(列表 → 项)上的 cdn_url。
    """
    i = html.find(PICTURE_LIST_KEY)
    if i < 0:
        return []
    k = i + len(PICTURE_LIST_KEY) - 1  # 指向 '['
    depth, urls, item_url = 0, [], None
    key_re = re.compile(r"cdn_url:\s*'((?:[^'\\]|\\.)*)'")
    while k < len(html):
        c = html[k]
        if c == "'":  # 跳过字符串,避免里面的括号干扰层级
            k += 1
            while k < len(html) and html[k] != "'":
                k += 2 if html[k] == "\\" else 1
        elif c in "[{":
            depth += 1
            if depth == 2:
                item_url = None
        elif c in "]}":
            if depth == 2 and item_url:
                urls.append(item_url)
            depth -= 1
            if depth == 0:
                break
        elif depth == 2 and item_url is None:
            m = key_re.match(html, k)
            if m:
                item_url = htmllib.unescape(js_unescape(m.group(1)))
                k = m.end()
                continue
        k += 1
    return urls


def build_picture_content(soup, html, meta):
    """把图片消息拼成和普通文章一样的正文容器,后面的流程可以复用"""
    urls = parse_picture_urls(html)
    if not urls:
        raise RuntimeError("图片消息里找不到图片列表,页面结构可能已变化")
    text = js_str(html, "content_noencode")
    # 帖子文字里带话题链接等 HTML,只留纯文本
    text = BeautifulSoup(htmllib.unescape(text), "html.parser").get_text(" ")
    text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()).strip()
    if not meta["account"]:
        meta["account"] = js_str(html, "nick_name")
    if not meta["publish_time"]:
        m = re.search(r"create_time:\s*'(\d{10})'", html)
        if m:
            meta["publish_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(m.group(1))))
    if text:
        meta["digest"] = text
    meta["type"] = "picture"
    meta["image_count"] = len(urls)

    content = soup.new_tag("div")
    note = soup.new_tag("p")
    note.string = f"(图片消息,正文在下面 {len(urls)} 张图片里)"
    content.append(note)
    for line in text.splitlines():
        if line.strip():
            p = soup.new_tag("p")
            p.string = line.strip()
            content.append(p)
    for u in urls:
        p = soup.new_tag("p")
        p.append(soup.new_tag("img", attrs={"data-src": u}))
        content.append(p)
    return content


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
        "type": "article",
    }


def localize_images(content, img_dir: Path, rel_prefix="images", numbered=False):
    """下载正文里的图片并把 src 改成本地相对路径

    numbered=True 时按出现顺序命名为 01.jpg、02.jpg …(图片消息用,方便按顺序 OCR);
    否则用 URL 的哈希命名。
    """
    img_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for idx, img in enumerate(content.find_all("img"), 1):
        # 关键:真实地址在 data-src 上,src 往往是占位图
        src = img.get("data-src") or img.get("src")
        if not src or src.startswith("data:"):
            continue
        try:
            r = requests.get(src, headers=IMG_HEADERS, timeout=20)
            r.raise_for_status()
        except Exception as e:
            print(f"  [warn] 图片下载失败 {src[:60]}... ({e})", file=sys.stderr)
            # 保留远程地址:离线 HTML 没有懒加载 JS,只有 data-src 的图片显示不出来
            img["src"] = src
            img.attrs.pop("data-src", None)
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

        if numbered:
            name = f"{idx:02d}.{ext}"  # 用位置编号,某张下载失败也不会错位
        else:
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

    content = soup.select_one("#js_content")
    if content is not None:
        # 正文默认 visibility:hidden,靠 JS 显示,离线要去掉
        content.attrs.pop("style", None)
        content.attrs.pop("visibility", None)
    elif PICTURE_LIST_KEY in html:
        content = build_picture_content(soup, html, meta)
        print(f"      图片消息: 正文在 {meta['image_count']} 张图片里,需要文字时请对图片做 OCR")
    else:
        raise RuntimeError("找不到正文容器 #js_content")

    print(f"      公众号: {meta['account']}  发布: {meta['publish_time']}")

    workdir = Path(outdir) / safe_name(meta["title"])
    workdir.mkdir(parents=True, exist_ok=True)

    if full:
        print("[2/4] 下载图片 …")
        n = localize_images(content, workdir / "images", numbered=meta["type"] == "picture")
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
