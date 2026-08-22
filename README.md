##Autonomous adverse event detection and FDA MedWatch reporting.

I built a multi-agent, containerized pipeline that detects adverse drug events (AEs) directly from raw clinical encounter data, built at the Abridge/Anthropic/Lightspeed agentic healthcare AI hackathon.

FDA adverse event (AE) reporting is currently manual. Nurses have to read encounter notes, extract the relevant fields, and file the report by hand. Nurses are often untrained on how to submit this report, and manually submitting it takes 10-15 minutes. That friction leads to under-reporting — at best, lost signal about drug safety; at worst, illegal failure to report harm. An estimated 90-99% adverse events go unreported. 

My pipeline reads the clinical encounter, de-identifies it, classifies severe, mandatory AEs and auto-fills FDA MedWatch Form 3500, with a read-back verification step so the agent confirms extracted values against the source text before anything is submitted. Mild-to-moderate or beneficial side effects are dumped into a txt file as Real World Insights for pharma diligence. 

I used Claude for reasoning over the clinical encounter and classification; Playwright and browser-use agent for the auto-fill/submission with read-back verification.

Impact: Autonomous FDA reporting to allow nurses to focus on patient care.
 

<video src="https://github.com/user-attachments/assets/c2928036-4c99-4a94-bee0-d489ce76d17c" autoplay loop muted playsinline width="100%"></video>

