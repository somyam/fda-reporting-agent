"""
VigilantAI - Shared FDA MedWatch case-data rendering.

Paradigm-neutral: renders de-identified case data into the prompt text that
describes WHAT to file. How to drive the browser (DOM refs vs screenshots) is
the caller's concern, so this module contains no automation instructions.
"""

import json
from typing import Dict


def render_case_data(redacted_patient_data: Dict) -> str:
    """
    Render de-identified patient data as FDA MedWatch form content.

    Args:
        redacted_patient_data: Agent 1 output, optionally carrying Agent 2's
            `adverse_events_detected` list.

    Returns:
        Prompt section describing the patient, the adverse events, and the
        encounter history. Contains no PHI - Agent 1 has already redacted it.
    """
    demographics = redacted_patient_data.get("patient_demographics", {})
    encounters = redacted_patient_data.get("encounters", [])
    adverse_events = redacted_patient_data.get("adverse_events_detected", [])

    out = f"""PATIENT INFORMATION (DE-IDENTIFIED):
- Patient Identifier: {demographics.get('patient_id', 'UNKNOWN')}
- Age: {demographics.get('age', 'UNKNOWN')} years
- Sex: {demographics.get('sex', 'UNKNOWN')}
- Weight: {demographics.get('weight', 'UNKNOWN')}

ADVERSE EVENT(S) DETECTED:
"""

    if adverse_events:
        for idx, ae in enumerate(adverse_events, 1):
            drug = ae.get("suspect_drug", {})
            timeline = ae.get("timeline", {})
            out += f"\n--- Adverse Event #{idx} ---\n"
            out += f"Suspect Drug: {drug.get('generic_name', 'Unknown')} ({drug.get('brand_name', 'Unknown')})\n"
            out += f"Event Description: {ae.get('adverse_event_description', 'Unknown')}\n"
            out += f"Severity: {ae.get('severity', 'Unknown')}\n"
            out += f"Outcome: {ae.get('outcome', 'Unknown')}\n"
            out += f"Causality: {ae.get('causality', 'Unknown')}\n"
            out += "Timeline:\n"
            out += f"  - Drug Started: {timeline.get('drug_start_date', 'Unknown')}\n"
            out += f"  - Event Onset: {timeline.get('event_onset_date', 'Unknown')}\n"
            out += f"  - Drug Stopped: {timeline.get('drug_stop_date') or 'Ongoing'}\n"
            out += f"  - Time to Onset: {timeline.get('time_to_onset', 'Unknown')}\n"
            if ae.get("fda_criteria_met"):
                out += f"FDA Criteria Met: {', '.join(ae['fda_criteria_met'])}\n"
            out += f"\nClinical Evidence: {ae.get('clinical_evidence', 'N/A')}\n"
    else:
        out += "\n(No adverse events detected)\n"

    out += "\nCLINICAL ENCOUNTERS:\n"

    for i, encounter in enumerate(encounters, 1):
        out += f"\nVisit {i} - {encounter.get('date', 'Date Unknown')}"
        if encounter.get("visit_type"):
            out += f" ({encounter['visit_type']})"
        out += f"\n  Chief Complaint: {encounter.get('chief_complaint', 'N/A')}\n"

        if encounter.get("history_of_present_illness"):
            out += f"  History: {encounter['history_of_present_illness']}\n"

        # Section C of Form 3500 wants dose/frequency/route/therapy dates and
        # lot/NDC when available, so carry the full medication record through.
        for key, label in (
            ("medications_prescribed", "Prescribed"),
            ("medications_current", "Current"),
            ("medications_discontinued", "Discontinued"),
        ):
            for med in encounter.get(key) or []:
                parts = [str(med.get("name", "Unknown"))]
                for field in ("brand", "strength", "form", "dose", "frequency", "route"):
                    if med.get(field):
                        parts.append(f"{field}={med[field]}")
                out += f"  {label}: {', '.join(parts)}\n"
                for field in (
                    "start_date", "last_dose_date", "indication",
                    "reason_for_discontinuation", "lot_number", "ndc", "manufacturer",
                ):
                    if med.get(field):
                        out += f"    {field}: {med[field]}\n"

        if encounter.get("assessment_and_plan"):
            out += f"  Assessment/Plan: {json.dumps(encounter['assessment_and_plan'])}\n"

        if encounter.get("outcome"):
            out += f"  Outcome: {encounter['outcome']}\n"

    return out


FORM_SECTION_REFERENCE = """FDA MEDWATCH FORM 3500 SECTIONS:
SECTION A - PATIENT INFORMATION
- Patient Identifier (use the de-identified ID above)
- Age at time of event
- Sex
- Weight

SECTION B - ADVERSE EVENT OR PRODUCT PROBLEM
- Describe the adverse event or product problem
- Include outcomes (death, hospitalization, disability, etc.)
- Event date

SECTION C - SUSPECT PRODUCT(S)
- Product name (generic and brand)
- Dose, frequency, and route
- Therapy dates (start and stop)
- Diagnosis for use
- Lot number and NDC if available

SECTION D - SUSPECT MEDICAL DEVICE (if applicable)
- Leave blank for drug events

SECTION E - REPORTER INFORMATION
- Use "Automated System" or "VigilantAI" for reporter name
- Leave contact information blank or use placeholder

SECTION F - CONCOMITANT MEDICAL PRODUCTS
- List other medications patient was taking

SECTION G - OTHER RELEVANT HISTORY
- Include relevant medical history and lab data

FIELD CONVENTIONS:
- Use the de-identified patient ID where patient name is requested
- For dates, use the format DD-MMM-YYYY (e.g., 15-JAN-2024)
- If a field asks for information not provided above, leave it blank or mark "Unknown"
"""
