from flask import render_template_string, url_for

baseCss = """
<style>
  :root {
    --bg: #ffffff;
    --fg: #111111;
    --border: #dddddd;
    --hover: #f5f5f5;
  }

  html[data-theme="dark"] {
    --bg: #0f1115;
    --fg: #e8e8e8;
    --border: #2a2f3a;
    --hover: #1a2030;
  }

  body { font-family: sans-serif; margin: 16px; background: var(--bg); color: var(--fg); }
  .topbar { display: flex; gap: 10px; align-items: center; margin-bottom: 14px; flex-wrap: wrap; }
  .pill { padding: 6px 10px; border: 1px solid var(--border); border-radius: 999px; text-decoration: none; color: inherit; background: transparent; cursor: pointer; }
  .pill:hover { background: var(--hover); }
  .card { border: 1px solid var(--border); border-radius: 12px; padding: 12px; margin-bottom: 12px; }
  .row { display: flex; justify-content: space-between; gap: 10px; margin: 6px 0; align-items: center; }
  .label { opacity: 0.8; }
  .value { font-weight: 700; text-align: right; }
  .small { opacity: 0.7; font-size: 0.9em; }
  .muted { opacity: 0.65; }
  .mono { font-family: monospace; }
  input[type="checkbox"] { transform: scale(1.2); }
</style>
"""

themeScript = """
<script>
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
  }

  function getPreferredTheme() {
    const saved = localStorage.getItem('theme');
    if (saved === 'light' || saved === 'dark') return saved;

    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    return prefersDark ? 'dark' : 'light';
  }

  function updateThemeButton(theme) {
    const btn = document.getElementById('themeToggle');
    if (!btn) return;
    btn.textContent = (theme === 'dark') ? 'Light' : 'Dark';
  }

  function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || getPreferredTheme();
    const next = (current === 'dark') ? 'light' : 'dark';
    localStorage.setItem('theme', next);
    applyTheme(next);
    updateThemeButton(next);
  }

  document.addEventListener('DOMContentLoaded', () => {
    const theme = getPreferredTheme();
    applyTheme(theme);
    updateThemeButton(theme);
  });
</script>
"""

def renderPage(title, bodyHtml, features=None):
    features = features or {}

    showStats = bool(features.get("stats", {}).get("enabled", True))
    showIr = bool(features.get("ir", {}).get("enabled", True))
    showNfc = bool(features.get("nfc_pn532", {}).get("enabled", False))
    showWifi = bool(features.get("wifi", {}).get("enabled", False))

    navLinks = f"""
      <a class="pill" href="{url_for('home')}">Home</a>
    """

    if showStats:
        navLinks += f"""<a class="pill" href="{url_for('statsPage')}">Stats</a>"""

    if showIr:
        navLinks += f"""<a class="pill" href="{url_for('irPage')}">IR</a>"""

    if showNfc:
        navLinks += f"""<a class="pill" href="{url_for('nfcPage')}">NFC</a>"""

    if showWifi:
        navLinks += f"""<a class="pill" href="{url_for('wifiPage')}">WiFi</a> """

    navLinks += f"""
      <a class="pill" href="{url_for('modulesPage')}">Modules</a>
    """

    nav = f"""
    <div class="topbar">
      {navLinks}
      <span class="muted">|</span>
      <button class="pill" id="themeToggle" onclick="toggleTheme()">Theme</button>
      <a class="pill" href="{url_for('pinoutPage')}">Pinout</a>

    </div>
    """

    return render_template_string(f"""
    <!doctype html>
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>{title}</title>
      {baseCss}
      {themeScript}
    </head>
    <body>
      <h2>{title}</h2>
      {nav}
      {bodyHtml}
    </body>
    </html>
    """)
