"""
VigilantAI - Agent 1: Adverse Event Detector
Uses Claude to autonomously identify drug-adverse event relationships from patient encounters
"""

import json
import os
from typing import Dict, List
from anthropic import Anthropic


class AdverseEventDetector:
    """
    Analyzes patient encounter data to identify adverse drug events
    Uses Claude to detect temporal relationships, drug discontinuations, and known AE patterns
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        self.client = Anthropic(api_key=self.api_key)
        self.model = "claude-sonnet-5"

    def detect_adverse_events(self, patient_data: Dict) -> Dict:
        """
        Main detection function - analyzes patient encounters to identify adverse events

        Args:
            patient_data: Patient profile with encounters, medications, symptoms

        Returns:
            Dict with detected adverse events and structured data
        """

        # Extract patient demographics and encounters
        demographics = patient_data.get("patient_demographics", {})
        encounters = patient_data.get("encounters", [])

        if not encounters:
            print("⚠️  No encounters found in patient data")
            return {
                "adverse_events_detected": [],
                "has_reportable_event": False
            }

        # Prepare encounter summary for Claude
        encounter_summary = self._prepare_encounter_summary(encounters)

        # Call Claude to analyze encounters
        adverse_events = self._analyze_with_claude(demographics, encounter_summary)

        # Determine if any events are reportable
        has_reportable = any(
            ae.get("severity") == "Serious" or
            ae.get("fda_reportable", False)
            for ae in adverse_events
        )

        result = {
            "patient_demographics": demographics,
            "encounters": encounters,
            "adverse_events_detected": adverse_events,
            "has_reportable_event": has_reportable,
            "detection_metadata": {
                "model_used": self.model,
                "total_encounters_analyzed": len(encounters),
                "total_adverse_events_found": len(adverse_events)
            }
        }

        # Print results
        print("\n" + "=" * 80)
        print("DETECTION RESULTS:")
        print("=" * 80)

        if adverse_events:
            print(f"\n✅ Found {len(adverse_events)} adverse event(s)\n")
            for idx, ae in enumerate(adverse_events, 1):
                print(f"┌─ Adverse Event #{idx} ───────────────────────────────────────┐")
                print(f"│")
                print(f"│ 💊 SUSPECT DRUG:")
                print(f"│    Generic: {ae['suspect_drug']['generic_name']}")
                print(f"│    Brand:   {ae['suspect_drug']['brand_name']}")
                print(f"│")
                print(f"│ ⚠️  ADVERSE EVENT:")
                print(f"│    Description: {ae['adverse_event_description']}")
                print(f"│    Severity:    {ae['severity']}")
                print(f"│    Outcome:     {ae['outcome']}")
                print(f"│")
                print(f"│ 📅 TIMELINE:")
                print(f"│    Drug Started:  {ae['timeline']['drug_start_date']}")
                print(f"│    Event Onset:   {ae['timeline']['event_onset_date']}")
                print(f"│    Drug Stopped:  {ae['timeline'].get('drug_stop_date', 'Ongoing')}")
                print(f"│    Time to Onset: {ae['timeline']['time_to_onset']}")
                print(f"│")
                print(f"│ 🎯 CONFIDENCE: {ae['confidence_level']}")
                print(f"│    FDA Reportable: {'YES' if ae.get('fda_reportable') else 'NO'}")
                print(f"│")
                print(f"└──────────────────────────────────────────────────────────────┘")
        else:
            print("\n❌ No adverse drug events detected")

        return result

    def _prepare_encounter_summary(self, encounters: List[Dict]) -> str:
        """
        Create a concise summary of encounters for Claude analysis
        """
        summary = []
        for idx, enc in enumerate(encounters, 1):
            encounter_text = f"\nVisit {idx} - {enc.get('date', 'Unknown Date')}"
            if enc.get('visit_type'):
                encounter_text += f" ({enc['visit_type']})"
            encounter_text += f"\n  Chief Complaint: {enc.get('chief_complaint', 'N/A')}\n"

            if enc.get('history_of_present_illness'):
                encounter_text += f"  History: {enc['history_of_present_illness']}\n"

            # Medications are split across three keys depending on status - all
            # three matter for causality (what started it, what was stopped, when).
            for key, label in (
                ("medications_prescribed", "Prescribed"),
                ("medications_current", "Current"),
                ("medications_discontinued", "Discontinued"),
            ):
                for med in enc.get(key) or []:
                    parts = [str(med.get('name', 'Unknown'))]
                    for field in ("brand", "strength", "form", "dose", "frequency", "route"):
                        if med.get(field):
                            parts.append(f"{field}={med[field]}")
                    encounter_text += f"  {label}: {', '.join(parts)}\n"
                    for field in ("start_date", "last_dose_date", "indication",
                                  "reason_for_discontinuation", "lot_number", "ndc",
                                  "manufacturer"):
                        if med.get(field):
                            encounter_text += f"    {field}: {med[field]}\n"

            if enc.get('physical_exam'):
                encounter_text += f"  Physical Exam: {json.dumps(enc['physical_exam'])}\n"

            if enc.get('assessment_and_plan'):
                encounter_text += f"  Assessment/Plan: {json.dumps(enc['assessment_and_plan'])}\n"

            if enc.get('labs_resulted'):
                encounter_text += f"  Labs: {json.dumps(enc['labs_resulted'])}\n"

            if enc.get('referrals'):
                encounter_text += f"  Referrals: {json.dumps(enc['referrals'])}\n"

            if enc.get('outcome'):
                encounter_text += f"  Outcome: {enc['outcome']}\n"

            summary.append(encounter_text)

        return "".join(summary)

    def _analyze_with_claude(self, demographics: Dict, encounter_summary: str) -> List[Dict]:
        """
        Use Claude to analyze encounters and identify adverse drug events
        """

        analysis_prompt = f"""You are a medical AI specialized in pharmacovigilance and adverse drug event detection.

Analyze the following patient encounter data to identify potential adverse drug events (ADEs).

PATIENT DEMOGRAPHICS:
- Age: {demographics.get('age', 'Unknown')}
- Sex: {demographics.get('sex', 'Unknown')}

ENCOUNTER HISTORY:
{encounter_summary}

TASK:
Identify any adverse drug events by looking for:

1. **Temporal Relationships**: New symptoms appearing after medication initiation
   - ANY new symptom within days/weeks/months of drug start
   - Does NOT need to be a "known" adverse event

2. **Drug Discontinuations**: Medications stopped due to side effects or worsening conditions
   - Look at provider notes for reason for discontinuation

3. **Worsening Conditions**: Patient complaints that got worse after starting a medication
   - Compare symptom severity across visits

4. **Known Drug-AE Patterns**: Common adverse events for specific medications
   - Examples: Dupixent → conjunctivitis, statins → myalgia, ACE inhibitors → cough
   - BUT ALSO detect rare/novel events not in literature

IMPORTANT:
- Report BOTH known AND unknown/rare adverse events
- Focus on temporal correlation, not just documented patterns
- A new symptom after drug initiation is suspicious regardless of whether it's a "known" side effect
- Rare events may be the most important to detect and report to FDA

For each adverse event identified, assess:
- **Severity**: Mild, Moderate, Serious (use FDA criteria: death, life-threatening, hospitalization, disability, intervention required)
- **Causality**: Definite, Probable, Possible, Unlikely
- **FDA Reportable**: Whether it meets FDA MedWatch reporting criteria

OUTPUT FORMAT (JSON):
{{
  "adverse_events": [
    {{
      "suspect_drug": {{
        "generic_name": "string",
        "brand_name": "string",
        "dose": "string",
        "route": "string"
      }},
      "adverse_event_description": "string (detailed description of the adverse event)",
      "severity": "Mild|Moderate|Serious",
      "outcome": "Recovered|Recovering|Not Recovered|Fatal|Unknown",
      "causality": "Definite|Probable|Possible|Unlikely",
      "timeline": {{
        "drug_start_date": "YYYY-MM-DD",
        "event_onset_date": "YYYY-MM-DD (or estimated)",
        "drug_stop_date": "YYYY-MM-DD or null if ongoing",
        "time_to_onset": "string (e.g., '2 weeks after drug initiation')"
      }},
      "clinical_evidence": "string (explain why this is an ADE - symptoms, timing, drug discontinuation, etc.)",
      "fda_reportable": true|false,
      "fda_criteria_met": ["list of criteria if reportable: death, life-threatening, hospitalization, disability, intervention required"],
      "confidence_level": "High|Medium|Low"
    }}
  ]
}}

IMPORTANT:
- Only report events where there is clear temporal relationship or clinical evidence
- Be conservative - don't report unrelated symptoms as ADEs
- If no adverse events are detected, return empty "adverse_events" array
- Focus on serious and medically significant events
"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": analysis_prompt
                }]
            )

            # Extract JSON from response
            # Handle both text blocks and thinking blocks
            response_text = ""
            for block in response.content:
                if hasattr(block, 'text'):
                    response_text += block.text

            # Try to parse JSON
            # Look for JSON block in response
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                analysis_result = json.loads(json_match.group())
            else:
                # If no JSON found, try parsing entire response
                analysis_result = json.loads(response_text)

            adverse_events = analysis_result.get("adverse_events", [])

            print(f"Claude analysis complete - {len(adverse_events)} event(s) found")

            return adverse_events

        except json.JSONDecodeError as e:
            print(f"❌ Error parsing Claude response: {e}")
            print(f"Response text: {response_text[:500]}...")
            return []

        except Exception as e:
            print(f"❌ Error calling Claude API: {e}")
            return []


if __name__ == "__main__":
    # Test with sample patient data
    print("\n" + "=" * 80)
    print("TESTING ADVERSE EVENT DETECTOR")
    print("=" * 80)

    # Load patient data
    with open("patient_adverse_event_case.json", "r") as f:
        patient_data = json.load(f)

    # Remove the adverse_event_summary to test autonomous detection
    if "adverse_event_summary" in patient_data:
        print("\n⚠️  Removing pre-filled adverse_event_summary to test autonomous detection")
        del patient_data["adverse_event_summary"]

    # Run detector
    detector = AdverseEventDetector()
    result = detector.detect_adverse_events(patient_data)

    # Save result
    output_file = "adverse_events_detected.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\n✅ Results saved to: {output_file}")
