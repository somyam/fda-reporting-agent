"""
VigilantAI - Autonomous Adverse Event Reporting Pipeline
Agent 1: PHI Redaction → Agent 2: Adverse Event Detection → Agent 3: Computer Use Form Submission
Redaction runs first so nothing unredacted is ever sent to an external API.
"""

import io
import json
import sys
import threading


class _ThreadRoutedStdout:
    """
    Route writes from registered threads into their own buffers.

    Agents 3 and 4 run concurrently and both print, which splices Agent 4's
    bullets into the middle of Agent 3's numbered steps. contextlib.redirect_stdout
    cannot fix this: it swaps sys.stdout globally, so it would capture both
    threads. Routing by thread identity lets Agent 3 stream live to the console
    while Agent 4 accumulates a buffer that is flushed as one block at the end.
    """

    def __init__(self, real_stdout):
        self._real = real_stdout
        self._buffers: dict[int, io.StringIO] = {}
        self._lock = threading.Lock()

    def register(self, buffer: io.StringIO) -> None:
        with self._lock:
            self._buffers[threading.get_ident()] = buffer

    def write(self, data):
        buffer = self._buffers.get(threading.get_ident())
        if buffer is not None:
            return buffer.write(data)
        return self._real.write(data)

    def flush(self):
        self._real.flush()

    def __getattr__(self, name):
        # isatty, encoding, etc. - defer anything else to the real stream.
        return getattr(self._real, name)
from agent_1_adverse_event_detector import AdverseEventDetector
from agent_1_phi_redactor import PHIRedactor
from browser_use_filler import BrowserUseFormFiller
from agent_4_real_world_insights import RealWorldInsightsLogger


def main():
    print("VIGILANTAI - AUTONOMOUS ADVERSE EVENT & SIDE EFFECT REPORTING")

    # Load patient data
    input_file = "patient_case.json"
    print(f"Loading patient data: {input_file}...")

    with open(input_file, "r") as f:
        patient_data = json.load(f)

    # Remove pre-filled adverse_event_summary to test autonomous detection
    if "adverse_event_summary" in patient_data:
        del patient_data["adverse_event_summary"]

    # AGENT 1: PHI Redaction
    # Runs FIRST so no unredacted PHI is ever sent to an external API.
    print("\nAGENT 1: PHI REDACTOR")

    phi_redactor = PHIRedactor()
    redacted_data = phi_redactor.redact_patient_data(patient_data)

    # Save redacted data
    with open("patient_case_redacted.json", "w") as f:
        json.dump(redacted_data, f, indent=2)

    print(f"\n✅ Agent 1 Complete")
    print(f"   Output: patient_case_redacted.json")

    # AGENT 2: Adverse Event Detection
    # Operates only on the de-identified copy produced by Agent 1.
    print("\nAGENT 2: ADVERSE EVENT DETECTOR")
    print("Analyzing de-identified encounters for adverse drug events...")

    detector = AdverseEventDetector()
    detected_data = detector.detect_adverse_events(redacted_data)

    # Save detected events - already de-identified, inherited from Agent 1
    with open("adverse_events_detected.json", "w") as f:
        json.dump(detected_data, f, indent=2)

    print(f"\n✅ Agent 2 Complete")
    print(f"   Output: adverse_events_detected.json")
    print(f"   Adverse Events Found: {len(detected_data['adverse_events_detected'])}")

    # Check if any events were detected
    if not detected_data.get("adverse_events_detected"):
        print("\n" + "=" * 80)
        print("⏹️  NO ADVERSE EVENTS DETECTED")
        print("=" * 80)
        print("Pipeline stopped - no adverse events identified in patient encounters.")
        return

    # Check if events meet FDA reporting criteria
    print("\n" + "─" * 80)
    print("FDA REPORTING CRITERIA ASSESSMENT:")
    print("─" * 80)

    # Get detected adverse events
    adverse_events = detected_data.get("adverse_events_detected", [])

    # Check each event for FDA reportability
    reportable_events = []
    for ae in adverse_events:
        # Agent 2 already determined if event is FDA reportable
        if ae.get("fda_reportable") or ae.get("severity") == "Serious":
            reportable_events.append(ae)

    if reportable_events:
        print(f"\n✅ {len(reportable_events)} REPORTABLE EVENT(S) IDENTIFIED\n")

        for idx, ae in enumerate(reportable_events, 1):
            print(f"Event #{idx}:")
            print(f"  Drug: {ae['suspect_drug']['generic_name']} ({ae['suspect_drug']['brand_name']})")
            print(f"  Event: {ae['adverse_event_description']}")
            print(f"  Severity: {ae['severity']}")

            if ae.get('fda_criteria_met'):
                print(f"  FDA Criteria Met:")
                for criterion in ae['fda_criteria_met']:
                    print(f"    ✓ {criterion}")
            print()

        print("📋 These events meet FDA reporting requirements.")
        print("   Proceeding to Agent 3: Computer Use Form Submission")
    else:
        print("\n❌ NO REPORTABLE EVENTS")
        print("\nThe detected adverse events do not meet FDA mandatory reporting criteria:")
        print("  - Not life-threatening")
        print("  - Did not result in death")
        print("  - Did not require hospitalization")
        print("  - Did not cause persistent disability")
        print("  - Not a congenital anomaly")
        print("\n⏹️  Pipeline stopped. No FDA report will be filed.")
        return

    # AGENTS 3 & 4: Run in parallel
    print("\n" + "─" * 80)
    print("AGENTS 3 & 4: RUNNING IN PARALLEL")
    print("─" * 80)
    print("  → Agent 3: Computer Use Form Filler")
    print("  → Agent 4: Real-World Insights Logger")

    # Open VNC viewer to watch Computer Use agent in action
    import webbrowser
    import time

    vnc_url = "http://localhost:6080/vnc.html"
    print(f"\n🖥️  Opening VNC viewer to watch Computer Use agent...")
    print(f"   URL: {vnc_url}")

    try:
        webbrowser.open(vnc_url)
        print("✅ VNC viewer opened in browser")
        print("   You can now watch the agent control the browser in real-time!")
        time.sleep(2)  # Give browser time to open
    except Exception as e:
        print(f"⚠️  Could not auto-open VNC viewer: {e}")
        print(f"   Please manually open: {vnc_url}")

    # Results storage
    agent3_result = {}
    agent4_result = {}

    # Define Agent 3 function
    def run_agent3():
        nonlocal agent3_result
        filler = BrowserUseFormFiller()
        agent3_result = filler.auto_submit_to_browser_use(
            redacted_patient_data=detected_data,
            portal_url="https://www.accessdata.fda.gov/scripts/medwatch/index.cfm?action=professional.reporting1"
        )

    # Agent 3 streams to the console live; Agent 4 is short and buffered, so the
    # two no longer interleave mid-line.
    router = _ThreadRoutedStdout(sys.stdout)
    agent4_output = io.StringIO()

    # Define Agent 4 function
    def run_agent4():
        nonlocal agent4_result
        router.register(agent4_output)
        logger = RealWorldInsightsLogger()
        agent4_result = logger.log_insights(
            patient_data=detected_data,
            adverse_events=detected_data.get("adverse_events_detected", [])
        )

    # Create threads for parallel execution
    thread3 = threading.Thread(target=run_agent3, name="Agent3-ComputerUse")
    thread4 = threading.Thread(target=run_agent4, name="Agent4-Insights")

    # Start both agents in parallel
    print("\n🚀 Starting parallel execution...")

    # Route stdout only while both threads are live, then restore.
    sys.stdout = router
    try:
        thread3.start()
        thread4.start()
        thread3.join()
        thread4.join()
    finally:
        sys.stdout = router._real

    buffered = agent4_output.getvalue()
    if buffered.strip():
        print("\n" + "─" * 80)
        print("AGENT 4: REAL-WORLD INSIGHTS")
        print("─" * 80)
        print(buffered, end="" if buffered.endswith("\n") else "\n")

    print("\n✅ Both agents completed")

    # Check for errors
    if agent3_result and not agent3_result.get("success"):
        print(f"\n❌ Agent 3 Error: {agent3_result.get('error')}")

    if agent4_result and not agent4_result.get("success"):
        print(f"\n❌ Agent 4 Error: {agent4_result.get('error')}")


if __name__ == "__main__":
    main()