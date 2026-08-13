# VigilantAI - Updated Architecture

## **Overview**

VigilantAI is a **fully autonomous adverse event reporting system** with 4 specialized agents:

1. **Agent 1: PHI Redactor** (Local processing, no API calls — runs first)
2. **Agent 2: Adverse Event Detector** (Claude-powered, de-identified input only)
3. **Agent 3: Browser-Tool Form Filler** (DOM automation against FDA MedWatch)
4. **Agent 4: Real-World Insights Logger** (runs in parallel with Agent 3)

> **Ordering is a compliance property, not a preference.** Redaction runs before
> any external API call, so no unredacted PHI ever leaves the machine.

---

## **Complete Pipeline Flow**

```
Raw Patient EHR Data
(encounters, medications, symptoms, labs - NO pre-filled adverse event summary)
         ↓
┌────────────────────────────────────────────────────────────────┐
│  AGENT 1: PHI Redactor                                         │
│  ─────────────────────────────────────────────────────────     │
│  • Runs FIRST, and 100% locally (NO API calls)                 │
│  • Strips all patient identifiable information:                │
│    - Names → "NAME_A1B2C3D4"                                   │
│    - Patient IDs → "PATIENT_D9FD325B"                          │
│    - Dates → Shifted by consistent offset                      │
│    - Locations → "City_XYZ"                                    │
│    - Phone → "XXX-XXX-XXXX"                                    │
│  • Uses deterministic hashing (same patient = same token)      │
│  • HIPAA compliant de-identification                           │
└────────────────────────────────────────────────────────────────┘
         ↓
  patient_case_redacted.json
  (Safe to send to external APIs)
         ↓
┌────────────────────────────────────────────────────────────────┐
│  AGENT 2: Adverse Event Detector                               │
│  ─────────────────────────────────────────────────────────     │
│  • Operates ONLY on the de-identified copy from Agent 1        │
│  • Uses Claude to analyze encounters                           │
│  • Identifies temporal relationships (symptoms after drug)     │
│  • Detects drug discontinuations                               │
│  • Recognizes known drug-AE patterns                           │
│  • Outputs structured adverse events with confidence scores    │
│  • Determines FDA reportability                                │
└────────────────────────────────────────────────────────────────┘
         ↓
  adverse_events_detected.json
  {
    "adverse_events_detected": [
      {
        "suspect_drug": {...},
        "adverse_event_description": "...",
        "severity": "Serious",
        "fda_reportable": true,
        "fda_criteria_met": ["hospitalization", "intervention required"],
        "confidence_level": "High"
      }
    ]
  }
         ↓
┌────────────────────────────────────────────────────────────────┐
│  FDA REPORTABILITY CHECK                                       │
│  • Checks if any detected events meet FDA criteria:           │
│    - Patient death                                            │
│    - Life-threatening                                         │
│    - Hospitalization                                          │
│    - Persistent disability                                    │
│    - Congenital anomaly                                       │
│    - Required intervention to prevent impairment              │
│  • If NO reportable events → STOP                            │
│  • If YES reportable events → Continue to Agents 3 & 4       │
└────────────────────────────────────────────────────────────────┘
         ↓
   ┌─────────────────────────┴─────────────────────────┐
   ↓                                                   ↓
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  AGENT 3: Browser-Tool Filler    │  │  AGENT 4: Real-World Insights    │
│  ──────────────────────────────  │  │  ──────────────────────────────  │
│  • Builds FDA Form 3500 prompt   │  │  • Aggregates detected events    │
│  • Drives Playwright via the     │  │    into population-level signal  │
│    browser-use-demo container    │  │  • Separates adverse events from │
│  • DOM-based, not pixel-based:   │  │    beneficial effects            │
│    1. navigate to portal         │  │  • Writes real_world_insights.txt│
│    2. read_page → element refs   │  └──────────────────────────────────┘
│    3. form_input by ref          │
│    4. get_page_text to VERIFY    │   Runs in parallel with Agent 3
│       each value actually landed │   (threading.Thread)
│    5. click Next → repeat        │
│  • Stops at final Submit         │
└──────────────────────────────────┘
         ↓
  FDA MedWatch Form 3500 Filled
  (Awaiting human verification & submission)
```

### **Why DOM automation rather than screenshot-and-click**

Agent 3 originally drove the **computer use** tool: screenshot the desktop, locate
fields visually, click pixel coordinates. It was rewritten to use the
browser-use-demo **browser tool**, which targets the DOM. For a regulated filing
the deciding factor is *read-back*: after entering a value, the agent can query
the DOM and confirm it landed. A mis-typed dose or a dropdown that silently
reverted is detectable — in a screenshot it looks identical to success.

Secondary benefits: coordinates go stale on scroll while refs do not; page
transitions are awaited by Playwright rather than guessed at; and termination is
a boolean DOM query rather than a visual judgment call.

---

## **Key Features**

### **1. Fully Autonomous Adverse Event Detection**

**Input**: Raw patient encounters (no manual adverse event documentation required)

**Agent 2 analyzes**:
- **Temporal relationships**: "Conjunctivitis appeared 2 weeks after starting Dupixent"
- **Drug discontinuations**: "Dupixent stopped at Visit 5 due to severe eye symptoms"
- **Worsening conditions**: "Mild dry eyes at Visit 4 → Severe conjunctivitis at Visit 5"
- **Known adverse event patterns**: "Dupixent is known to cause conjunctivitis in ~10% of patients"
- **Rare/unexpected events**: Can identify novel adverse events not previously documented
  - Example: New symptom appearing after drug initiation with no prior reports
  - Based on temporal correlation, not just known drug-AE databases

**Output**: Structured adverse events with:
- Suspect drug (generic + brand name)
- Event description
- Severity (Mild/Moderate/Serious)
- Causality (Definite/Probable/Possible/Unlikely)
- Timeline (drug start → event onset → drug stop)
- Clinical evidence
- FDA reportability determination
- Confidence level

### **2. HIPAA-Compliant PHI Protection**

**Local Processing**:
- Agent 1 runs entirely on local machine
- NO API calls during de-identification
- No patient data leaves machine in identifiable form

**De-identification Methods**:
- **Hashing**: SHA256 with random seed → consistent tokens
- **Date shifting**: Random offset (-180 to +180 days), consistent per patient
- **Location redaction**: Cities → "City_XYZ", Zip → "XXXXX"
- **Phone/email**: Fully masked

**Result**: Only de-identified data is sent to the Claude API or entered into the FDA portal

### **3. Intelligent FDA Reportability Assessment**

**Agent 2** determines if event meets FDA MedWatch criteria:

| Criterion | How Detected |
|-----------|--------------|
| Death | Searches for "death", "fatal", "died" in outcome/description |
| Life-threatening | Checks severity, seriousness criteria, event description |
| Hospitalization | Looks for "hospitalized", "hospital admission", "prolongation" |
| Disability | Searches for "disability", "impairment", "permanent damage" |
| Congenital anomaly | Checks for "birth defect", "congenital" |
| Intervention required | Identifies "required intervention to prevent permanent impairment" |

**Pipeline behavior**:
- If **NO reportable events** → Pipeline stops, no FDA report filed
- If **reportable events** → Continues to PHI redaction and form submission

### **4. Autonomous Browser Control (Browser Tool)**

**Where the loop runs**: inside the `browser-use-demo` container, not on the host.
The Playwright page must persist across tool calls; one `docker exec` per action
would discard the page between steps. The host builds the prompt, launches
`agent3_browser_runner.py` in the container, and streams JSONL progress back.

```
host                                    container
────                                    ─────────
browser_use_filler.py
  ├─ build prompt (case_prompt.py)
  ├─ docker cp runner ──────────────►  agent3_browser_runner.py
  ├─ docker exec, prompt on stdin ──►    └─ sampling_loop(BrowserTool)
  └─ read JSONL events on stdout ◄───       └─ Playwright → Chromium (headful)
```

**Tool execution loop**:
1. `navigate`: go to the MedWatch portal
2. `read_page`: get element refs (`ref_1`, `ref_2`, …) for the current page
3. `form_input ref=ref_N value=…`: fill by reference, not coordinate
4. `get_page_text`: **verify** each entered value is actually present; re-enter if not
5. `left_click` the Next control → page advances
6. Repeat from step 2 until the final Submit page

**Safety gate — enforced in code, not prompt text**: `SubmitGuardedBrowserTool`
subclasses `BrowserTool` and intercepts click actions. It resolves the ref through
`window.__claudeElementMap` (the same map the tool's own JS uses), inspects the
element, and returns a tool *error* instead of clicking the terminal control. The
agent receives that as a normal failure and stops cleanly.

⚠️ **Navigation must outrank the submit heuristic.** MedWatch renders its Next
button as `<input type="submit" value="next">`, so `type=submit` alone cannot
identify the terminal control — an early version of the guard fired on page 1 and
the agent never traversed the form. `_is_submit()` therefore checks
`NAVIGATION_WORDS` first and returns `False` on any match. An *unlabeled*
`type=submit` is still blocked conservatively: a false stop is recoverable, a
false FDA filing is not.

⚠️ **The runner is copied in with `docker cp`, never bind-mounted.** A single-file
bind mount pins the host inode at container start, so editing the runner leaves
the container silently executing a frozen copy — and a stale `__pycache__` entry
can mask that the file changed at all. `_preflight()` copies the runner, clears
`__pycache__`, and byte-compiles it before every run.

---

## **File Structure**

```
abridge/
├── patient_case.json                         # Input: Raw patient data
│
├── agent_1_phi_redactor.py                   # Agent 1: Local PHI redaction
├── agent_1_adverse_event_detector.py         # Agent 2: Autonomous AE detection
├── browser_use_filler.py                     # Agent 3: host side (prompt + launch)
├── agent3_browser_runner.py                  # Agent 3: container side (browser loop)
├── case_prompt.py                            # Shared FDA Form 3500 field mapping
├── agent_4_real_world_insights.py            # Agent 4: population-level insights
│
├── simple_orchestrator.py                    # Main pipeline coordinator
│
├── patient_case_redacted.json                # Output: Agent 1 results
├── adverse_events_detected.json              # Output: Agent 2 results
├── computer_use_prompt.txt                   # Output: Agent 3 prompt (manual fallback)
├── real_world_insights.txt                   # Output: Agent 4 results
│
├── claude-quickstarts/browser-use-demo/      # Browser automation container
│   └── docker-compose.override.yml           # Mounts case data read-only
│
└── ARCHITECTURE_UPDATED.md                   # This file
```

> Note: the filenames retain their original numbering (`agent_1_phi_redactor.py`,
> `agent_1_adverse_event_detector.py`) which no longer matches pipeline position.
> The orchestrator is authoritative: redaction runs first.

---

## **Usage**

### **Run the complete pipeline**:

```bash
python simple_orchestrator.py
```

### **Expected Output**:

```
================================================================================
VIGILANTAI - AUTONOMOUS ADVERSE EVENT REPORTING
================================================================================
Agent 1: PHI Redaction (Local, no API)
Agent 2: Adverse Event Detection (Claude-powered)
Agents 3 & 4: Form Submission + Insights (parallel)
================================================================================

Loading patient data: patient_case.json...

AGENT 1: PHI REDACTOR
  → Redacting patient demographics...
  → Redacting encounter data...
✅ Agent 1 Complete
   Output: patient_case_redacted.json

AGENT 2: ADVERSE EVENT DETECTOR
Analyzing de-identified encounters for adverse drug events...
Claude analysis complete - 2 event(s) found

┌─ Adverse Event #1 ───────────────────────────────────────┐
│ 💊 Dupilumab (Dupixent)
│ ⚠️  Mild bilateral dry eye and conjunctival injection
│    Severity: Mild   Confidence: Medium   FDA Reportable: NO
└──────────────────────────────────────────────────────────┘
┌─ Adverse Event #2 ───────────────────────────────────────┐
│ 💊 Dupilumab (Dupixent)
│ ⚠️  Severe bilateral conjunctivitis, punctate keratitis,
│    visual acuity 20/20 → 20/30 OD, 20/40 OS
│    Severity: Serious   Confidence: High   FDA Reportable: YES
└──────────────────────────────────────────────────────────┘

✅ Agent 2 Complete — Adverse Events Found: 2

FDA REPORTING CRITERIA ASSESSMENT:
✅ 1 REPORTABLE EVENT(S) IDENTIFIED
  FDA Criteria Met:
    ✓ intervention required (urgent ophthalmology referral, drug discontinuation)
    ✓ disability (temporary decreased visual acuity, corneal involvement)

AGENTS 3 & 4: RUNNING IN PARALLEL

🖥️  AGENT 3: BROWSER-TOOL FORM FILLER
================================================================================
✅ Prompt generated (12827 characters)
🎯 Target URL: https://www.accessdata.fda.gov/scripts/medwatch/...
🤖 Model: claude-sonnet-5
🧭 Driving: Playwright DOM tool in browser-use-demo-browser-use-1
🛑 Submit guard: ON — stops before final submit
================================================================================

🚀 Starting browser session...
   Watch live: http://localhost:6080/vnc.html

🔧 [  1] navigate url=https://www.accessdata.fda.gov/scripts/medwatch/...
🔧 [  2] read_page text=interactive
🔧 [  3] form_input ref=ref_12 value=PATIENT_D9FD325B
🔧 [  4] get_page_text
💬 Verified: patient identifier landed correctly.
...
🔧 [ 37] left_click ref=ref_38

🛑 SUBMIT GUARD TRIPPED — reached the final Submit control
   element: <input> 'submit report'
   Form is filled and awaiting human review.

✅ Agent 3 complete — 39 turns, 38 actions
   Reached Submit and stopped, as designed.

📊 Extracting real-world insights...
✅ Insights logged to: real_world_insights.txt
   Total insights: 2  (adverse events: 2, beneficial effects: 0)

✅ Both agents completed
```

---

## **Key Capability: Detecting Rare & Novel Adverse Events**

### **Why This Matters:**

Traditional pharmacovigilance systems rely on:
- **Known adverse event databases** - Only detect what's already documented
- **Rule-based alerts** - Miss unexpected drug-symptom combinations
- **Manual provider reporting** - Depends on provider recognizing the pattern

### **VigilantAI's Approach:**

**Agent 1 uses temporal pattern recognition**, not just known drug-AE databases:

```
Example: Detecting a Rare Event

Patient Timeline:
  Visit 1: Starts new medication "DrugX" for condition A
  Visit 2 (2 weeks later): Reports strange tingling in fingers
  Visit 3 (1 month later): Tingling worsening, now bilateral
  Visit 4 (2 months later): DrugX discontinued, tingling resolves within days

Claude Analysis:
  ✓ Temporal relationship: Symptom started after drug initiation
  ✓ Dose-response: Worsened over time while on drug
  ✓ Dechallenge: Resolved after drug stopped
  ✓ Causality: Probable

  → Reports to FDA even if "DrugX → tingling" is NOT in literature
  → This could be the FIRST report of this adverse event
  → Critical for FDA signal detection
```

### **Detection Algorithm:**

1. **Start with temporal correlation** (not known patterns)
   - Did a NEW symptom appear after drug start?
   - Did an EXISTING symptom worsen after drug start?

2. **Assess strength of evidence**
   - Time to onset (days? weeks? months?)
   - Dechallenge (did it improve when drug stopped?)
   - Re-challenge (did it recur if drug restarted?)
   - Alternative explanations (other drugs, disease progression, comorbidities)

3. **Assign causality**
   - **Definite**: Positive re-challenge
   - **Probable**: Clear temporal relationship + dechallenge + no other explanation
   - **Possible**: Temporal relationship but could be other causes
   - **Unlikely**: Temporal relationship but clearly explained by other factors

4. **Report if medically significant**
   - Serious events (hospitalization, disability, life-threatening) → Always report
   - Moderate events → Report if causality ≥ Possible
   - Mild events → Report if causality ≥ Probable

### **Real-World Impact:**

**FDA relies on initial case reports to detect signals:**
- First few reports of rare AE → Investigation triggered
- Pattern emerges across multiple reports → Safety review
- If confirmed → Drug label update, boxed warning, or market withdrawal

**VigilantAI can detect the "first case" that starts this process**

---

## **Advantages Over Previous Architecture**

| Aspect | Old (3-agent with structured extraction) | New (3-agent with autonomous detection) |
|--------|------------------------------------------|----------------------------------------|
| **Input** | Required pre-filled `adverse_event_summary` | Works with raw encounter data only |
| **Autonomy** | Depended on human documenting AE | Fully autonomous AE detection |
| **Detection** | None (assumed AE already identified) | Claude analyzes temporal patterns |
| **Flexibility** | Only worked with known AE format | Discovers unexpected adverse events |
| **Rare Events** | Could not detect novel AEs | **Can detect first-ever reports of rare AEs** |
| **Use Case** | Automate reporting of known AEs | Discover + report both known AND novel AEs |

---

## **Technical Details**

### **Agent 1: Adverse Event Detector**

**File**: `agent_1_adverse_event_detector.py`

**Model**: `claude-sonnet-4-6`

**Prompt Strategy**:
- Provides complete encounter history
- Asks Claude to identify temporal relationships
- Requires structured JSON output
- Includes FDA reporting criteria in prompt

**Output Format**:
```json
{
  "adverse_events": [
    {
      "suspect_drug": {
        "generic_name": "Dupilumab",
        "brand_name": "Dupixent",
        "dose": "300mg",
        "route": "Subcutaneous"
      },
      "adverse_event_description": "Severe bilateral conjunctivitis...",
      "severity": "Serious",
      "outcome": "Recovering",
      "causality": "Probable",
      "timeline": {
        "drug_start_date": "2023-09-05",
        "event_onset_date": "2023-12-25",
        "drug_stop_date": "2024-01-03",
        "time_to_onset": "3.5 months"
      },
      "clinical_evidence": "Patient had no history of conjunctivitis...",
      "fda_reportable": true,
      "fda_criteria_met": ["intervention required"],
      "confidence_level": "High"
    }
  ]
}
```

### **Agent 2: PHI Redactor**

**File**: `agent_1_phi_redactor.py`

**Processing**: 100% local, no API calls

**Methods**:
- `_generate_token()`: SHA256 hashing with random seed
- `_shift_date()`: Consistent date offset per patient
- `redact_patient_data()`: Main redaction function

### **Agent 3: Browser-Tool Form Filler**

**Files**: `browser_use_filler.py` (host) · `agent3_browser_runner.py` (container)
· `case_prompt.py` (shared field mapping)

**Configuration**:
- Model: `claude-sonnet-5` (override via `model=`)
- Tool: the browser-use-demo `browser` tool (Playwright, custom — not an
  Anthropic-defined tool type)
- Max turns: 60 (`--max-turns`); enforced in `api_response_callback`, since
  `sampling_loop` itself runs `while True`
- Max tokens: 8192
- Container: `browser-use-demo-browser-use-1`
  (override via `VIGILANTAI_BROWSER_CONTAINER`)

**Actions used**: `navigate`, `read_page`, `form_input`, `get_page_text`,
`left_click`, `screenshot`. The tool also exposes coordinate actions as a
fallback for elements the DOM cannot address.

**Prompt includes**:
- Step-by-step DOM workflow with an explicit per-page verification step
- De-identified patient demographics
- All detected adverse events with full details
- Encounter summaries
- FDA MedWatch Form 3500 section-by-section reference
- Paths to the read-only case files mounted at `/home/browseruse/case_data/`

**Return contract** (consumed by `simple_orchestrator.py`):
`success`, `error`, `turns_used`, `actions_taken`, `reached_submit`,
`submitted`, `submit_element`.

> `reached_submit=False` on a successful run means the agent finished **without**
> arriving at the Submit page — treat that as "form probably incomplete", not
> success.

**Known limitation**: `browser_use_demo/loop.py` defines
`_maybe_filter_to_n_most_recent_images` but never calls it, so screenshots are
not pruned from context. Leading with DOM calls keeps screenshots rare, but a
long enough form could still grow the context window.

---

## **Safety & Compliance**

✅ **HIPAA Compliant**: All PHI stripped before external API calls

✅ **Human-in-the-Loop**: A code-level submit guard blocks the final Submit click

✅ **Auditable**: All intermediate outputs saved (detected events, redacted data)

✅ **Transparent**: Complete logging of all agent actions

✅ **Reversible**: Human can review and cancel before final submission

---

## **Future Enhancements**

1. **Multi-patient batch processing**: Analyze entire cohorts
2. **Real-time EHR integration**: Connect to live EHR systems
3. **Post-submission tracking**: Monitor FDA case numbers
4. **Feedback loop**: Learn from FDA responses to improve detection
5. **Multi-language support**: International adverse event reporting
