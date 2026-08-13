"""
VigilantAI - Agent 4: Real-World Insights Logger

Extracts real-world drug effects - both adverse events and beneficial ones -
and writes a scannable table report.

Beneficial effects matter for risk-benefit assessment: an adverse-event-only
view of a drug is a biased view. Dupilumab causing conjunctivitis and dupilumab
relieving depression are both real-world evidence.
"""

import re
from datetime import date, datetime
from typing import Dict, List, Optional

# Encounters carry medications under several keys; there is no plain
# "medications". Looking only at that key silently found nothing, which is why
# beneficial effects reported 0 despite being present in the data.
MEDICATION_KEYS = (
    "medications_current",
    "medications_prescribed",
    "medications_discontinued",
)

IMPROVEMENT_TERMS = ("improve", "improved", "improvement", "alleviat", "resolved",
                     "better", "reduced", "well-controlled", "well controlled")

# Phrases indicating an event is a later phase of one already recorded, rather
# than a new independent event.
PROGRESSION_TERMS = ("pre-existing", "preexisting", "progression", "progressive",
                     "worsening", "evolved", "evolving", "previously documented",
                     "prior mild", "earlier mild")

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DURATION = re.compile(r"(?:approximately\s+|about\s+|~\s*)?(\d+)(?:\s*-\s*\d+)?\s*(week|month|day|year)s?",
                       re.IGNORECASE)
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30.44, "year": 365.25}


class RealWorldInsightsLogger:
    """Logs adverse events and beneficial effects as a table report."""

    def __init__(self, output_file: str = "real_world_insights.txt"):
        self.output_file = output_file

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _parse_date(value) -> Optional[date]:
        """Pull a date out of a field that may carry trailing prose."""
        match = _ISO_DATE.search(str(value or ""))
        if not match:
            return None
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    def _timing_check(self, ae: Dict) -> Optional[str]:
        """
        Flag narrative timing that contradicts the recorded dates.

        The narrative is what gets typed into MedWatch, and drug-event interval
        is central to causality assessment - so a description saying "6 weeks"
        against dates six months apart is a reportable-quality defect, not a
        cosmetic one.
        """
        timeline = ae.get("timeline") or {}
        start = self._parse_date(timeline.get("drug_start_date"))
        onset = self._parse_date(timeline.get("event_onset_date"))
        if not start or not onset:
            return None

        actual_days = (onset - start).days
        if actual_days <= 0:
            return None

        text = f"{ae.get('adverse_event_description', '')} {timeline.get('time_to_onset', '')}"
        claims = []
        for amount, unit in _DURATION.findall(text):
            claims.append(int(amount) * _UNIT_DAYS[unit.lower()])
        if not claims:
            return None

        # Only flag when every stated duration is far from the computed one, so
        # a correct field alongside a sloppy one does not trip it.
        if all(abs(c - actual_days) > max(30, 0.5 * actual_days) for c in claims):
            months = actual_days / 30.44
            return (f"stated onset interval disagrees with dates "
                    f"({start} to {onset} = {actual_days} days / {months:.1f} months)")
        return None

    @staticmethod
    def _is_continuation(ae: Dict, earlier: List[Dict]) -> Optional[int]:
        """
        Return the index of the event this one continues, if any.

        Agent 2 detects per clinical presentation and has no field for "this is
        the same event, later". Without linking them, one patient's evolving
        event is counted twice, which inflates population-level incidence.
        """
        text = " ".join([
            str(ae.get("adverse_event_description", "")),
            str((ae.get("timeline") or {}).get("time_to_onset", "")),
        ]).lower()
        if not any(term in text for term in PROGRESSION_TERMS):
            return None

        drug = ((ae.get("suspect_drug") or {}).get("generic_name") or "").lower()
        for idx, prior in enumerate(earlier):
            prior_drug = ((prior.get("suspect_drug") or {}).get("generic_name") or "").lower()
            if drug and drug == prior_drug:
                return idx
        return None

    @staticmethod
    def _drug_in_encounter(encounter: Dict, names=("dupilumab", "dupixent")) -> Optional[Dict]:
        """Find a matching medication across every medication key."""
        for key in MEDICATION_KEYS:
            for med in encounter.get(key) or []:
                label = f"{med.get('name', '')} {med.get('brand', '')}".lower()
                if any(name in label for name in names):
                    return med
        return None

    # ------------------------------------------------------------ extraction

    def _extract_beneficial_effects(self, patient_data: Dict) -> List[Dict]:
        """Extract beneficial effects (symptom, mood, quality-of-life gains)."""
        effects: List[Dict] = []

        for encounter in patient_data.get("encounters", []):
            hpi = str(encounter.get("history_of_present_illness", ""))
            diagnoses = (encounter.get("assessment_and_plan") or {}).get("diagnoses", [])
            med = self._drug_in_encounter(encounter)
            if not med:
                continue

            drug_generic = med.get("name", "Dupilumab")
            drug_brand = med.get("brand", "Dupixent")
            visit_date = encounter.get("date", "Unknown")

            # Mood / depression improvement stated in the narrative.
            hpi_lower = hpi.lower()
            if any(t in hpi_lower for t in ("depress", "mood")) and \
                    any(t in hpi_lower for t in IMPROVEMENT_TERMS):
                sentence = next(
                    (s.strip() for s in re.split(r"(?<=[.])\s+", hpi)
                     if ("depress" in s.lower() or "mood" in s.lower())
                     and any(t in s.lower() for t in IMPROVEMENT_TERMS)),
                    "Improvement in mood and depression symptoms",
                )
                effects.append({
                    "type": "beneficial_effect",
                    "drug_generic": drug_generic,
                    "drug_brand": drug_brand,
                    "category": "Mood / depression",
                    "effect": sentence,
                    "magnitude": "Significant",
                    "outcome": "Improved",
                    "observed": visit_date,
                    "source": "history_of_present_illness",
                })

            # Improvements recorded in the assessment/plan diagnoses.
            for diagnosis in diagnoses:
                text = str(diagnosis)
                low = text.lower()
                if not any(t in low for t in IMPROVEMENT_TERMS):
                    continue
                if "depress" in low or "mood" in low:
                    category = "Mood / depression"
                elif "eczema" in low or "dermatitis" in low or "pruritus" in low:
                    category = "Primary indication (eczema)"
                else:
                    category = "Other"
                effects.append({
                    "type": "beneficial_effect",
                    "drug_generic": drug_generic,
                    "drug_brand": drug_brand,
                    "category": category,
                    "effect": text,
                    "magnitude": "Documented",
                    "outcome": "Improved",
                    "observed": visit_date,
                    "source": "assessment_and_plan",
                })

        # De-duplicate on drug + category, keeping the fullest description.
        best: Dict = {}
        for effect in effects:
            key = (effect["drug_generic"].lower(), effect["category"])
            if key not in best or len(effect["effect"]) > len(best[key]["effect"]):
                best[key] = effect
        return list(best.values())

    # --------------------------------------------------------------- writing

    @staticmethod
    def _row(cells: List[str], widths: List[int]) -> str:
        out = []
        for cell, width in zip(cells, widths):
            text = str(cell)
            if len(text) > width:
                text = text[: width - 1] + "…"
            out.append(text.ljust(width))
        return " " + " ".join(out).rstrip()

    def _write_report(self, adverse: List[Dict], beneficial: List[Dict],
                      patient_data: Dict, clusters: Dict, warnings: Dict) -> None:
        widths = [3, 22, 40, 9, 14, 12]
        header = ["#", "Drug", "Effect", "Severity", "Outcome", "Onset"]
        rule = "─" * 104

        with open(self.output_file, "w") as f:
            f.write("═" * 104 + "\n")
            f.write("REAL-WORLD DRUG INSIGHTS\n")
            f.write("═" * 104 + "\n")
            f.write(f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Patient   : "
                    f"{(patient_data.get('patient_demographics') or {}).get('patient_id', 'UNKNOWN')}\n")
            f.write("═" * 104 + "\n\n")

            # ---- adverse events -------------------------------------------
            f.write("ADVERSE EVENTS\n")
            f.write(rule + "\n")
            if adverse:
                f.write(self._row(header, widths) + "\n")
                f.write(rule + "\n")
                for idx, ae in enumerate(adverse, 1):
                    drug = ae.get("suspect_drug") or {}
                    timeline = ae.get("timeline") or {}
                    onset = self._parse_date(timeline.get("event_onset_date"))
                    f.write(self._row([
                        idx,
                        f"{drug.get('generic_name', '?')} ({drug.get('brand_name', '?')})",
                        ae.get("adverse_event_description", ""),
                        ae.get("severity", "?"),
                        ae.get("outcome", "?"),
                        onset.isoformat() if onset else "unknown",
                    ], widths) + "\n")
                    parent = clusters.get(idx)
                    if parent:
                        f.write(f"     └─ continuation of #{parent} "
                                f"(same evolving event, not a separate one)\n")
                    if warnings.get(idx):
                        f.write(f"     ⚠  {warnings[idx]}\n")
            else:
                f.write(" (none detected)\n")
            f.write("\n")

            # ---- beneficial effects ---------------------------------------
            f.write("BENEFICIAL EFFECTS\n")
            f.write(rule + "\n")
            if beneficial:
                f.write(self._row(
                    ["#", "Drug", "Effect", "Magnitude", "Outcome", "Observed"], widths) + "\n")
                f.write(rule + "\n")
                for idx, be in enumerate(beneficial, 1):
                    f.write(self._row([
                        idx,
                        f"{be['drug_generic']} ({be['drug_brand']})",
                        be["effect"],
                        be.get("magnitude", ""),
                        be.get("outcome", ""),
                        be.get("observed", ""),
                    ], widths) + "\n")
                    f.write(f"     └─ {be['category']}\n")
            else:
                f.write(" (none detected)\n")
            f.write("\n")

            # ---- full text ------------------------------------------------
            f.write("DETAIL\n")
            f.write(rule + "\n")
            for idx, ae in enumerate(adverse, 1):
                f.write(f"AE #{idx}: {ae.get('adverse_event_description', '')}\n")
                timeline = ae.get("timeline") or {}
                f.write(f"        time to onset: {timeline.get('time_to_onset', 'unknown')}\n\n")
            for idx, be in enumerate(beneficial, 1):
                f.write(f"BE #{idx}: {be['effect']}\n\n")

            # ---- summary ---------------------------------------------------
            distinct = len(adverse) - len(clusters)
            reportable = sum(1 for ae in adverse
                             if ae.get("fda_reportable") or ae.get("severity") == "Serious")
            f.write("SUMMARY\n")
            f.write(rule + "\n")
            f.write(f" Distinct adverse events  : {distinct}"
                    f"   ({len(adverse)} presentation(s), {len(clusters)} linked as continuations)\n")
            f.write(f" FDA-reportable           : {reportable}\n")
            f.write(f" Beneficial effects       : {len(beneficial)}\n")
            f.write(rule + "\n")
            f.write("Distinct events count evolving presentations once - counting each\n")
            f.write("presentation separately would inflate population-level incidence.\n")
            f.write("═" * 104 + "\n")

    # ------------------------------------------------------------------ main

    def log_insights(self, patient_data: Dict, adverse_events: List[Dict]) -> Dict:
        """Build the insight report. Returns a summary dict for the orchestrator."""
        print("\n📊 Extracting real-world insights...")

        adverse = list(adverse_events or [])

        # Link evolving presentations and flag contradictory timing.
        clusters: Dict[int, int] = {}
        warnings: Dict[int, str] = {}
        for idx, ae in enumerate(adverse, 1):
            parent = self._is_continuation(ae, adverse[: idx - 1])
            if parent is not None:
                clusters[idx] = parent + 1
            warning = self._timing_check(ae)
            if warning:
                warnings[idx] = warning

        beneficial = self._extract_beneficial_effects(patient_data)
        self._write_report(adverse, beneficial, patient_data, clusters, warnings)

        distinct = len(adverse) - len(clusters)
        print(f"✅ Insights logged to: {self.output_file}")
        print(f"   Adverse events    : {distinct} distinct ({len(adverse)} presentations)")
        print(f"   Beneficial effects: {len(beneficial)}")
        for idx, warning in warnings.items():
            print(f"   ⚠  AE #{idx}: {warning}")

        return {
            "success": True,
            "output_file": self.output_file,
            "total_insights": distinct + len(beneficial),
            "adverse_events": distinct,
            "adverse_presentations": len(adverse),
            "beneficial_effects": len(beneficial),
            "timing_warnings": warnings,
        }
