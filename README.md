##Autonomous adverse event detection and FDA MedWatch reporting.



I built a multi-agent, containerized pipeline that detects adverse drug events (AEs) directly from raw clinical encounter data, built at the Abridge/Anthropic/Lightspeed agentic healthcare AI hackathon.

FDA adverse event (AE) reporting is currently manual. Nurses have to read encounter notes, extract the relevant fields, and file the report by hand. Nurses are often untrained on how to submit this report, and manually submitting it takes 10-15 minutes. That friction leads to under-reporting — at best, lost signal about drug safety; at worst, illegal failure to report harm. An estimated 90-99% adverse events go unreported. My pipeline reads the clinical encounter, de-identifies it, classifies severe, mandatory AEs and auto-fills FDA MedWatch Form 3500, with a read-back verification step so the agent confirms extracted values against the source text before anything is submitted. Mild-to-moderate or beneficial side effects are dumped into a txt file as Real World Insights for pharma diligence. 

I used Claude for reasoning over the clinical encounter and classification; Playwright and browser-use agent for the auto-fill/submission with read-back verification.

Impact: Autonomous FDA reporting to allow nurses to focus on patient care.
 

<video src="https://github.com/user-attachments/assets/c2928036-4c99-4a94-bee0-d489ce76d17c" autoplay loop muted playsinline width="100%"></video>



---

## Pipeline

```
Raw patient encounters (patient_case.json)
        │
        ▼
  AGENT 1 — PHI Redactor            local only, no API calls
        │                            runs FIRST so no unredacted PHI
        ▼                            ever reaches an external service
  patient_case_redacted.json
        │
        ▼
  AGENT 2 — Adverse Event Detector  Claude, de-identified input only
        │                            temporal correlation, drug
        ▼                            discontinuations, known AE patterns
  adverse_events_detected.json
        │
        ▼
  FDA reportability check           no reportable events → stop here
        │
        ├──────────────────┐
        ▼                  ▼
  AGENT 3 — Form Filler   AGENT 4 — Real-World Insights
  fills MedWatch 3500     adverse events + beneficial effects
  stops at Submit         real_world_insights.txt (table)
```

---

## Setup

```bash
# 1. API key
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env
export ANTHROPIC_API_KEY=sk-ant-...

# 2. Browser automation container (needed by Agent 3)
cd claude-quickstarts/browser-use-demo && docker compose up -d && cd -

# 3. Dependencies
python -m venv .venv && .venv/bin/pip install anthropic
```

## Running

```bash
python simple_orchestrator.py
```

Agent 3's live browser run is **paused by default** — it generates the filling
prompt and stops. To let it drive the form:

```bash
VIGILANTAI_AGENT3_LIVE=1 python simple_orchestrator.py
```

Watch it at **http://localhost:6080/vnc.html** (Chromium runs headful).

---

## Agent 3

Agent 3 drives the DOM through the browser-use-demo container's Playwright
tool — `read_page` for element references, `form_input` to fill, `get_page_text`
to verify. The benefit of browser-use is **read-back**: 
after entering a value the agent queries the field and confirms it landed.

The agent loop runs *inside* the container, because the Playwright page has to
persist across tool calls. The host builds the prompt, copies the runner in with
`docker cp`, and streams JSONL progress back.

```
host                                  container
browser_use_filler.py   ──stdin──►    agent3_browser_runner.py
     ◄──JSONL events───              └─ sampling_loop + BrowserTool
                                        └─ Playwright → Chromium
```

## Real-world insights (Agent 4)

`real_world_insights.txt` is a table report covering both directions of the
risk-benefit picture

```
ADVERSE EVENTS
 #   Drug                   Effect                        Severity  Outcome        Onset
 1   Dupilumab (Dupixent)   Mild bilateral dry eye…       Mild      Not Recovered  2024-03-27
 2   Dupilumab (Dupixent)   Progression to severe…        Serious   Recovering     2024-04-11
     └─ continuation of #1 (same evolving event, not a separate one)
     ⚠  stated onset interval disagrees with dates (… = 219 days / 7.2 months)

BENEFICIAL EFFECTS
 1   Dupilumab (Dupixent)   significant improvement in mood and depression symptoms…
     └─ Mood / depression
```

## Files

```
agent_1_phi_redactor.py            Agent 1 — local de-identification
agent_1_adverse_event_detector.py  Agent 2 — AE detection via Claude
browser_use_filler.py              Agent 3 — host side (prompt, launch)
agent3_browser_runner.py           Agent 3 — container side (browser loop)
case_prompt.py                     FDA Form 3500 field + checkbox mapping
agent_4_real_world_insights.py     Agent 4 — population insights
simple_orchestrator.py             Pipeline coordinator

deploy/browser-use-demo.override.yml   compose override (symlinked into
                                       claude-quickstarts, which is untracked)
ARCHITECTURE_UPDATED.md                design notes and rationale
```

Outputs: `patient_case_redacted.json`, `adverse_events_detected.json`,
`computer_use_prompt.txt`, `real_world_insights.txt`.

---

## Disclaimer

Demonstration system built on **synthetic** patient data
(`synthetic-ambient-fhir-25`). Not approved for real FDA reporting, for
processing real PHI without a compliance review, or for clinical use.

Do not enable submission against the live MedWatch portal with synthetic data —
that files a false report with a federal agency and pollutes a database
regulators use to detect genuine drug-safety signals.
