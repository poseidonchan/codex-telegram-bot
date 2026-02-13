from __future__ import annotations

import html


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def as_pre_code(text: str) -> str:
    # Telegram HTML parse mode supports <pre><code>.
    return f"<pre><code>{escape_html(text)}</code></pre>"

