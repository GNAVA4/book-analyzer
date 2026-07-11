"""Convert ARCHITECTURE.md to a single self-contained HTML file with Mermaid
diagrams rendered client-side via the official CDN.

Uses python-markdown for the prose and tables; preserves ```mermaid blocks
as <pre class="mermaid"> so mermaid.js initialises them.
"""
import sys, pathlib, re, html

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import markdown
except ImportError:
    print("Need to install: venv/Scripts/pip install markdown")
    sys.exit(1)

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "ARCHITECTURE.md"
OUT = ROOT / "ARCHITECTURE.html"

src = SRC.read_text(encoding="utf-8")

# Extract ```mermaid blocks first and replace with placeholders so the markdown
# converter does NOT touch their internals (it would mangle <br/> and quotes).
mermaid_blocks = []

def _stash(match):
    mermaid_blocks.append(match.group(1))
    return f"\n\n@@MERMAID_{len(mermaid_blocks) - 1}@@\n\n"

src_stashed = re.sub(r"```mermaid\n(.*?)\n```", _stash, src, flags=re.DOTALL)

html_body = markdown.markdown(
    src_stashed,
    extensions=["tables", "fenced_code", "toc", "attr_list"],
    output_format="html5",
)

# Restore mermaid placeholders as <pre class="mermaid">.
def _unstash(m):
    idx = int(m.group(1))
    return f'<pre class="mermaid">{html.escape(mermaid_blocks[idx])}</pre>'

html_body = re.sub(r"@@MERMAID_(\d+)@@", _unstash, html_body)
# Markdown may wrap the placeholder in <p>...</p>; strip those wrappers around
# the mermaid blocks so they render as block-level diagrams.
html_body = re.sub(
    r"<p>(<pre class=\"mermaid\">.*?</pre>)</p>",
    r"\1",
    html_body,
    flags=re.DOTALL,
)

CSS = """
:root {
  --bg: #0f172a;
  --panel: #1e293b;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #7c3aed;
  --accent2: #4a90e2;
  --border: #334155;
  --code-bg: #0b1220;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
.container {
  max-width: 1120px;
  margin: 0 auto;
  padding: 32px 28px 96px;
}
h1, h2, h3, h4 { color: #f1f5f9; line-height: 1.25; }
h1 { font-size: 2.1rem; margin-top: 0; border-bottom: 2px solid var(--accent); padding-bottom: 12px; }
h2 { font-size: 1.55rem; margin-top: 2.4rem; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
h3 { font-size: 1.2rem; margin-top: 1.8rem; color: var(--accent2); }
h4 { font-size: 1rem; color: var(--muted); }
p { margin: 0.8em 0; }
em { color: var(--muted); }
strong { color: #f8fafc; }
a { color: var(--accent2); text-decoration: none; }
a:hover { text-decoration: underline; }
hr { border: 0; border-top: 1px solid var(--border); margin: 2rem 0; }
code {
  font-family: "JetBrains Mono", "Fira Code", Consolas, monospace;
  background: var(--code-bg);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 0.9em;
  color: #fbbf24;
}
pre {
  background: var(--code-bg);
  padding: 14px 18px;
  border-radius: 8px;
  overflow-x: auto;
  border: 1px solid var(--border);
}
pre code { background: transparent; color: #cbd5e1; padding: 0; }
table {
  width: 100%;
  border-collapse: collapse;
  margin: 1.2rem 0;
  background: var(--panel);
  border-radius: 6px;
  overflow: hidden;
}
th, td {
  padding: 9px 13px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  font-size: 0.93rem;
}
th { background: #283549; color: #f1f5f9; font-weight: 600; }
tbody tr:hover { background: rgba(74, 144, 226, 0.06); }
ul, ol { padding-left: 1.6rem; }
li { margin: 0.3em 0; }
blockquote {
  margin: 1em 0;
  padding: 0.6em 1em;
  border-left: 4px solid var(--accent);
  background: var(--panel);
  color: var(--muted);
  border-radius: 0 4px 4px 0;
}
pre.mermaid {
  background: #f8fafc;
  color: #0f172a;
  padding: 22px;
  border-radius: 10px;
  border: 1px solid #cbd5e1;
  text-align: center;
  margin: 1.6rem 0;
  overflow: auto;
}
.toc-card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 18px 22px;
  margin: 1.6rem 0 2.4rem;
}
.toc-card h2 { margin-top: 0; border: 0; font-size: 1.15rem; }
.toc-card ul { margin: 0; }
"""

HEAD = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Архитектура Book Analyzer</title>
<style>{CSS}</style>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{
    startOnLoad: true,
    theme: "default",
    flowchart: {{ htmlLabels: true, curve: "basis" }},
    securityLevel: "loose",
  }});
</script>
</head>
<body>
<div class="container">
"""

FOOT = """
</div>
</body>
</html>
"""

OUT.write_text(HEAD + html_body + FOOT, encoding="utf-8")
print(f"Saved: {OUT}")
print(f"Size: {OUT.stat().st_size:,} bytes")
print(f"Mermaid blocks: {len(mermaid_blocks)}")
