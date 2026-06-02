#!/usr/bin/env python3
"""Documentation server for OpenCode Session Manager.
Serves docs/ on localhost, converts .md to HTML on the fly.
Usage: python docs_server.py [--port PORT]
"""

import http.server, webbrowser, os, re, sys, socket

DEFAULT_PORT = 8765
DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs')

HTML_TPL = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — OpenCode Session Manager</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:#f8fafc; color:#1e293b; line-height:1.7; padding:20px; }
.container { max-width:960px; margin:0 auto; background:#fff; padding:30px 40px; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,.1); }
h1 { font-size:1.8em; margin:0 0 8px 0; color:#0f172a; border-bottom:2px solid #e2e8f0; padding-bottom:10px; }
h2 { font-size:1.4em; margin:24px 0 8px; color:#1e40af; }
h3 { font-size:1.15em; margin:20px 0 6px; color:#334155; }
p { margin:8px 0; }
a { color:#2563eb; text-decoration:none; }
a:hover { text-decoration:underline; }
pre { background:#1e293b; color:#e2e8f0; padding:12px 16px; border-radius:8px; overflow-x:auto; font-size:.9em; line-height:1.5; }
code { background:#f1f5f9; padding:1px 5px; border-radius:4px; font-size:.9em; }
pre code { background:transparent; padding:0; }
ul, ol { margin:8px 0 8px 24px; }
li { margin:4px 0; }
table { width:100%; border-collapse:collapse; margin:12px 0; }
th, td { border:1px solid #e2e8f0; padding:8px 12px; text-align:left; font-size:.95em; }
th { background:#f1f5f9; font-weight:600; }
tr:nth-child(even) td { background:#f8fafc; }
blockquote { border-left:4px solid #2563eb; padding:8px 16px; margin:12px 0; background:#eff6ff; border-radius:0 8px 8px 0; }
blockquote p { margin:0; }
hr { border:none; border-top:2px solid #e2e8f0; margin:24px 0; }
.back-link { display:inline-block; margin-bottom:16px; font-size:.9em; color:#64748b; }
.back-link:hover { color:#2563eb; }
.tag { display:inline-block; background:#eff6ff; color:#2563eb; font-size:.75em; padding:2px 8px; border-radius:4px; margin-right:6px; }
@media (prefers-color-scheme:dark) {
body { background:#0f172a; color:#e2e8f0; }
.container { background:#1e293b; }
h1 { color:#f1f5f9; border-bottom-color:#334155; }
h2 { color:#60a5fa; }
h3 { color:#cbd5e1; }
th { background:#334155; }
td { border-color:#334155; }
tr:nth-child(even) td { background:#1e293b; }
pre { background:#0f172a; }
code { background:#334155; }
blockquote { background:#1e3a5f; border-left-color:#60a5fa; }
.back-link { color:#94a3b8; }
}
</style>
</head>
<body>
<div class="container">
<a href="/" class="back-link">&larr; Назад к документации</a>
{content}
</div>
</body>
</html>"""

def md_to_html(md_text, title="Documentation"):
    lines = md_text.split('\n')
    html = []
    i = 0
    in_code = False
    code_buf = []
    code_lang = ""
    table_buf = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith('```'):
            if in_code:
                code = '\n'.join(code_buf)
                lang = f' class="language-{code_lang}"' if code_lang else ''
                html.append(f'<pre><code{lang}>{_escape(code)}</code></pre>')
                code_buf = []
                code_lang = ""
                in_code = False
            else:
                in_code = True
                code_lang = stripped[3:].strip()
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        if stripped.startswith('#'):
            level = len(re.match(r'^#+', stripped).group())
            text = stripped[level:].strip()
            html.append(f'<h{level}>{_escape(text)}</h{level}>')
            i += 1
            continue

        if stripped.startswith('>'):
            text = stripped[1:].strip()
            html.append(f'<blockquote><p>{_inline(text)}</p></blockquote>')
            i += 1
            continue

        if stripped.startswith('- ') or stripped.startswith('* '):
            html.append('<ul>')
            while i < len(lines):
                s = lines[i].strip()
                if s.startswith('- ') or s.startswith('* '):
                    html.append(f'<li>{_inline(s[2:])}</li>')
                elif s and not s.startswith('#'):
                    break
                else:
                    break
                i += 1
            html.append('</ul>')
            continue

        if stripped.startswith('|'):
            in_table = True
            table_buf = []
            sep_row = False
            while i < len(lines) and lines[i].strip().startswith('|'):
                s = lines[i].strip()
                if re.match(r'^\|[\s\-:]+\|', s):
                    sep_row = True
                else:
                    cells = [c.strip() for c in s.split('|')[1:-1]]
                    table_buf.append(cells)
                i += 1
            if table_buf:
                html.append('<table>')
                for idx, row in enumerate(table_buf):
                    tag = 'th' if (idx == 0 and sep_row) else 'td'
                    html.append(f'<tr>{"".join(f"<{tag}>{_escape(c)}</{tag}>" for c in row)}</tr>')
                html.append('</table>')
            continue

        if stripped == '---' or stripped == '___':
            html.append('<hr>')
            i += 1
            continue

        if stripped == '':
            i += 1
            continue

        html.append(f'<p>{_inline(stripped)}</p>')
        i += 1

    return HTML_TPL.replace('{title}', _escape(title)).replace('{content}', '\n'.join(html))

def _escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def _inline(text):
    text = _escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
    return text

class DocHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.translate_path(self.path)
        if os.path.isfile(path) and path.endswith('.md'):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    md = f.read()
            except Exception as e:
                self.send_error(500, f"Error reading file: {e}")
                return
            title = os.path.splitext(os.path.basename(path))[0]
            html = md_to_html(md, title)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
        else:
            super().do_GET()

    def log_message(self, format, *args):
        sys.stderr.write(f"[docs] {args[0]} {args[1]} {args[2]}\n")

def _find_free_port(start=8765, max_attempts=10):
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    return None

if __name__ == '__main__':
    port = DEFAULT_PORT
    if '--port' in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index('--port') + 1])
        except (ValueError, IndexError):
            print("Usage: python docs_server.py [--port PORT]", file=sys.stderr)
            sys.exit(1)

    if not os.path.isdir(DOCS_DIR):
        print(f"ERROR: docs directory not found at {DOCS_DIR}", file=sys.stderr)
        sys.exit(1)

    actual_port = _find_free_port(port)
    if not actual_port:
        print(f"ERROR: no free port found starting from {port}", file=sys.stderr)
        sys.exit(1)

    os.chdir(DOCS_DIR)
    server = http.server.HTTPServer(('127.0.0.1', actual_port), DocHandler)
    url = f'http://127.0.0.1:{actual_port}/'
    print(f"Documentation server running at {url}")
    if actual_port != port:
        print(f"(port {port} was in use, using {actual_port} instead)")
    try:
        port_file = os.path.join(os.environ.get('TMP', os.environ.get('TEMP', '/tmp')), 'opencode_docs_port.txt')
        with open(port_file, 'w') as f:
            f.write(url)
    except: pass
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
