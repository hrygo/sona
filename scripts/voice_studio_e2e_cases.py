"""Declared, deterministic UX branch matrix. Invoked by voice_studio_e2e.py."""

from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import expect


class Case:
    def __init__(self, page: Any, client: Any, base: str) -> None:
        self.page, self.client, self.base = page, client, base

    async def config(self, **values: Any) -> None:
        response = await self.client.post(self.base + "/__ux/config", json=values)
        response.raise_for_status()

    async def state(self) -> dict[str, Any]:
        return (await self.client.get(self.base + "/__ux/state")).json()

    def button(self, name: str) -> Any:
        return self.page.get_by_role("button", name=name, exact=True)

    async def open(self, *, design: bool = False, ready: bool = True) -> None:
        await expect(self.page.locator("#assistant-voice-select")).to_be_enabled()
        await self.page.get_by_title("打开声音工坊：管理、克隆与设计音色").click()
        if ready:
            await expect(self.page.locator(".btn-record-primary")).to_be_enabled()
        if design:
            await self.page.locator(".forge-tab-btn").filter(has_text="描述声音").click()

    async def design(self) -> str:
        await self.page.locator(".voice-example-card").filter(has_text="温柔知性").click()
        await self.button("生成并保存可复用音色").click()
        await expect(self.button("检查输出（18 段）")).to_be_visible()
        voices = (await self.state())["voices"]
        return next(ident for ident in reversed(voices) if ident.startswith("design_"))

    async def accepted(self) -> None:
        await self.button("检查输出（18 段）").click()
        await expect(
            self.page.get_by_text("输出检查已通过，请试听保存后的实际声音。", exact=True)
        ).to_be_visible()
        await self.button("试听实际音色").click()
        await expect(self.button("确认使用此音色")).to_be_enabled()

    async def record(self) -> None:
        await self.page.locator(".btn-record-primary").click()
        await expect(self.page.locator(".btn-record-stop")).to_be_visible()
        await asyncio.sleep(3.1)
        await self.page.locator(".btn-record-stop").click()
        await expect(self.page.locator(".btn-submit-clone")).to_be_visible()
        await self.page.locator(".clone-name-input").fill("我的录音候选")

    async def saved_clone(self) -> str:
        await self.page.locator(".btn-submit-clone").click()
        await expect(self.button("检查输出（18 段）")).to_be_visible()
        return next(
            ident
            for ident in reversed((await self.state())["voices"])
            if ident.startswith("clone_")
        )

    def card(self, name: str) -> Any:
        return self.page.locator(".voice-deck-card").filter(has_text=name)


async def happy_design(c: Case) -> None:
    await c.open(design=True)
    ident = await c.design()
    assert (await c.state())["runtime"]["voice"] == "default"
    await expect(c.button("确认使用此音色")).to_be_disabled()
    await c.accepted()
    await c.config(activate_delay=0.6)
    await c.button("确认使用此音色").click()
    await expect(c.button("正在确认启用…")).to_be_disabled()
    assert (await c.state())["runtime"]["voice"] == "default"
    await expect(c.button("已确认启用")).to_be_visible()
    assert (await c.state())["runtime"]["voice"] == ident
    await c.page.locator(".voice-studio-close-btn").click()
    await expect(c.page.locator(".voice-studio-dialog")).to_have_count(0)
    await asyncio.sleep(0.1)
    state = await c.state()
    assert state["runtime"]["mic_muted"] is False
    assert len([call for call in state["calls"] if call["op"] == "activate"]) == 1


async def example_edit_branches(c: Case) -> None:
    await c.open(design=True)
    await expect(c.page.locator(".voice-example-card")).to_have_count(6)
    await c.page.locator(".voice-example-card").filter(has_text="技术讲解").click()
    await c.page.locator("#design-name-input").fill("保留名称")
    await c.page.locator("#design-instruction-input").fill("我修改的沉稳声音，不要覆盖。")
    await c.page.locator(".voice-example-card").filter(has_text="温柔知性").click()
    await c.button("保留当前描述").click()
    await expect(c.page.locator("#design-instruction-input")).to_have_value(
        "我修改的沉稳声音，不要覆盖。"
    )
    await c.page.locator(".voice-example-card").filter(has_text="温柔知性").click()
    await c.button("替换描述").click()
    await expect(c.page.locator("#design-name-input")).to_have_value("保留名称")
    await c.page.locator(".forge-tab-btn").filter(has_text="克隆声音").click()
    await c.page.locator(".forge-tab-btn").filter(has_text="描述声音").click()
    await expect(c.page.locator("#design-name-input")).to_have_value("保留名称")
    await c.page.locator("#design-preview-input").fill("太短")
    await c.button("生成并保存可复用音色").click()
    assert not any(call.get("path") == "v1/voices/designs" for call in (await c.state())["calls"])


async def preview_recovery(c: Case) -> None:
    await c.open(design=True)
    await c.page.locator(".voice-example-card").filter(has_text="温柔知性").click()
    await c.config(audio_fail=True)
    await c.button("试听草稿").click()
    await expect(c.button("试听草稿")).to_be_enabled()
    await c.config(audio_fail=False)
    await c.button("试听草稿").click()
    await expect(
        c.page.get_by_text(
            "已试听草稿。保存时会重新生成并核验参考；最终音色需在保存后另外试听。", exact=True
        )
    ).to_be_visible()
    await c.config(audio_delay=0.7)
    await c.button("试听草稿").click()
    await c.button("停止试听").click()
    await expect(c.page.locator("#design-instruction-input")).to_be_enabled()
    assert len((await c.state())["voices"]) == 3


async def registration_rejection(c: Case) -> None:
    await c.open(design=True)
    await c.config(registration="reject")
    await c.page.locator(".voice-example-card").filter(has_text="温柔知性").click()
    await c.button("生成并保存可复用音色").click()
    await expect(c.page.locator("#design-instruction-input")).to_be_enabled()
    await expect(c.button("检查保存结果")).to_have_count(0)
    assert len((await c.state())["voices"]) == 3
    await c.config(registration="success")
    await c.design()


async def design_lost_result(c: Case) -> None:
    await c.open(design=True)
    await c.config(registration="lost")
    await c.page.locator(".voice-example-card").filter(has_text="温柔知性").click()
    await c.button("生成并保存可复用音色").click()
    await expect(c.button("检查保存结果")).to_be_visible()
    await expect(c.page.locator("#design-instruction-input")).to_be_disabled()
    await c.config(list_fail=True)
    await c.button("检查保存结果").click()
    await expect(
        c.page.get_by_text("档案库暂不可用，注册结果仍未确认。", exact=True)
    ).to_be_visible()
    await c.config(list_fail=False)
    await c.button("检查保存结果").click()
    await expect(c.button("检查输出（18 段）")).to_be_visible()
    assert len((await c.state())["voices"]) == 4
    assert (await c.state())["runtime"]["voice"] == "default"


async def design_malformed_result(c: Case) -> None:
    await c.open(design=True)
    await c.config(registration="malformed")
    await c.page.locator(".voice-example-card").filter(has_text="温柔知性").click()
    await c.button("生成并保存可复用音色").click()
    await expect(c.button("检查保存结果")).to_be_visible()
    await c.button("检查保存结果").click()
    await expect(c.button("检查输出（18 段）")).to_be_visible()


async def design_cancel_retry(c: Case) -> None:
    await c.open(design=True)
    await c.config(register_delay=0.7)
    await c.page.locator(".voice-example-card").filter(has_text="温柔知性").click()
    await c.button("生成并保存可复用音色").click()
    await c.button("停止等待").click()
    await expect(c.button("使用同一 ID 重试")).to_be_enabled()
    await asyncio.sleep(0.8)
    await c.config(register_delay=0)
    await c.button("使用同一 ID 重试").click()
    await expect(c.button("检查保存结果")).to_be_enabled()
    await c.button("检查保存结果").click()
    await expect(c.button("检查输出（18 段）")).to_be_visible()
    calls = [call for call in (await c.state())["calls"] if call.get("path") == "v1/voices/designs"]
    assert len(calls) == 2 and calls[0]["data"] == calls[1]["data"]


async def quality_branches(c: Case) -> None:
    await c.open(design=True)
    await c.design()
    for mode in ("reject", "warn", "unevaluated", "incomplete", "error"):
        await c.config(quality=mode)
        await c.page.locator(".voice-candidate-review .btn-design-preview").first.click()
        await expect(
            c.page.locator(".voice-candidate-review .btn-design-preview").first
        ).to_be_enabled()
        await expect(c.button("确认使用此音色")).to_be_disabled()
        assert (await c.state())["runtime"]["voice"] == "default"
    await c.config(quality="pass")
    await c.page.locator(".voice-candidate-review .btn-design-preview").first.click()
    await c.button("试听实际音色").click()
    await expect(c.button("确认使用此音色")).to_be_enabled()
    await c.config(audio_fail=True)
    await c.button("试听实际音色").click()
    await expect(
        c.page.get_by_text("实际音色试听失败，尚未完成听感确认。", exact=True)
    ).to_be_visible()
    await expect(c.button("确认使用此音色")).to_be_disabled()


async def quality_cancel(c: Case) -> None:
    await c.open(design=True)
    await c.design()
    await c.accepted()
    await c.config(quality_delay=0.8)
    await c.button("重新检查输出").click()
    await c.button("停止等待检查").click()
    await expect(c.button("确认使用此音色")).to_be_disabled()
    await asyncio.sleep(0.9)
    await expect(c.button("确认使用此音色")).to_be_disabled()
    await c.config(quality_delay=0)
    await c.accepted()


async def activation_rejection(c: Case) -> None:
    await c.open(design=True)
    ident = await c.design()
    await c.accepted()
    for changes in ({"activate_fail": True}, {"activate_fail": False, "activate_mismatch": True}):
        await c.config(**changes)
        await c.button("确认使用此音色").click()
        await expect(c.button("确认使用此音色")).to_be_enabled()
        await expect(c.button("已确认启用")).to_have_count(0)
        assert (await c.state())["runtime"]["voice"] == "default"
    await c.config(activate_mismatch=False)
    await c.button("确认使用此音色").click()
    await expect(c.button("已确认启用")).to_be_visible()
    assert (await c.state())["runtime"]["voice"] == ident


async def design_back(c: Case) -> None:
    await c.open(design=True)
    before = await c.design()
    await c.button("基于此描述再设计").click()
    await expect(c.page.locator("#design-name-input")).to_have_value("知性女声")
    await c.page.locator("#design-instruction-input").fill(
        "清晰稳健的成年女性声音，语气自然沉稳，语速适中。"
    )
    await c.button("生成并保存可复用音色").click()
    await expect(c.button("检查输出（18 段）")).to_be_visible()
    ids = [ident for ident in (await c.state())["voices"] if ident.startswith("design_")]
    assert len(ids) == 2 and before in ids


async def deletion_failure_escape(c: Case) -> None:
    await c.open()
    await c.card("既有录音音色").locator(".btn-deck-delete").click()
    await c.page.keyboard.press("Escape")
    await expect(c.page.locator(".voice-delete-modal-dialog")).to_have_count(0)
    await expect(c.page.locator(".voice-studio-dialog")).to_be_visible()
    await c.card("既有录音音色").locator(".btn-deck-delete").click()
    await c.config(delete_fail=True)
    await c.button("确认删除").click()
    await expect(c.button("确认删除")).to_be_enabled()
    await expect(c.page.locator(".voice-delete-modal-dialog")).to_be_visible()
    assert "my_clone" in (await c.state())["voices"]
    await c.config(delete_fail=False)
    await c.button("确认删除").click()
    await expect(c.page.locator(".voice-delete-modal-dialog")).to_have_count(0)
    await expect(c.card("既有录音音色")).to_have_count(0)
    assert "my_clone" not in (await c.state())["voices"]


async def active_delete(c: Case) -> None:
    # Set a real acknowledged initial state before entering the workshop.
    await c.config(runtime={"voice": "my_clone"})
    await expect(c.page.locator("#assistant-voice-select")).to_have_value("my_clone")
    await c.open()
    await c.config(activate_fail=True)
    await c.card("既有录音音色").locator(".btn-deck-delete").click()
    await c.button("确认删除").click()
    await expect(c.button("确认删除")).to_be_enabled()
    assert not any(call["op"] == "DELETE" for call in (await c.state())["calls"])
    await c.config(activate_fail=False, delete_fail=True)
    await c.button("确认删除").click()
    await expect(c.button("确认删除")).to_be_enabled()
    assert (await c.state())["runtime"]["voice"] == "default"
    assert "my_clone" in (await c.state())["voices"]
    await c.config(delete_fail=False)
    await c.button("确认删除").click()
    await expect(c.page.locator(".voice-delete-modal-dialog")).to_have_count(0)


async def delete_candidate(c: Case) -> None:
    await c.open(design=True)
    ident = await c.design()
    await c.card("知性女声").locator(".btn-deck-delete").click()
    await c.button("确认删除").click()
    await expect(c.page.locator("#design-instruction-input")).to_be_visible()
    await expect(c.button("确认使用此音色")).to_have_count(0)
    assert ident not in (await c.state())["voices"]


async def clone_permission(c: Case) -> None:
    await c.open()
    await c.page.evaluate("window.__ux_microphone.mode = 'denied'")
    await c.page.locator(".btn-record-primary").click()
    await expect(
        c.page.locator(".forge-error-banner").filter(has_text="麦克风采集失败")
    ).to_be_visible()
    await c.page.evaluate("window.__ux_microphone.mode = 'synthetic'")
    await c.record()
    ident = await c.saved_clone()
    calls = [call for call in (await c.state())["calls"] if call.get("path") == "v1/voices/clone"]
    assert len(calls) == 1 and calls[0]["key"] == ident and calls[0]["data"]["id"] == ident
    assert calls[0]["authorized"]
    assert await c.page.evaluate("window.__ux_microphone.stops") > 0


async def pending_permission_cancel(c: Case) -> None:
    await c.open()
    await c.page.evaluate("window.__ux_microphone.mode = 'pending'")
    await c.page.locator(".btn-record-primary").click()
    await c.button("取消麦克风等待").click()
    await expect(c.page.locator(".btn-record-primary")).to_be_enabled()
    await c.page.evaluate("window.__ux_microphone.pending.splice(0).forEach(resolve => resolve())")
    await asyncio.sleep(0.15)
    assert await c.page.evaluate("window.__ux_microphone.stops") > 0
    await expect(c.page.locator(".btn-record-stop")).to_have_count(0)


async def clone_preflight(c: Case) -> None:
    await c.open()
    await c.record()
    for mode in ("reject", "unevaluated", "error"):
        await c.config(preflight=mode)
        await c.page.locator(".btn-submit-clone").click()
        await expect(c.page.locator(".btn-submit-clone")).to_be_enabled()
        assert not any(call.get("path") == "v1/voices/clone" for call in (await c.state())["calls"])
    await c.config(preflight="pass")
    await c.page.locator("#clone-reference-text").fill(
        "这是我在录音中实际说出的内容，校对后再注册。"
    )
    ident = await c.saved_clone()
    assert (await c.state())["voices"][ident][
        "ref_text"
    ] == "这是我在录音中实际说出的内容，校对后再注册。"


async def clone_lost_retry(c: Case) -> None:
    await c.open()
    await c.record()
    await c.config(registration="lost")
    await c.page.locator(".btn-submit-clone").click()
    await expect(c.button("检查保存结果")).to_be_visible()
    await expect(c.page.locator(".clone-name-input")).to_be_disabled()
    await c.config(registration="success")
    await c.button("使用同一 ID 重试").click()
    await expect(c.button("检查输出（18 段）")).to_be_visible()
    calls = [call for call in (await c.state())["calls"] if call.get("path") == "v1/voices/clone"]
    assert (
        len(calls) == 2
        and calls[0]["data"] == calls[1]["data"]
        and calls[0]["key"] == calls[1]["key"]
    )
    assert len((await c.state())["voices"]) == 4


async def clone_cancel(c: Case) -> None:
    await c.open()
    await c.record()
    await c.config(preflight_delay=0.7)
    await c.page.locator(".btn-submit-clone").click()
    await c.button("停止等待").click()
    await expect(c.page.locator(".btn-submit-clone")).to_be_enabled()
    await asyncio.sleep(0.8)
    assert not any(call.get("path") == "v1/voices/clone" for call in (await c.state())["calls"])
    await c.config(preflight_delay=0, register_delay=0.7)
    await c.page.locator(".btn-submit-clone").click()
    await asyncio.sleep(0.2)
    await c.button("停止等待").click()
    await expect(c.button("检查保存结果")).to_be_visible()
    await asyncio.sleep(0.8)
    await c.button("检查保存结果").click()
    await expect(c.button("检查输出（18 段）")).to_be_visible()


async def mute_failure_retry(c: Case) -> None:
    await c.config(mute_fail=True)
    await c.open(ready=False)
    await expect(c.button("重试静音准备")).to_be_visible()
    await expect(c.page.locator(".btn-record-primary")).to_be_disabled()
    assert await c.page.evaluate("window.__ux_microphone.calls") == 0
    await c.config(mute_fail=False)
    await c.button("重试静音准备").click()
    await expect(c.page.locator(".btn-record-primary")).to_be_enabled()


async def close_during_mute(c: Case) -> None:
    await c.config(mute_delay=0.5)
    await c.open(ready=False)
    await c.page.locator(".voice-studio-close-btn").click()
    await asyncio.sleep(1.2)
    state = await c.state()
    assert state["runtime"]["mic_muted"] is False
    assert [call["muted"] for call in state["calls"] if call["op"] == "mute"] == [True, False]


async def unsupported_backend(c: Case) -> None:
    await c.open(design=True)
    await c.config(registration="unsupported")
    await c.page.locator(".voice-example-card").filter(has_text="温柔知性").click()
    await c.button("生成并保存可复用音色").click()
    await expect(c.page.locator("#design-instruction-input")).to_be_enabled()
    assert not any(
        call["op"] == "POST" and call.get("path") == "v1/voices"
        for call in (await c.state())["calls"]
    )


async def lower_tier(c: Case) -> None:
    await c.open(ready=False)
    await expect(
        c.page.get_by_text(
            "⚠️ 当前 TTS 模型不支持声音创设，请切换至 Quality 配置（设计与 Base 克隆能力）。",
            exact=True,
        )
    ).to_be_visible()
    await expect(c.page.locator(".btn-record-primary")).to_have_count(0)
    await expect(c.page.locator(".voice-example-card:visible")).to_have_count(0)
    await expect(
        c.page.get_by_role("button", name="生成并保存可复用音色", exact=True, include_hidden=True)
    ).to_be_disabled()


async def narrow_keyboard(c: Case) -> None:
    await c.page.set_viewport_size({"width": 390, "height": 844})
    await c.open(design=True)
    await c.page.locator(".voice-example-card").filter(has_text="温柔知性").click()
    for theme in ("light", "dark"):
        await c.page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        assert await c.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    await c.page.keyboard.press("Escape")
    await expect(c.page.locator(".voice-studio-dialog")).to_have_count(0)
    await expect(c.page.get_by_title("打开声音工坊：管理、克隆与设计音色")).to_be_focused()


async def sidebar_candidate_review(c: Case) -> None:
    await c.config(mute_delay=0.5)
    await c.page.locator("#assistant-voice-select").select_option("my_clone")
    await expect(c.page.locator(".voice-studio-dialog")).to_be_visible()
    await expect(c.button("试听实际音色")).to_be_disabled()
    await expect(c.button("试听实际音色")).to_be_enabled()
    assert (await c.state())["runtime"]["voice"] == "default"
    assert not any(call["op"] == "activate" for call in (await c.state())["calls"])


async def modal_shortcut_isolation(c: Case) -> None:
    await c.open()
    await c.page.locator(".voice-studio-dialog").focus()
    for key in ("m", "?", "Control+k", "Control+2", "Control+3"):
        await c.page.keyboard.press(key)
    await asyncio.sleep(0.1)
    assert len([call for call in (await c.state())["calls"] if call["op"] == "mute"]) == 1
    await expect(c.page.locator('[role="dialog"][aria-modal="true"]')).to_have_count(1)
    await expect(c.page.locator(".voice-studio-dialog")).to_be_visible()


async def refresh_backend(c: Case) -> None:
    await c.open(design=True)
    await c.page.locator("#design-instruction-input").fill("刷新期间保留的手写描述。")
    await c.config(list_fail=True)
    await c.button("刷新音色与能力").click()
    await expect(
        c.page.get_by_text("刷新未完成，保留原列表。请检查 SpeechRail 后重试。", exact=True)
    ).to_be_visible()
    await expect(c.card("既有录音音色")).to_have_count(1)
    await c.config(list_fail=False, tier="light")
    await c.button("刷新音色与能力").click()
    await expect(c.page.locator(".btn-record-primary")).to_have_count(0)
    await c.config(tier="quality")
    await c.button("刷新音色与能力").click()
    await c.page.locator(".forge-tab-btn").filter(has_text="描述声音").click()
    await expect(c.page.locator("#design-instruction-input")).to_have_value(
        "刷新期间保留的手写描述。"
    )


async def unsaved_close(c: Case) -> None:
    await c.open(design=True)
    await c.page.locator("#design-instruction-input").fill("手写的温暖清晰声音描述，不能意外丢弃。")
    await c.page.locator(".voice-studio-close-btn").click()
    await expect(c.button("继续编辑")).to_be_focused()
    await c.button("继续编辑").click()
    await expect(c.page.locator("#design-instruction-input")).to_have_value(
        "手写的温暖清晰声音描述，不能意外丢弃。"
    )
    await c.page.locator(".voice-studio-close-btn").click()
    await c.button("关闭并丢弃本地草稿").click()
    await expect(c.page.locator(".voice-studio-dialog")).to_have_count(0)


async def cancel_recording(c: Case) -> None:
    await c.open()
    await c.page.locator(".btn-record-primary").click()
    await c.button("取消这次录音").click()
    await expect(c.page.locator(".btn-record-primary")).to_be_enabled()
    await expect(c.page.locator(".recorded-audio-player")).to_have_count(0)
    assert await c.page.evaluate("window.__ux_microphone.stops") > 0
    await c.record()
    await c.page.locator(".voice-studio-close-btn").click()
    await expect(c.button("继续编辑")).to_be_focused()
    await c.button("继续编辑").click()
    await expect(c.page.locator(".recorded-audio-player")).to_be_visible()


async def mute_mismatch(c: Case) -> None:
    await c.config(mute_mismatch=True)
    await c.open(ready=False)
    await expect(c.button("重试静音准备")).to_be_visible()
    await expect(c.page.locator(".btn-record-primary")).to_be_disabled()
    assert await c.page.evaluate("window.__ux_microphone.calls") == 0
    await c.config(mute_mismatch=False)
    await c.button("重试静音准备").click()
    await expect(c.page.locator(".btn-record-primary")).to_be_enabled()


CASES = [
    ("U03-unsaved-close-confirmation", unsaved_close, {}),
    ("C06-cancel-recording-draft-close", cancel_recording, {}),
    ("M03-mute-state-mismatch-retry", mute_mismatch, {}),
    ("A02-sidebar-candidate-review", sidebar_candidate_review, {}),
    ("U02-modal-shortcut-isolation", modal_shortcut_isolation, {}),
    ("V03-refresh-failed-recovered-tier", refresh_backend, {}),
    ("D01-happy-design-ack-and-restore", happy_design, {}),
    ("D02-examples-edit-and-validation", example_edit_branches, {}),
    ("D03-preview-failure-retry-cancel", preview_recovery, {}),
    ("D04-definite-rejection-editable", registration_rejection, {}),
    ("D05-lost-result-catalog-recovery", design_lost_result, {}),
    ("D06-malformed-success-recovery", design_malformed_result, {}),
    ("D07-cancel-register-same-id", design_cancel_retry, {}),
    ("Q01-five-output-failures-and-audition", quality_branches, {}),
    ("Q02-cancel-invalidates-acceptance", quality_cancel, {}),
    ("A01-activation-reject-mismatch-retry", activation_rejection, {}),
    ("D08-back-edit-new-identity", design_back, {}),
    ("X01-delete-escape-failure-retry", deletion_failure_escape, {}),
    ("X02-active-delete-switch-first", active_delete, {}),
    ("X03-delete-open-candidate", delete_candidate, {}),
    ("C01-permission-retry-real-recorder", clone_permission, {}),
    ("C02-cancel-pending-permission", pending_permission_cancel, {}),
    ("C03-preflight-failures-correction", clone_preflight, {}),
    ("C04-lost-clone-stable-retry", clone_lost_retry, {}),
    ("C05-cancel-before-after-submit", clone_cancel, {}),
    ("M01-mute-failure-retry", mute_failure_retry, {}),
    ("M02-close-before-mute-ack", close_during_mute, {}),
    ("V01-old-backend-no-fallback", unsupported_backend, {}),
    ("V02-lower-tier-blocked", lower_tier, {"tier": "light"}),
    ("U01-narrow-themes-keyboard-close", narrow_keyboard, {}),
]
