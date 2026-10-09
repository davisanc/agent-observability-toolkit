# Agent Observability Toolkit

KQL queries for observing AI agents in Azure Application Insights and Log Analytics: **what users asked, which tools the agents called, and what it cost in tokens.** Covers Copilot Studio, Microsoft Foundry agents, and custom agents on Azure Web Apps or Containers. Companion to the Agent 365 PoC Planner.

> **Status: proof of concept.** Built from official Microsoft documentation and the OpenTelemetry GenAI semantic conventions. The queries have **not been run against a live tenant**. Validate in a test workspace first.

**Scope:** observability and cost only. Security detections (prompt injection, sensitive data) are deliberately out of scope; use Microsoft Defender and Microsoft Purview DSPM.

## Use it

- **Interactive page:** open `docs/index.html` (or the GitHub Pages site, if enabled). It has a table-name switch, copy buttons, setup steps and troubleshooting.
- **Plain files:** the `queries/` folder holds every query as a `.kql` file, in two flavours:
  - `queries/appinsights/` for the **Logs blade of an Application Insights resource** (`customEvents`, `dependencies`, `customDimensions`)
  - `queries/loganalytics/` for a **Log Analytics workspace** (`AppEvents`, `AppDependencies`, `Properties`)

Run each flavour in the matching place. They do not mix.

## Setup in short

1. Send agent telemetry to a workspace-based Application Insights resource (page, section 1).
2. Save the four functions from `queries/<flavour>/functions/` as functions with a `lookback:timespan` parameter: `AgentTraces`, `AgentToolCalls`, `AgentTokens`, then `AgentCost`.
3. Edit the price table in `AgentCost` (USD per 1M tokens). Until you do, cost is 0.
4. Run `V1` to confirm which tables exist, and `V3` to see what your telemetry contains.

Note: in the Logs editor a **blank line ends a query**. Keep function definitions and the query in one block, with no blank lines between them.

## Contents

| Group | IDs | Examples |
|---|---|---|
| Verify | V1-V4 | table check, telemetry coverage, agent inventory |
| Prompts | P1-P8 | recent and common prompts, conversation transcript |
| Tool calls | T1-T9 | usage, sequences, failures, slow calls |
| Token usage and cost | K1-K9 | per agent, prompt, user; most expensive prompts |
| Alerts | O1-O6 | token spike, budget, failure regression, runaway turns |

## Rebuild

The page and the `.kql` files are generated from `src/kql_library.py`.

```
python3 src/build_toolkit.py
```

Edit queries in `src/kql_library.py` (they use `{{TOKENS}}` for table and column names so both flavours come from one source), rebuild, and commit the results.

## What each platform can answer

Prompts and responses: all three, when content capture is on. Tool calls: Foundry, custom agents, and Copilot Studio environment-level telemetry (preview). Tokens: Foundry and custom agents that emit `gen_ai.usage.*`. Copilot Studio is licensed on messages or credits, so token cost is generally not available for it.

## License

No license is set yet. Add one before sharing publicly.
