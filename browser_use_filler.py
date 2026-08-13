"""
VigilantAI - Agent 3: FDA MedWatch form filler (browser tool).

Replaces the computer-use implementation. Instead of screenshotting a desktop
and clicking pixel coordinates, this drives the browser-use-demo container's
Playwright tool against the DOM: `read_page` for element refs, `form_input` to
fill, `get_page_text` to verify what actually landed.

Why that matters for a regulated filing: DOM targeting lets the agent read a
value back after entering it. A mis-typed dose or a dropdown that silently
reverted is detectable, where in a screenshot it looks identical to success.

The agent loop runs inside the container (the Playwright page must persist
across tool calls); this module builds the prompt, launches the runner, and
streams its progress back to the orchestrator.
"""

import json
import os
import shutil
import subprocess
from typing import Dict, List, Optional

from case_prompt import (
    FORM_SECTION_REFERENCE,
    render_case_data,
    render_outcome_checkboxes,
)

# Agent 3's live browser run is PAUSED by default.
#
# Multi-page traversal against the real MedWatch portal is unproven: repeated
# automated runs tripped FDA's rate limiter
# (accessdata.fda.gov/apology_objects/excessive-requests-apology.html), which
# also confounded debugging. Until the flow is validated against a local mock,
# the orchestrator generates the prompt and stops.
#
# Re-enable a live run with:  VIGILANTAI_AGENT3_LIVE=1
LIVE_ENV_VAR = "VIGILANTAI_AGENT3_LIVE"

DEFAULT_CONTAINER = "browser-use-demo-browser-use-1"
RUNNER_PATH = "/home/browseruse/agent3_browser_runner.py"
CASE_DATA_DIR = "/home/browseruse/case_data"

MEDWATCH_URL = (
    "https://www.accessdata.fda.gov/scripts/medwatch/"
    "index.cfm?action=professional.reporting1"
)


class BrowserUseFormFiller:
    """Agent 3. Fills FDA MedWatch Form 3500 via the container's browser tool."""

    def __init__(self, container: Optional[str] = None):
        self.container = container or os.getenv(
            "VIGILANTAI_BROWSER_CONTAINER", DEFAULT_CONTAINER
        )

    # ------------------------------------------------------------------ prompt

    def generate_prompt(
        self,
        redacted_patient_data: Dict,
        portal_url: str = MEDWATCH_URL,
        allow_submit: bool = False,
    ) -> str:
        """Build the Agent 3 prompt: how to drive the form, then what to file."""
        prompt = f"""You are filing FDA MedWatch Form 3500 (voluntary adverse event report).

HOW TO WORK THIS FORM:
1. navigate to {portal_url}
2. call read_page to get element refs for the current page's fields
3. fill fields with form_input using those refs - refs are reliable, coordinates are not
4. before advancing, call get_page_text and confirm each value you entered is present.
   If a value did not land (dropdown reverted, field rejected the format), re-enter it.
5. click the "Next"/"Continue" control to advance, then repeat from step 2
6. keep going page by page until you reach the page bearing the final Submit button

This form spans multiple pages. Do not stop early because a page looks finished -
only the Submit page is the end.

CLICKING - THIS MATTERS:
* ALWAYS click by ref (left_click ref=ref_N). NEVER click by coordinate.
  Coordinates on this page are unreliable and have previously landed on footer
  links instead of the Next button, throwing away a page of entered data.
* Refs go stale after scrolling, navigation, or any DOM update. Call read_page
  again to refresh them immediately before clicking, not from an earlier turn.
* To find the Next control, use read_page or find - do not guess its position.
* A left_click with no ref is a coordinate click. Do not emit one. If you cannot
  find a ref for something, call read_page again or use find - never fall back
  to clicking a position.

TEXT FIELDS THAT REFORMAT AS YOU TYPE:
* Date fields on this form apply an input mask. Using `type` character by
  character produces mangled values like "03//2/7/20" from "03/27/2024".
* For any date or otherwise formatted field, use form_input (it sets the value
  directly) instead of clicking the field and typing.
* After filling a date, read it back with get_page_text and confirm it matches
  what you intended. If it is mangled, clear it and set it with form_input.

RECOVERING FROM A WRONG PAGE:
* `navigate` takes a FULL URL only. There is no "back" action, and
  `navigate back` resolves to https://back/ and fails.
* To go back one page, use: execute_js with `history.back()`
* Do NOT re-navigate to the form's start URL to recover. That resets the form
  and discards every field you have entered. Use history.back() instead.

TABS:
* Some MedWatch links open the form in a NEW TAB. This is handled for you - the
  tool follows the new tab automatically. After a click that seems to do
  nothing, just call read_page again; you are probably already on the new page.
* Do NOT click the same link twice because "nothing happened". A second click
  opens a duplicate tab, which triggers MedWatch's "already open in another tab"
  dialog - and dismissing that dialog CLOSES the tab, losing all your work.
* If that dialog does appear, do not click OK. Call read_page to re-orient.

"""
        if not allow_submit:
            prompt += """STOPPING CONDITION (HARD):
When you reach the final Submit button, STOP. Do not click it. Take a screenshot
and report which fields you filled and which you left blank for human review.
A code-level guard will block the click anyway; treat that block as confirmation
you are finished, not as an error to work around.

"""
        else:
            prompt += "SUBMISSION: You are authorized to click the final Submit button.\n\n"

        prompt += render_case_data(redacted_patient_data)
        prompt += render_outcome_checkboxes(redacted_patient_data)
        prompt += "\n" + FORM_SECTION_REFERENCE
        prompt += f"""
ADDITIONAL CONTEXT AVAILABLE:
These files are readable in this container. Consult them with bash whenever a
field needs detail this prompt does not carry:
  {CASE_DATA_DIR}/patient_case_redacted.json
  {CASE_DATA_DIR}/form_filling_instructions.md
  {CASE_DATA_DIR}/fda_extracted_data.json

Begin by navigating to the portal and calling read_page.
"""
        return prompt

    def save_prompt_to_file(
        self,
        redacted_patient_data: Dict,
        output_file: str = "computer_use_prompt.txt",
        portal_url: str = MEDWATCH_URL,
    ) -> Dict:
        """Write the prompt to disk for manual use via the Streamlit UI."""
        prompt = self.generate_prompt(redacted_patient_data, portal_url)
        with open(output_file, "w") as handle:
            handle.write(prompt)
        return {
            "success": True,
            "prompt_file": output_file,
            "prompt_length": len(prompt),
            "next_step": f"Paste into http://localhost:8080, or run Agent 3 directly",
        }

    # ----------------------------------------------------------------- running

    def _preflight(self) -> Optional[str]:
        """Return an error string if the container cannot run Agent 3."""
        if shutil.which("docker") is None:
            return "docker CLI not found on PATH"

        probe = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self.container],
            capture_output=True, text=True,
        )
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            return (
                f"container '{self.container}' is not running. "
                "Start it with: docker compose up -d"
            )

        # Copy the runner in fresh rather than bind-mounting it. A single-file
        # bind mount pins the host inode at container start, so an edited runner
        # silently keeps executing the old copy - and a stale __pycache__ entry
        # can hide that the file changed at all.
        local_runner = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "agent3_browser_runner.py")
        if not os.path.exists(local_runner):
            return f"runner not found at {local_runner}"

        copy = subprocess.run(
            ["docker", "cp", local_runner, f"{self.container}:{RUNNER_PATH}"],
            capture_output=True, text=True,
        )
        if copy.returncode != 0:
            return f"failed to copy runner into container: {copy.stderr.strip()[:300]}"

        # Drop any bytecode cached from a previous copy.
        subprocess.run(
            ["docker", "exec", self.container, "rm", "-rf", "/home/browseruse/__pycache__"],
            capture_output=True, text=True,
        )

        verify = subprocess.run(
            ["docker", "exec", self.container, "python3", "-m", "py_compile", RUNNER_PATH],
            capture_output=True, text=True,
        )
        if verify.returncode != 0:
            return f"runner failed to compile in container: {verify.stderr.strip()[:300]}"
        return None

    def auto_submit_to_browser_use(
        self,
        redacted_patient_data: Dict,
        portal_url: str = MEDWATCH_URL,
        allow_submit: bool = False,
        model: str = "claude-sonnet-5",
        max_turns: int = 120,
        live: Optional[bool] = None,
    ) -> Dict:
        """
        Run Agent 3.

        By default this generates the prompt and stops - the live browser run is
        paused (see LIVE_ENV_VAR). Pass live=True, or set VIGILANTAI_AGENT3_LIVE=1,
        to drive the form.

        Returns the same shape the orchestrator already expects: success,
        error, turns_used, actions_taken, submitted.
        """
        if live is None:
            live = os.getenv(LIVE_ENV_VAR, "").strip() in ("1", "true", "yes")

        print("\n🖥️  AGENT 3: BROWSER-TOOL FORM FILLER")
        print("=" * 80)

        prompt = self.generate_prompt(redacted_patient_data, portal_url, allow_submit)

        # Keep writing the prompt file - it is the manual fallback path.
        saved = self.save_prompt_to_file(redacted_patient_data, portal_url=portal_url)

        if not live:
            print("⏸️  Live browser run is PAUSED (multi-page traversal unvalidated).")
            print(f"✅ Prompt generated ({len(prompt)} characters)")
            print(f"💾 Saved to: {saved['prompt_file']}")
            print("\n   To file this report:")
            print("     1. Open http://localhost:8080")
            print(f"     2. Paste the contents of {saved['prompt_file']}")
            print("     3. Review each page before the final Submit")
            print(f"\n   To re-enable the automated run: {LIVE_ENV_VAR}=1")
            print("=" * 80)
            return {
                "success": True,
                "paused": True,
                "prompt_file": saved["prompt_file"],
                "turns_used": 0,
                "actions_taken": [],
                "submitted": False,
                "message": "Agent 3 paused - prompt generated for manual review",
            }

        problem = self._preflight()
        if problem:
            print(f"❌ Preflight failed: {problem}")
            return {
                "success": False,
                "error": problem,
                "turns_used": 0,
                "actions_taken": [],
                "recommendation": "Paste computer_use_prompt.txt into http://localhost:8080",
            }

        guard = "OFF — agent may submit" if allow_submit else "ON — stops before final submit"
        print(f"✅ Prompt generated ({len(prompt)} characters)")
        print(f"🎯 Target URL: {portal_url}")
        print(f"🤖 Model: {model}")
        print(f"🧭 Driving: Playwright DOM tool in {self.container}")
        print(f"🛑 Submit guard: {guard}")
        print("=" * 80)
        print("\n🚀 Starting browser session...")
        print("   Watch live: http://localhost:6080/vnc.html\n")

        command = [
            "docker", "exec", "-i", self.container,
            "python3", RUNNER_PATH,
            "--model", model,
            "--max-turns", str(max_turns),
        ]
        if allow_submit:
            command.append("--allow-submit")

        return self._stream(command, prompt)

    def _stream(self, command: List[str], prompt: str) -> Dict:
        """Run the container runner and translate its JSONL events to console output."""
        result: Optional[Dict] = None
        actions: List[str] = []

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            return {
                "success": False,
                "error": f"failed to launch runner: {exc}",
                "turns_used": 0,
                "actions_taken": [],
            }

        assert process.stdin and process.stdout
        process.stdin.write(prompt)
        process.stdin.close()

        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(f"   {line}")  # runner diagnostic, pass through
                continue

            kind = event.get("type")
            if kind == "action":
                actions.append(event["summary"])
                print(f"🔧 [{event['n']:>3}] {event['summary']}")
            elif kind == "assistant_text":
                print(f"💬 {event['text']}")
            elif kind == "tool_error":
                print(f"⚠️  tool error: {event['error']}")
            elif kind == "api_error":
                print(f"❌ API error: {event['error']}")
            elif kind == "submit_blocked":
                element = event.get("element") or {}
                print("\n🛑 SUBMIT GUARD TRIPPED — reached the final Submit control")
                print(f"   element: <{element.get('tag')}> {element.get('text', '')[:70]!r}")
                print("   Form is filled and awaiting human review.\n")
            elif kind == "submit_page_reached":
                element = event.get("element") or {}
                print("\n🏁 Reached the Submit page and stopped without clicking")
                print(f"   control: <{element.get('tag')}> {element.get('text', '')[:70]!r}\n")
            elif kind == "result":
                result = event

        process.wait()
        stderr = (process.stderr.read() if process.stderr else "") or ""

        if result is None:
            return {
                "success": False,
                "error": f"runner exited ({process.returncode}) without a result. {stderr[:400]}",
                "turns_used": 0,
                "actions_taken": actions,
            }

        if result.get("success"):
            print(f"\n✅ Agent 3 complete — {result.get('turns_used')} turns, "
                  f"{len(result.get('actions_taken') or [])} actions")
            if result.get("reached_submit") and not result.get("submitted"):
                print("   Reached Submit and stopped, as designed.")
            elif not result.get("reached_submit"):
                print("   ⚠️  Finished without reaching the Submit page — the form is "
                      "probably incomplete. Review before filing.")
        else:
            print(f"\n❌ Agent 3 failed: {result.get('error')}")

        result.setdefault("actions_taken", actions)
        return result


if __name__ == "__main__":
    import sys

    source = sys.argv[1] if len(sys.argv) > 1 else "adverse_events_detected.json"
    with open(source) as handle:
        data = json.load(handle)
    outcome = BrowserUseFormFiller().auto_submit_to_browser_use(data)
    sys.exit(0 if outcome.get("success") else 1)
