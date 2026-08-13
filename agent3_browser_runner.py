#!/usr/bin/env python3
"""
VigilantAI Agent 3 - container-side runner.

Runs INSIDE the browser-use-demo container, because the Playwright browser must
persist across tool calls; driving it from the host via one `docker exec` per
action would discard the page between steps.

Reads the prompt on stdin, emits newline-delimited JSON events on stdout, and
finishes with a single {"type": "result", ...} line. Diagnostics go to stderr so
stdout stays parseable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, "/home/browseruse")

from browser_use_demo.loop import APIProvider, sampling_loop  # noqa: E402
from browser_use_demo.tools import BrowserTool, ToolResult  # noqa: E402

CLICK_ACTIONS = {"left_click", "double_click", "middle_click", "triple_click"}

# Words that identify the terminal submit control.
SUBMIT_WORDS = (
    "submit", "send report", "file report", "send to fda", "finish and send",
)

# Page-advance controls. MedWatch builds these as <input type="submit" value="next">,
# so type=submit alone cannot identify the terminal control - checking it first
# blocked the agent on page 1. Navigation always wins over the submit heuristics.
NAVIGATION_WORDS = (
    "next", "continue", "previous", "back", "return", "save and", "go to",
)

# Resolves a ref through the same WeakRef map the browser tool's own JS uses
# (window.__claudeElementMap), then reports what the element actually is.
INSPECT_ELEMENT_JS = """
(ref) => {
  const map = window.__claudeElementMap;
  if (!map || !map[ref]) return null;
  const el = map[ref].deref ? map[ref].deref() : map[ref];
  if (!el || !document.contains(el)) return null;
  const text = [
    el.value, el.innerText, el.getAttribute('aria-label'),
    el.name, el.id, el.getAttribute('title'),
  ].filter(Boolean).join(' ').toLowerCase();
  return {
    tag: (el.tagName || '').toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(),
    text: text.replace(/\\s+/g, ' ').trim().slice(0, 200),
  };
}
"""


# Coordinate clicks carry no ref, so the ref map cannot be consulted. Resolve
# whatever sits at the point instead - without this the guard is bypassable by
# clicking Submit positionally.
INSPECT_POINT_JS = """
([x, y]) => {
  const hit = document.elementFromPoint(x, y);
  if (!hit) return null;
  const el = hit.closest('button, input, a, [role=button]') || hit;
  const text = [
    el.value, el.innerText, el.getAttribute('aria-label'),
    el.name, el.id, el.getAttribute('title'),
  ].filter(Boolean).join(' ').toLowerCase();
  return {
    tag: (el.tagName || '').toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(),
    text: text.replace(/\\s+/g, ' ').trim().slice(0, 200),
  };
}
"""


def emit(kind: str, **fields) -> None:
    """Write one JSONL event to stdout."""
    sys.stdout.write(json.dumps({"type": kind, **fields}) + "\n")
    sys.stdout.flush()


class SubmitGuardedBrowserTool(BrowserTool):
    """
    BrowserTool that refuses to click the final Submit control.

    This is the DOM paradigm's real advantage over prompt-only guardrails: the
    block is a code path, not an instruction the model may drift past. The agent
    still receives a normal tool error, so it can stop cleanly and report.
    """

    def __init__(self, allow_submit: bool = False):
        super().__init__()
        self.allow_submit = allow_submit
        self.reached_submit = False
        self.submit_element = None

    async def _inspect(self, ref: str):
        if self._page is None:
            return None
        try:
            return await self._page.evaluate(INSPECT_ELEMENT_JS, ref)
        except Exception as exc:  # element vanished, navigation mid-flight, etc.
            print(f"[guard] could not inspect {ref}: {exc}", file=sys.stderr)
            return None

    @staticmethod
    def _is_submit(info: dict) -> bool:
        """
        Identify the terminal submit control.

        Order matters. A multi-page HTML form advances via <input type="submit">,
        so navigation labels are checked first and always win - otherwise the
        guard fires on page 1 and the agent never traverses the form.
        """
        if not info:
            return False
        text = info.get("text", "")
        if any(word in text for word in NAVIGATION_WORDS):
            return False
        if any(word in text for word in SUBMIT_WORDS):
            return True
        # Unlabeled submit control: block conservatively. A false stop is
        # recoverable; a false FDA filing is not.
        return info.get("type") == "submit"

    async def _inspect_point(self, coordinate):
        """Inspect whatever element sits under a coordinate click."""
        if self._page is None or not coordinate:
            return None
        try:
            x, y = self._scale_coordinates(int(coordinate[0]), int(coordinate[1]))
            return await self._page.evaluate(INSPECT_POINT_JS, [x, y])
        except Exception as exc:
            print(f"[guard] could not inspect point {coordinate}: {exc}", file=sys.stderr)
            return None

    async def _adopt_newest_page(self):
        """
        Follow target=_blank navigations.

        BrowserTool pins self._page to the first page for the session, so a link
        that opens a new tab is invisible to it. On MedWatch the "Health
        Professional" link does exactly that: the agent sees no change, clicks
        again, spawns a second tab, and the site's duplicate-session dialog then
        closes the tab the tool was holding. Adopting the newest live page after
        each click keeps the tool pointed at what the user actually sees.
        """
        if self._context is None:
            return None
        live = [p for p in self._context.pages if not p.is_closed()]
        if not live:
            return None
        newest = live[-1]
        if newest is self._page and not self._page.is_closed():
            return None
        self._page = newest
        try:
            await newest.bring_to_front()
            await newest.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception as exc:
            print(f"[tabs] adopt warning: {exc}", file=sys.stderr)
        emit("tab_switch", url=newest.url[:200], open_tabs=len(live))
        return newest.url

    async def __call__(self, *, action=None, ref=None, coordinate=None, **kwargs) -> ToolResult:
        # If the page we were holding died (duplicate-session dialog, popup
        # closed), recover onto a live one before doing anything else.
        if self._page is not None and self._page.is_closed():
            await self._adopt_newest_page()

        if not self.allow_submit and action in CLICK_ACTIONS:
            # Both paths must be covered: a ref click resolves through the ref
            # map, a coordinate click through elementFromPoint.
            info = await self._inspect(ref) if ref else await self._inspect_point(coordinate)
            if self._is_submit(info):
                self.reached_submit = True
                self.submit_element = info
                emit("submit_blocked", ref=ref, coordinate=coordinate, element=info)
                return ToolResult(
                    error=(
                        "SUBMIT GUARD: blocked click on the final submit control "
                        f"(<{info.get('tag')}> type={info.get('type') or 'n/a'} "
                        f"text={info.get('text', '')[:80]!r}).\n"
                        "The form is now complete and awaiting human review. Do NOT "
                        "attempt to submit again by any means - not by coordinate "
                        "click, keyboard, or execute_js. Take a screenshot, summarize "
                        "which fields you filled and which you left blank, then stop."
                    )
                )
        result = await super().__call__(
            action=action, ref=ref, coordinate=coordinate, **kwargs
        )

        # A click may have opened the real form in a new tab. Adopt it so the
        # next read_page sees the page the user is actually looking at.
        if action in CLICK_ACTIONS or action == "navigate":
            try:
                await self._adopt_newest_page()
            except Exception as exc:
                print(f"[tabs] {exc}", file=sys.stderr)

        return result


SYSTEM_SUFFIX = (
    " You are filling a regulated FDA adverse-event report. Accuracy outranks "
    "speed: after filling each page, call read_page or get_page_text and verify "
    "every value you entered actually landed before clicking Next. If a value "
    "did not take, re-enter it. Never invent a value that is not in the case "
    "data - leave a field blank rather than guessing."
)


async def run(args) -> int:
    prompt = sys.stdin.read()
    if not prompt.strip():
        emit("result", success=False, error="empty prompt on stdin")
        return 1

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        emit("result", success=False, error="ANTHROPIC_API_KEY not set in container")
        return 1

    tool = SubmitGuardedBrowserTool(allow_submit=args.allow_submit)
    actions: list[str] = []
    state = {"turns": 0}

    class TurnBudgetExceeded(RuntimeError):
        pass

    def on_output(block) -> None:
        block = block if isinstance(block, dict) else dict(block)
        if block.get("type") == "text":
            text = (block.get("text") or "").strip()
            if text:
                emit("assistant_text", text=text[:800])
        elif block.get("type") == "tool_use":
            payload = block.get("input") or {}
            summary = str(payload.get("action", "?"))
            for key in ("url", "ref", "text", "value"):
                if payload.get(key):
                    summary += f" {key}={str(payload[key])[:60]}"
            actions.append(summary)
            emit("action", n=len(actions), summary=summary)

    def on_tool(result, tool_id) -> None:
        error = getattr(result, "error", None)
        if error:
            emit("tool_error", error=str(error)[:400])

    def on_api(request, response, error) -> None:
        if error is not None:
            emit("api_error", error=str(error)[:400])
            return
        state["turns"] += 1
        emit("turn", n=state["turns"])
        # sampling_loop runs `while True`; this is the only turn ceiling.
        if state["turns"] >= args.max_turns:
            raise TurnBudgetExceeded(f"max_turns ({args.max_turns}) exhausted")

    messages = [{"role": "user", "content": prompt}]
    outcome = {"success": True, "error": None}

    try:
        await sampling_loop(
            model=args.model,
            provider=APIProvider.ANTHROPIC,
            system_prompt_suffix=SYSTEM_SUFFIX,
            messages=messages,
            output_callback=on_output,
            tool_output_callback=on_tool,
            api_response_callback=on_api,
            api_key=api_key,
            max_tokens=args.max_tokens,
            browser_tool=tool,
        )
    except TurnBudgetExceeded as exc:
        outcome = {"success": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - surfaced to the host verbatim
        outcome = {"success": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
    finally:
        try:
            await tool.cleanup()
        except Exception as exc:  # cleanup must never mask the real result
            print(f"[cleanup] {exc}", file=sys.stderr)

    emit(
        "result",
        success=outcome["success"],
        error=outcome["error"],
        turns_used=state["turns"],
        actions_taken=actions,
        reached_submit=tool.reached_submit,
        submitted=bool(args.allow_submit and tool.reached_submit),
        submit_element=tool.submit_element,
    )
    return 0 if outcome["success"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="VigilantAI Agent 3 browser runner")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument(
        "--allow-submit",
        action="store_true",
        help="Disable the submit guard. Off by default - the agent stops at Submit.",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
