# Luma Take-Home

Modern engineering is about directing leverage — tools, judgment, taste — toward real outcomes. This take-home is designed around that.

Pick a problem. Build something that works. You have \~1 working day.

**You must use AI coding tools** — Claude Code, Cursor, Codex, whatever you prefer. These problems are scoped so that AI is necessary to ship something real in a day. We want to see how you direct the tools: how you plan, how you course-correct, what you accept, and what you push back on.

---

## Choose a Problem

These problems range from pure product to deep platform infrastructure. Pick the one where you'll shine — we'd rather see you in your element than stretching into unfamiliar territory.

These are deliberately open-ended — we want to see what paths you take. For product-leaning problems, we'll focus more on how you thought about the user and the problem space. For infra/platform problems, we'll focus more on architecture and system design. But we care about both in every submission. In all cases, we expect you to have a real opinion on what you built and why — and a mental model of the system in your head. This isn't vibe coding. The AI writes the code; you own the decisions.

### 1. Where (and When) Should We Eat?

A group of people is trying to decide where and when to get lunch or dinner.

**Design and build a product that helps groups reach a decision quickly.**

### 2. Where People and Agents Write Together

Teams write a steady stream of documents — product specs, technical design docs, security reviews, plans — and more and more of that writing is done by AI. But the seam in the middle is ugly: an agent generates a draft in one place, someone pastes it into a separate tool — Google Docs, usually — for humans to edit and review, and authoring and reviewing end up in two disconnected worlds. The version worth building closes that gap into a single loop — AI drafts, AI critiques, people steer and decide.

**Design and build a product where people and their AI agents both author and review documents together.**

### 3. Managing Prompt & Model Behavior in Production

Modern AI products ship by changing behavior — prompts, model routing, tool usage, retrieval strategies, temperature, guardrails, post-processing. Small changes can create big differences in user outcomes.

**Design and build a product that helps a team introduce, evaluate, and manage changes to AI behavior.**

### 4. Build a High-Scale TTS API

Your company wants to offer text-to-speech as an API for voice agent use cases (think real-time streaming from a LLM). Developers will integrate it into real applications and expect it to work reliably. Integrate a real open-source TTS model of your choice.

If successful, usage will be very large — many concurrent clients, sustained throughput, bursty traffic, long-running streams, customers depending on you.

**Design and build a system that could credibly operate in this world.**

---

## Tips

The candidates who do best don't start by building — they start by getting sharp on the problem. It's easy to either throw everything at the wall or get heads-down on making something work, and miss the more important question: *what's actually worth solving here, and for whom?*

Slow down before you write a line of code. The thinking you do upfront will shape everything.

---

## What We're Looking For

We want real, working software — not a prototype, not a toy. You'll likely focus on a slice of the problem, but that slice should actually work and be something you'd put in front of a user or developer. Show polish where it matters to you. Ship a finished product, not a proof of concept.

We expect the result to be better than what an AI would produce on its own with minimal guidance. Your judgment, taste, and direction are what make the difference. Specifically, we're paying attention to:

- **How you approach new problems** — how you break down ambiguity, decide what to tackle first, and make good decisions with incomplete information
- **How you use AI tools** — not just that you used them, but how you directed them, where you pushed back, and where your judgment shaped the result
- **The unique perspective you bring** — the product instincts, technical taste, or domain insight that made your solution distinct from what anyone else would have built

---

## What to Deliver

### 1. Working software

Build your solution directly in this repo. It should run. Include setup instructions that work in a fresh Linux container — we will run your code in one during review. If you use Docker, provide a `docker-compose.yml` for one-command setup.

**If your project is deployable, deploy it.** We want to experience what you built, not just read about it. A live URL — whether it's a web app, an API endpoint, or a hosted service — goes a long way. Vercel, Railway, Fly, a VPS, whatever works. Include the URL in your APPROACH.md.

A `.env.example` is included with stub keys for providers we have accounts with (Anthropic, OpenAI, ElevenLabs, Google Cloud, AWS). Copy it to `.env`, use whichever keys your solution needs, and document any others.

### 2. APPROACH.md

- What you built and why
- Key decisions and tradeoffs
- What you intentionally left out
- What breaks first under pressure
- What you'd build next

### 3. Video walkthrough

Record a short video (\~5 minutes) showing what you built. Demo the key flows — whether that's a UI walkthrough, a CLI session, or hitting your API — explain your decisions, and highlight anything you're particularly proud of. This is your chance to show us the experience through your eyes.

**Paste your video link (Loom, Google Drive, YouTube, etc.) into** `video.md`**.**

### 4. AI session history

Your AI session logs (Claude Code, Codex, Cursor) are packaged automatically when you run `./submit.sh`. If you used other AI tools (ChatGPT, etc.), export those conversations and include them in your repo before submitting.

This is a required deliverable. We review your AI interaction to understand how you work — how you plan, iterate, and direct the tools.

---

## Getting Started

```bash
# 1. Extract the challenge archive you downloaded
tar xzf challenge.tar.gz && cd *eng-take-home*

# 2. Create your own private repo and push to it
git init && git add -A && git commit -m "initial"
gh repo create my-take-home --private --source=. --push

# 3. Copy the env file and fill in any keys you need
cp .env.example .env
```

Now build your solution. Commit and push as you go.

---

## Submitting

When you're ready, run the submit script from your repo root:

```bash
./submit.sh
```

This handles everything: packages your AI session history, commits and pushes your latest changes, grants reviewer access, and registers your submission. You'll see a confirmation when it's done.