#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "deploy" / "server-operations.md"
OUTPUT = ROOT / "deploy" / "server-operations.html"


def inline(text: str) -> str:
    code_values: list[str] = []

    def stash_code(match: re.Match[str]) -> str:
        code_values.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00CODE{len(code_values) - 1}\x00"

    value = re.sub(r"`([^`]+)`", stash_code, text)
    value = html.escape(value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(
        r"\[([^]]+)]\(([^)]+)\)",
        lambda match: (
            f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>'
        ),
        value,
    )
    for index, code in enumerate(code_values):
        value = value.replace(f"\x00CODE{index}\x00", code)
    return value


def slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text.lower()).strip("-") or "section"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def render_markdown(source: str) -> tuple[str, list[tuple[int, str, str]], str]:
    lines = source.splitlines()
    output: list[str] = []
    headings: list[tuple[int, str, str]] = []
    used_slugs: set[str] = set()
    title = "Open-WebUI Seedance 服务器运维手册"
    section_open = False
    index = 0

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            language = stripped[3:].strip()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            index += 1
            code = html.escape("\n".join(code_lines))
            output.append(
                '<div class="code-block"><button class="copy" type="button">Copy</button>'
                f'<pre><code class="language-{html.escape(language)}">{code}</code></pre></div>'
            )
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip()
            anchor = slug(text, used_slugs)
            if level == 1:
                title = text
                output.append(
                    '<header class="doc-head"><p class="eyebrow">OPEN-WEBUI SEEDANCE / INTERNAL OPS</p>'
                    f'<h1 id="{anchor}">{inline(text)}</h1>'
                    '<p class="doc-meta">目标服务器 <code>baize@10.104.14.205</code> · '
                    'Ubuntu · systemd · SQLite</p></header>'
                )
            else:
                if level == 2:
                    if section_open:
                        output.append("</section>")
                    output.append(f'<section class="manual-section" data-search-section id="section-{anchor}">')
                    section_open = True
                output.append(f'<h{level} id="{anchor}">{inline(text)}</h{level}>')
                headings.append((level, text, anchor))
            index += 1
            continue

        if stripped.startswith("> "):
            values: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("> "):
                values.append(lines[index].strip()[2:])
                index += 1
            output.append(f'<aside class="callout">{inline(" ".join(values))}</aside>')
            continue

        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and is_table_separator(lines[index + 1])
        ):
            headers = [cell.strip() for cell in stripped.strip("|").split("|")]
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
                index += 1
            head = "".join(f"<th>{inline(cell)}</th>" for cell in headers)
            body = "".join(
                "<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>"
                for row in rows
            )
            output.append(
                f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
            )
            continue

        list_match = re.match(r"^(-|\d+\.)\s+(.+)$", stripped)
        if list_match:
            ordered = list_match.group(1) != "-"
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            pattern = r"^\d+\.\s+(.+)$" if ordered else r"^-\s+(.+)$"
            while index < len(lines):
                match = re.match(pattern, lines[index].strip())
                if not match:
                    break
                items.append(f"<li>{inline(match.group(1))}</li>")
                index += 1
            output.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue

        paragraph = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if not candidate or re.match(r"^(#{1,3})\s+", candidate):
                break
            if candidate.startswith(("```", "> ", "|", "- ")) or re.match(r"^\d+\.\s+", candidate):
                break
            paragraph.append(candidate)
            index += 1
        output.append(f"<p>{inline(' '.join(paragraph))}</p>")

    if section_open:
        output.append("</section>")
    return "\n".join(output), headings, title


def render_html(source: str) -> str:
    body, headings, title = render_markdown(source)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    toc = "".join(
        f'<a class="toc-level-{level}" href="#{anchor}">{html.escape(text)}</a>'
        for level, text, anchor in headings
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="source-sha256" content="{digest}" />
  <link rel="icon" href="data:," />
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --ink:#17212b; --muted:#62707f; --line:#d9e0e6; --paper:#fff; --wash:#f4f7f8; --nav:#182a35; --blue:#1769aa; --teal:#087f75; --coral:#c74f3d; }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:var(--wash); font:15px/1.68 "Avenir Next","PingFang SC","Noto Sans SC",sans-serif; letter-spacing:0; }}
    .layout {{ display:grid; grid-template-columns:280px minmax(0,1fr); min-height:100vh; }}
    .sidebar {{ position:sticky; top:0; height:100vh; overflow:auto; padding:28px 20px; color:#eef4f5; background:var(--nav); border-right:4px solid var(--teal); }}
    .brand {{ display:block; margin-bottom:20px; font-size:18px; font-weight:700; color:#fff; text-decoration:none; }}
    .brand small {{ display:block; margin-top:4px; color:#9db0bb; font-size:12px; font-weight:500; }}
    .search {{ width:100%; height:40px; margin-bottom:18px; padding:0 11px; color:#fff; background:#243b48; border:1px solid #49606c; border-radius:4px; outline:none; }}
    .search:focus {{ border-color:#79c8bd; }}
    nav {{ display:grid; gap:2px; }}
    nav a {{ padding:7px 9px; color:#cedade; text-decoration:none; border-left:2px solid transparent; }}
    nav a:hover {{ color:#fff; border-left-color:#70c5b8; background:#223946; }}
    nav .toc-level-3 {{ padding-left:21px; color:#aebfc5; font-size:13px; }}
    .sidebar-foot {{ margin-top:24px; padding-top:15px; color:#9db0bb; font-size:12px; border-top:1px solid #3a505c; }}
    main {{ width:min(1080px,100%); padding:42px clamp(24px,5vw,72px) 80px; }}
    .doc-head {{ padding:0 0 28px; border-bottom:2px solid var(--ink); }}
    .eyebrow {{ margin:0 0 8px; color:var(--coral); font-size:12px; font-weight:800; text-transform:uppercase; }}
    h1 {{ margin:0; font-size:52px; line-height:1.12; letter-spacing:0; }}
    .doc-meta {{ margin:14px 0 0; color:var(--muted); }}
    .manual-section {{ padding:30px 0 10px; border-bottom:1px solid var(--line); }}
    h2 {{ margin:0 0 16px; font-size:25px; line-height:1.3; letter-spacing:0; }}
    h3 {{ margin:26px 0 10px; padding-left:10px; font-size:18px; line-height:1.4; letter-spacing:0; border-left:3px solid var(--teal); }}
    p {{ max-width:76ch; margin:10px 0 16px; }}
    ul,ol {{ margin:10px 0 18px; padding-left:24px; }}
    li {{ margin:5px 0; }}
    code {{ padding:2px 5px; font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; color:#174a6b; background:#eaf1f4; border-radius:3px; overflow-wrap:anywhere; }}
    .code-block {{ position:relative; margin:14px 0 20px; background:#14232c; border-left:4px solid var(--coral); border-radius:4px; overflow:hidden; }}
    pre {{ margin:0; padding:18px 58px 18px 18px; overflow:auto; color:#e8f0f2; font:13px/1.62 ui-monospace,SFMono-Regular,Menlo,monospace; }}
    pre code {{ padding:0; color:inherit; background:none; border-radius:0; overflow-wrap:normal; }}
    .copy {{ position:absolute; top:8px; right:8px; height:30px; padding:0 9px; color:#dbe6e8; background:#28424f; border:1px solid #49616c; border-radius:3px; cursor:pointer; }}
    .copy:hover {{ background:#365564; }}
    .callout {{ margin:18px 0; padding:14px 16px; color:#364751; background:#edf6f4; border-left:4px solid var(--teal); }}
    .table-wrap {{ margin:14px 0 22px; overflow:auto; border:1px solid var(--line); border-radius:4px; }}
    table {{ width:100%; min-width:680px; border-collapse:collapse; background:var(--paper); }}
    th,td {{ padding:10px 12px; text-align:left; vertical-align:top; border-bottom:1px solid var(--line); }}
    th {{ color:#fff; background:#29434f; font-size:13px; }}
    tr:last-child td {{ border-bottom:0; }}
    a {{ color:var(--blue); }}
    [hidden] {{ display:none !important; }}
    .empty {{ padding:28px 0; color:var(--muted); }}
    @media (max-width:820px) {{
      .layout {{ display:block; }}
      .sidebar {{ position:relative; width:100%; height:auto; padding:18px; border-right:0; border-bottom:4px solid var(--teal); }}
      .brand {{ margin-bottom:12px; }}
      nav {{ grid-template-columns:repeat(2,minmax(0,1fr)); max-height:220px; overflow:auto; }}
      nav .toc-level-3 {{ display:none; }}
      main {{ padding:28px 18px 60px; }}
      h1 {{ font-size:34px; }}
      .manual-section {{ padding-top:25px; }}
      pre {{ padding-right:18px; }}
      .copy {{ position:relative; float:right; margin:8px 8px 0; }}
    }}
    @media print {{ .sidebar,.copy {{ display:none; }} .layout {{ display:block; }} main {{ width:100%; padding:0; }} body {{ background:#fff; }} .code-block {{ break-inside:avoid; }} }}
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <a class="brand" href="#">Open-WebUI Seedance<small>服务器运维手册</small></a>
      <input class="search" type="search" placeholder="搜索命令或故障..." aria-label="搜索手册" />
      <nav aria-label="章节目录">{toc}</nav>
      <div class="sidebar-foot">Markdown 单一来源<br />SHA-256: {digest[:12]}</div>
    </aside>
    <main>{body}<p class="empty" id="empty" hidden>没有匹配的章节。</p></main>
  </div>
  <script>
    const input = document.querySelector('.search');
    const sections = [...document.querySelectorAll('[data-search-section]')];
    const empty = document.querySelector('#empty');
    input.addEventListener('input', () => {{
      const query = input.value.trim().toLowerCase();
      let visible = 0;
      sections.forEach((section) => {{
        const match = !query || section.textContent.toLowerCase().includes(query);
        section.hidden = !match;
        if (match) visible += 1;
      }});
      empty.hidden = visible !== 0;
    }});
    document.querySelectorAll('.copy').forEach((button) => {{
      button.addEventListener('click', async () => {{
        const code = button.parentElement.querySelector('code').textContent;
        await navigator.clipboard.writeText(code);
        button.textContent = 'Copied';
        setTimeout(() => button.textContent = 'Copy', 1200);
      }});
    }});
  </script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the server operations HTML manual.")
    parser.add_argument("--check", action="store_true", help="fail when generated HTML is stale")
    args = parser.parse_args()
    rendered = render_html(SOURCE.read_text(encoding="utf-8"))
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            print(f"stale generated manual: {OUTPUT}", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
