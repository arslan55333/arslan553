"""Publish a preview folder to Netlify (API) or Cloudflare Pages (wrangler)."""

from __future__ import annotations

import asyncio
import io
import shutil
import zipfile
from pathlib import Path

from leadengine.errors import LeadEngineError
from leadengine.http import HttpClient

NETLIFY_API = "https://api.netlify.com/api/v1"


class DeployError(LeadEngineError):
    pass


def zip_folder(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(folder).as_posix())
    return buf.getvalue()


async def deploy_netlify(http: HttpClient, token: str, folder: Path, site_name: str,
                         custom_domain: str | None = None) -> str:
    """Create (or reuse) a Netlify site and upload the folder as a zip deploy. Returns the live URL."""
    if not token:
        raise DeployError("NETLIFY_TOKEN missing in .env")
    headers = {"Authorization": f"Bearer {token}"}
    r = await http.request("GET", f"{NETLIFY_API}/sites", params={"name": site_name, "filter": "all"}, headers=headers)
    if r.status_code == 401:
        raise DeployError("Netlify rejected the token")
    sites = [s for s in (r.json() if r.status_code == 200 else []) if s.get("name") == site_name]
    if sites:
        site = sites[0]
    else:
        body = {"name": site_name}
        if custom_domain:
            body["custom_domain"] = custom_domain
        r = await http.request("POST", f"{NETLIFY_API}/sites", json=body, headers=headers)
        if r.status_code >= 400:
            raise DeployError(f"Netlify site create failed: HTTP {r.status_code} {r.text[:200]}")
        site = r.json()
    r = await http.request("POST", f"{NETLIFY_API}/sites/{site['id']}/deploys", content=zip_folder(folder),
                           headers={**headers, "Content-Type": "application/zip"}, timeout=120)
    if r.status_code >= 400:
        raise DeployError(f"Netlify deploy failed: HTTP {r.status_code} {r.text[:200]}")
    if custom_domain:
        return f"https://{custom_domain}"
    return site.get("ssl_url") or site.get("url") or f"https://{site_name}.netlify.app"


async def deploy_cloudflare(folder: Path, project: str, *, custom_domain: str | None = None,
                            api_token: str = "", account_id: str = "", runner=None) -> str:
    """Deploy with Cloudflare's wrangler CLI (``npm i -g wrangler``). Creates the project on first use."""
    if not api_token or not account_id:
        raise DeployError("CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID missing in .env")
    wrangler = shutil.which("wrangler") or shutil.which("npx")
    if runner is None and wrangler is None:
        raise DeployError("wrangler not found: install Node.js, then `npm i -g wrangler`")
    args = (["wrangler"] if wrangler and wrangler.endswith("wrangler") else ["npx", "--yes", "wrangler"]) + [
        "pages", "deploy", str(folder), "--project-name", project, "--branch", "main", "--commit-dirty=true"]
    env = {"CLOUDFLARE_API_TOKEN": api_token, "CLOUDFLARE_ACCOUNT_ID": account_id}
    run = runner or _run
    code, out = await run(args, env)
    if code != 0 and "not found" in out.lower():
        await run((args[:args.index("pages")]) + ["pages", "project", "create", project, "--production-branch", "main"], env)
        code, out = await run(args, env)
    if code != 0:
        raise DeployError(f"wrangler failed: {out[-300:]}")
    return f"https://{custom_domain}" if custom_domain else f"https://{project}.pages.dev"


async def _run(args: list[str], env: dict[str, str]) -> tuple[int, str]:
    import os

    proc = await asyncio.create_subprocess_exec(*args, env={**os.environ, **env},
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode, out.decode(errors="replace")
