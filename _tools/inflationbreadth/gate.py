"""Password-gate an HTML page the same way as the site's other gated pages.

The page is encrypted with AES-256-GCM using a key derived from the password
(PBKDF2-HMAC-SHA256, 200k iterations) and decrypted in the browser, so only
ciphertext is published.

  python3 gate.py charts/pce_breadth.html path/to/site/inflationbreadth/index.html [--title "Page title"]
Password comes from $PAGE_PASSWORD (or .env PAGE_PASSWORD=...).
"""
import base64
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITER = 200_000
TITLE = "PCE Inflation Breadth"

GATE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex,nofollow">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet">
<style>
  *{box-sizing:border-box} html,body{height:100%}
  body{margin:0;font-family:Lora,Georgia,serif;background:#faf9f7;color:#1a1a1a;
       display:flex;align-items:center;justify-content:center}
  .gate{width:min(92vw,420px);text-align:center;padding:36px 30px}
  .gate h1{font-size:24px;font-weight:500;margin:0 0 6px}
  .gate p{color:#555;font-size:14px;line-height:1.5;margin:0 0 22px}
  .gate form{display:flex;gap:8px;justify-content:center}
  .gate input{flex:1;font-family:inherit;font-size:15px;padding:10px 12px;border:1px solid #ccc;border-radius:6px;background:#fff}
  .gate input:focus{outline:none;border-color:#1a1a1a}
  .gate button{font-family:inherit;font-size:15px;padding:10px 18px;border:none;border-radius:6px;background:#1a1a1a;color:#fff;cursor:pointer}
  .gate button:disabled{opacity:.5;cursor:default}
  .err{color:#b00020;font-size:13px;height:18px;margin-top:12px}
</style></head>
<body>
  <div class="gate">
    <h1>__TITLE__</h1>
    <p>This preview is password-protected. Enter the password to continue.</p>
    <form id="f">
      <input id="pw" type="password" autocomplete="current-password" placeholder="Password" autofocus>
      <button id="btn" type="submit">Unlock</button>
    </form>
    <div class="err" id="err"></div>
  </div>
<script>
const SALT="__SALT__", IV="__IV__", CT="__CT__", ITER=__ITER__;
const bytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
const f=document.getElementById('f'), pwEl=document.getElementById('pw'),
      btn=document.getElementById('btn'), err=document.getElementById('err');
async function unlock(pw){
  const base=await crypto.subtle.importKey('raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveKey']);
  const key=await crypto.subtle.deriveKey({name:'PBKDF2', salt:bytes(SALT), iterations:ITER, hash:'SHA-256'},
              base, {name:'AES-GCM', length:256}, false, ['decrypt']);
  const pt=await crypto.subtle.decrypt({name:'AES-GCM', iv:bytes(IV)}, key, bytes(CT));
  try{ sessionStorage.setItem('gate:'+location.pathname, pw); }catch(_){}
  const html=new TextDecoder().decode(pt);
  document.open(); document.write(html); document.close();
}
f.addEventListener('submit', async e=>{
  e.preventDefault(); err.textContent=''; btn.disabled=true; btn.textContent='Unlocking…';
  try{ await unlock(pwEl.value); }
  catch(_){ err.textContent='Incorrect password.'; btn.disabled=false; btn.textContent='Unlock'; pwEl.select(); }
});
// Remember the password for this tab so reloads (e.g. after an update) skip the gate.
try{ const pw=sessionStorage.getItem('gate:'+location.pathname); if(pw) unlock(pw).catch(()=>{}); }catch(_){}
</script></body></html>"""


def password():
    pw = os.environ.get("PAGE_PASSWORD")
    env = Path(__file__).resolve().parent / ".env"
    if not pw and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("PAGE_PASSWORD="):
                pw = line.split("=", 1)[1].strip()
    if not pw:
        sys.exit("PAGE_PASSWORD not set")
    return pw


def gate(html: str, pw: str, title: str = TITLE) -> str:
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(pw.encode())
    ct = AESGCM(key).encrypt(iv, html.encode(), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return (GATE.replace("__TITLE__", title).replace("__SALT__", b64(salt)).replace("__IV__", b64(iv))
            .replace("__ITER__", str(ITER)).replace("__CT__", b64(ct)))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--title", default=TITLE)
    a = ap.parse_args()
    a.dst.parent.mkdir(parents=True, exist_ok=True)
    a.dst.write_text(gate(a.src.read_text(), password(), a.title))
    print(f"wrote {a.dst}")
