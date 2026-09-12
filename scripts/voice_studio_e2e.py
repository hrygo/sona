"""Browser branch audit for a running voice_studio_e2e_server.py.

Requires an optional Playwright Python install and Chromium; neither is a runtime dependency.
Production React components, browser audio elements and Sona HTTP/WS handlers are used.
Only browser transports, microphone source and the external SpeechRail engine are fixtures.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import websockets
from playwright.async_api import async_playwright, expect
from voice_studio_e2e_cases import CASES, Case

ROOT = Path(__file__).resolve().parents[1]


class Session:
    def __init__(self, base: str, client: httpx.AsyncClient) -> None:
        self.base = base
        self.client = client
        self.sockets: dict[int, Any] = {}
        self.tasks: dict[int, asyncio.Task[Any]] = {}
        self.page: Any = None
        self.errors: list[str] = []
        self.http_tasks: set[asyncio.Task[Any]] = set()

    async def attach(self, page: Any) -> None:
        self.page = page
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        await page.expose_function("__ux_http", self.http)
        await page.expose_function("__ux_ws_open", self.open_socket)
        await page.expose_function("__ux_ws_send", self.send_socket)
        await page.expose_function("__ux_ws_close", self.close_socket)

    async def http(self, request: dict[str, Any]) -> dict[str, Any]:
        task = asyncio.current_task()
        if task:
            self.http_tasks.add(task)
        try:
            return await self._http(request)
        finally:
            if task:
                self.http_tasks.discard(task)

    async def _http(self, request: dict[str, Any]) -> dict[str, Any]:
        path = request["path"]
        assert path.startswith(("/api/", "/v1/")) and not path.startswith("//"), path
        kwargs: dict[str, Any] = {"headers": request.get("headers", {})}
        if "form" in request:
            kwargs["files"] = [
                (
                    field["key"],
                    (field["name"], base64.b64decode(field["file"]), field["type"])
                    if "file" in field
                    else (None, field["value"]),
                )
                for field in request["form"]
            ]
        elif "body" in request:
            kwargs["content"] = request["body"]
        result = await self.client.request(request["method"], self.base + path, **kwargs)
        return {
            "status": result.status_code,
            "body": base64.b64encode(result.content).decode(),
            "headers": {
                key: value
                for key, value in result.headers.items()
                if key.lower() in {"content-type", "x-request-id", "retry-after"}
            },
        }

    async def open_socket(self, request: dict[str, Any]) -> None:
        ident, path = request["id"], request["path"]
        assert path in {"/ws/v1/control", "/ws/assistant", "/ws/v1/meetings", "/ws/subtitles"}

        async def run() -> None:
            try:
                async with websockets.connect(
                    self.base.replace("http:", "ws:") + path, origin=self.base
                ) as socket:
                    self.sockets[ident] = socket
                    await self.page.evaluate(
                        "window.__ux_socket_event", {"id": ident, "type": "open"}
                    )
                    async for message in socket:
                        await self.page.evaluate(
                            "window.__ux_socket_event",
                            {"id": ident, "type": "message", "data": message},
                        )
            except asyncio.CancelledError:
                pass
            except Exception:
                if not self.page.is_closed():
                    await self.page.evaluate(
                        "window.__ux_socket_event", {"id": ident, "type": "close"}
                    )
            finally:
                self.sockets.pop(ident, None)

        self.tasks[ident] = asyncio.create_task(run())

    async def send_socket(self, request: dict[str, Any]) -> None:
        socket = self.sockets.get(request["id"])
        if socket:
            await socket.send(request["text"])

    async def close_socket(self, ident: int) -> None:
        task = self.tasks.pop(ident, None)
        if task:
            task.cancel()

    async def close(self) -> None:
        if self.http_tasks:
            await asyncio.gather(*tuple(self.http_tasks), return_exceptions=True)
        await asyncio.sleep(0.05)
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def prepare_assets(output: str) -> tuple[Path, str, str, str]:
    artifact = Path(output).resolve()
    artifact.mkdir(parents=True, exist_ok=True)
    bundle = artifact / "app.js"
    subprocess.run(
        [
            str(ROOT / "ui/node_modules/.bin/esbuild"),
            str(ROOT / "ui/src/main.tsx"),
            "--bundle",
            "--format=iife",
            '--define:import.meta.env={"VITE_API_BASE_URL":"http://ux.fixture"}',
            '--define:process.env.NODE_ENV="production"',
            f"--outfile={bundle}",
        ],
        check=True,
        cwd=ROOT,
    )
    css = bundle.with_suffix(".css").read_text()
    script = bundle.read_text()
    bridge = (ROOT / "scripts/voice_studio_browser_bridge.js").read_text()
    return artifact, css, script, bridge


async def run(args: argparse.Namespace) -> None:
    artifact, css, script, bridge = await asyncio.to_thread(prepare_assets, args.output)
    async with (
        httpx.AsyncClient(timeout=60, trust_env=False) as client,
        async_playwright() as playwright,
    ):
        for attempt in range(100):
            try:
                (await client.get(args.base + "/__ux/state")).raise_for_status()
                break
            except (httpx.HTTPError, OSError):
                if attempt == 99:
                    raise RuntimeError("Local UX fixture server did not become ready") from None
                await asyncio.sleep(0.2)
        browser = await playwright.chromium.launch(
            executable_path=args.chromium,
            headless=True,
            args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"],
        )
        results = []
        selected = [
            (name, case, config)
            for name, case, config in CASES
            if not args.case or args.case in name
        ]
        assert selected, "No matching browser cases"
        for name, test, config in selected:
            await client.post(args.base + "/__ux/config", json={"reset": True, **config})
            page = await browser.new_page(viewport={"width": 1440, "height": 1080})
            page.set_default_timeout(8000)
            session = Session(args.base, client)
            await session.attach(page)
            try:
                await page.set_content(
                    '<html><head></head><body><div id="root"></div></body></html>'
                )
                await page.add_style_tag(content=css)
                await page.add_script_tag(content=bridge)
                await page.add_script_tag(content=script)
                await expect(page.locator("#assistant-voice-select")).to_be_enabled(timeout=10000)
                await test(Case(page, client, args.base))
                assert not session.errors, session.errors
                results.append({"case": name, "status": "passed"})
            except Exception as error:
                results.append(
                    {
                        "case": name,
                        "status": "failed",
                        "error": str(error),
                        "traceback": traceback.format_exc(),
                        "page_errors": session.errors,
                    }
                )
            await page.screenshot(path=str(artifact / f"{name}.png"), full_page=True)
            print(
                json.dumps(
                    {k: v for k, v in results[-1].items() if k != "traceback"}, ensure_ascii=False
                ),
                flush=True,
            )
            await session.close()
            await page.close()
            (artifact / "results.json").write_text(
                json.dumps(
                    {
                        "scope": (
                            "real React + Sona HTTP/WS; browser transport and "
                            "source audio fixtures; "
                            "external MLX simulated"
                        ),
                        "results": results,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        await browser.close()
        assert all(result["status"] == "passed" for result in results), (
            "Browser branch audit contains failures"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:19830")
    parser.add_argument(
        "--chromium", help="Optional installed browser path; default uses Playwright Chromium"
    )
    parser.add_argument("--output", default="/tmp/sona-ux-e2e")
    parser.add_argument("--case", help="Run one named branch or prefix")
    args = parser.parse_args()
    assert urlsplit(args.base).hostname in {"127.0.0.1", "localhost", "::1"}, (
        "Harness only supports loopback"
    )
    asyncio.run(run(args))
