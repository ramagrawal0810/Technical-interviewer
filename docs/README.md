# Technical Interviewer: Documentation

A text-based AI interviewer for technical roles. It asks role-specific questions, probes weak or vague answers with follow-ups,
scores every answer on four dimensions with evidence from the candidate's own words, and produces a report and a hiring
recommendation that a human interviewer reviews and can override. It is a single LLM-driven application (OpenAI chat models), not a
multi-agent system.

Scope: **text interviews only.** Speech-to-text and text-to-speech code (`backend/app/services/stt_service.py`, `tts_service.py`,
`backend/app/api/audio.py`, `frontend/audio_utils.py`, `frontend/voice_mode_phase4.py`) remains in the repository from an earlier phase;
the current interface does not use it and nothing in these documents or the evaluation covers audio or video.

## Who it is for
- **Interviewer / hiring team:** schedules interviews, reviews reports, accepts or overrides the AI recommendation.
- **Candidate:** reads a data-handling notice, consents, answers questions in the browser, can end the interview early.
- **Developer / reviewer:** runs, tests and extends the system.

## Main capabilities
| Capability | Where to read more |
|---|---|
| Three interview modes: Question Bank, CV-Driven, Hybrid | [FEATURES_AND_CODE_MAP.md](FEATURES_AND_CODE_MAP.md) |
| Adaptive follow-ups (at most 3 per main question), difficulty progression | [FEATURES_AND_CODE_MAP.md](FEATURES_AND_CODE_MAP.md) |
| 4-dimension scoring with verbatim evidence quotes, recommendation and confidence | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Consent, abandonment, data retention | [DATA_HANDLING.md](DATA_HANDLING.md), [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) |
| Prompt-injection detection (heuristic) and prompt hardening | [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) |
| Human review: accept or override, stored separately from the AI result | [FEATURES_AND_CODE_MAP.md](FEATURES_AND_CODE_MAP.md) |
| PDF and Markdown feedback reports, LLM usage and cost dashboard | [FEATURES_AND_CODE_MAP.md](FEATURES_AND_CODE_MAP.md) |

## Document set
| Document | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, flows and the code behind each |
| [TECHNOLOGY_STACK.md](TECHNOLOGY_STACK.md) | Libraries, verified versions, why chosen, alternatives |
| [FEATURES_AND_CODE_MAP.md](FEATURES_AND_CODE_MAP.md) | Feature to file map, with implementation and test status |
| [SETUP_AND_OPERATIONS.md](SETUP_AND_OPERATIONS.md) | Windows setup, run, seed, test, troubleshoot |
| [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md) | Security as it actually exists, gaps and hardening |
| [DATA_HANDLING.md](DATA_HANDLING.md) | What is stored, who can see it, retention (checked against the code by a test) |
| [TESTING_AND_EVALUATION.md](TESTING_AND_EVALUATION.md) | The four client evaluation requirements, evidence and status |
| [KNOWN_LIMITATIONS_AND_ROADMAP.md](KNOWN_LIMITATIONS_AND_ROADMAP.md) | Current limits and prioritised future work |
| [CLIENT_HANDOVER.md](CLIENT_HANDOVER.md) | Short handover guide and demo script |
| [archive/README.md](archive/README.md) | Index of archived historical build reports |

The client evaluation deliverables are generated into `evaluation/` and `evaluation/client_documents/` (see [TESTING_AND_EVALUATION.md](TESTING_AND_EVALUATION.md)).

## Prerequisites (verified in this repository)
- Windows, PowerShell, Python 3.14 (both virtual environments report 3.14.7). There is no Node.js component: the frontend is Streamlit.
- An OpenAI API key with credit for the interview features (tests do not need one).
- Details: [SETUP_AND_OPERATIONS.md](SETUP_AND_OPERATIONS.md).

## Known limitations (summary)
No real authentication (a shared demo passcode gates the interviewer pages; the API itself is open); no encryption at rest; no automatic data
deletion; the prompt-injection detector is a pattern list; locking is in-process only; the evaluator runs at temperature 0.7 so scores vary
slightly between runs; independent human agreement with the AI is **not verified**. Full list: [KNOWN_LIMITATIONS_AND_ROADMAP.md](KNOWN_LIMITATIONS_AND_ROADMAP.md).
