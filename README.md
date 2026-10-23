# Redmargin Career Agent

### The Career Lab, Not the Hype Lab.

**No fake praise. No mystery scores. Just receipts and next steps.**

Redmargin is a voice-enabled career-prep agent for developers. Give it a GitHub repo and a target role, and it tells you what the repo actually proves, where the evidence is thin, and what to fix before you apply.

> Built for the **Amazon Developer Hackathon**.

| | |
|---|---|
| **Live demo** | https://redmargin-alexaplus.onrender.com/career |
| **Source** | https://github.com/KomalDeep355/Redmargin-AlexaPlus |
| **Health check** | https://redmargin-alexaplus.onrender.com/api/health |
| **API docs** | https://redmargin-alexaplus.onrender.com/docs |

> The free-tier server sleeps when idle. If the first load is slow (up to ~50 seconds), open `/api/health` first to wake it.

---

## The Problem

Early-career developers get two kinds of feedback: vague ("improve your projects") or flattering. Neither tells them whether a repo demonstrates what a Data Scientist, NLP, SWE, or FDE employer expects. Repo review, resume feedback, and interview practice also live in separate tools.

## The Solution

One agent, four workflows, grounded in evidence:

| Workflow | What you get |
|---|---|
| **Repository review** | Repo URL + target role: what the repo demonstrates, what's missing, what to be ready to defend in an interview |
| **Career guidance** | Prioritized next steps for a target role |
| **Resume verdict** | Guidance on how well your profile communicates fit for a role |
| **Interview prep** | Practice explaining your projects and role-relevant questions |

Available by **typing or speaking**, and the repo review is also exposed as an **MCP tool** so other AI clients can call it.

**Principles:** evidence over hype, actionable next steps, accessible interaction.

## Supported Roles

SWE, Data Scientist, NLP, Forward Deployed Engineer (FDE)

---

## Architecture

```text
 User (text / voice)
        |
        v
 Web frontend  (static/career.html + career.js, browser Speech APIs)
        |
        v
 FastAPI app  (app/main.py)
   |-- /api/agent/review    repository review
   |-- /api/help            career guidance
   |-- /api/resume-verdict  resume verdict
   |-- /api/interview       interview prep
        |
        v
 LLM provider (mock mode by default)

 MCP client --> MCP server (app/mcp_server.py) --> same review logic
```

| Layer | Tech |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Frontend | HTML, CSS, vanilla JavaScript |
| Voice | Browser SpeechRecognition + SpeechSynthesis |
| Agent tooling | Model Context Protocol (`review_repository`, `get_supported_roles`) |
| AI | Pluggable LLM integration; mock/demo mode |
| Quality | Pytest, GitHub Actions CI |
| Hosting | Render |

## Status: What's Built vs. Planned

**Working**
- Career Agent web UI at `/career`
- Repo review, guidance, resume verdict, and interview endpoints
- Browser voice input and spoken output (browser-dependent)
- MCP server with two tools
- Pytest suite and CI workflow
- Public deployment on Render

**Important: the deployed demo runs in mock/demo mode.** Reviews are demo-quality, not live LLM output. Connecting a real LLM provider is a configuration step (see `.env.example`).

**Not built yet**
- Native Alexa skill (voice today is browser-based only)
- Amazon Bedrock as the model provider
- Private-repo access
- Multi-turn interview sessions with follow-ups

---

## Quick Start

```bash
git clone https://github.com/KomalDeep355/Redmargin-AlexaPlus.git
cd Redmargin-AlexaPlus

python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
cp .env.example .env               # Windows: Copy-Item .env.example .env
```

Fill in only what you need in `.env` (an LLM key and GitHub token are optional; see `.env.example` for exact variable names). Never commit `.env`.

```bash
python -m uvicorn app.main:app --reload --port 8000
```

| URL | What |
|---|---|
| http://127.0.0.1:8000/career | Career Agent |
| http://127.0.0.1:8000/ | Redirects to Career Agent |
| http://127.0.0.1:8000/api/health | Health check |
| http://127.0.0.1:8000/docs | Interactive API docs |

**MCP server** (separate terminal, same venv):

```bash
python -m app.mcp_server           # http://127.0.0.1:8001/mcp
```

## Try It in 60 Seconds

1. Open `/career`.
2. Paste a public GitHub repo URL and pick a target role.
3. Run the review, or click the mic and speak your request (Chrome recommended; allow microphone access).
4. See `/docs` for exact request/response schemas.

---

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Redirects to `/career` |
| `/career` | GET | Career Agent UI |
| `/api/health` | GET | Health and config check |
| `/api/agent/review` | POST | Repository review |
| `/api/help` | POST | Career guidance |
| `/api/resume-verdict` | POST | Resume verdict |
| `/api/interview` | POST | Interview prep |
| `/docs` | GET | Swagger UI |

## MCP Tools

| Tool | Purpose |
|---|---|
| `review_repository` | Review a repo URL against a target role |
| `get_supported_roles` | List supported role categories |

The MCP server is a separate process from the web app. A client must be configured to connect to it, and `127.0.0.1` is only reachable locally.

## Voice

Uses the browser's speech recognition and speech synthesis. Support varies by browser, OS, and microphone permissions, so text input always works as a fallback. This is **not** a native Alexa skill.

---

## Testing

```bash
python -m pytest -q
```

## Deployment (Render)

1. Push the repo to GitHub (no secrets, no `.env`).
2. Render dashboard: **New > Web Service**, select this repo.
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables from `.env.example` in Render's settings.
6. Verify `/`, `/career`, `/api/health`, `/docs` on the public URL.

Free-tier services spin down when idle, so expect a slow first request.

---

## Security & Privacy

- API keys stay server-side, never in frontend JS.
- Inputs are validated; external-API failures don't leak diagnostics.
- Public repos only; no private-repo auth is implemented.
- Automated feedback is guidance, not a hiring decision.

## Limitations

- The deployed demo uses mock mode; real LLM output needs provider credentials and may cost money.
- Without a GitHub token, GitHub API rate limits are low.
- Reviews are limited to what the app can access and process.
- Four roles don't cover every employer expectation.
- Browser speech support is inconsistent.
- No guarantee of interviews or offers.

## Roadmap

- Real LLM-backed reviews (including Amazon Bedrock)
- Native Alexa skill calling `/api/agent/review`
- Feedback that cites specific files and commits
- Deeper role-specific rubrics
- Multi-turn interview practice with follow-ups
- Broader MCP tool coverage and more failure-path tests

## Contributing

Fork, branch, change, run `python -m pytest -q`, check `git status` for stray files or secrets, then open a PR explaining the problem and fix.

## License

See [LICENSE](LICENSE).

---

**Redmargin Career Agent: Build evidence. Understand your gaps. Improve your next step.**
