# KQL library for the Agent Observability Toolkit.
# Queries use {{TOKENS}} so the same text renders for two schemas:
#   classic  -> Application Insights "Logs" blade   (customEvents, dependencies, customDimensions ...)
#   ws       -> Log Analytics workspace / Sentinel   (AppEvents, AppDependencies, Properties ...)

SCHEMAS = {
    "classic": {
        "DEPS": "dependencies", "EVENTS": "customEvents", "TS": "timestamp", "PROPS": "customDimensions",
        "NAME": "name", "ROLE": "cloud_RoleName", "ROLEINST": "cloud_RoleInstance", "OPID": "operation_Id",
        "USERID": "user_Id", "AUTHUSER": "user_AuthenticatedId", "DUR": "duration", "OK": "success", "RES": "resultCode",
    },
    "ws": {
        "DEPS": "AppDependencies", "EVENTS": "AppEvents", "TS": "TimeGenerated", "PROPS": "Properties",
        "NAME": "Name", "ROLE": "AppRoleName", "ROLEINST": "AppRoleInstance", "OPID": "OperationId",
        "USERID": "UserId", "AUTHUSER": "UserAuthenticatedId", "DUR": "DurationMs", "OK": "Success", "RES": "ResultCode",
    },
}


def render(text, variant):
    out = text
    for k, v in SCHEMAS[variant].items():
        out = out.replace("{{" + k + "}}", v)
    assert "{{" not in out, "unreplaced token in: " + out[:200]
    return out


# ------------------------------------------------------------------ functions
FN_TRACES = r'''let AgentTraces = (lookback:timespan) {
    // Operations that carry prompt / response text. Add "chat" if your framework only emits model-call spans.
    let traceOps = dynamic(["invoke_agent", "output_messages"]);
    // cloud_RoleName values of agents YOU host on Web Apps / Containers (everything else with gen_ai spans is labelled Foundry)
    let customAgentRoles = dynamic(["my-custom-agent-app"]);
    // ---------- Copilot Studio, agent-level telemetry: one row per message ----------
    let csLegacy =
        {{EVENTS}}
        | where {{TS}} > ago(lookback)
        | where {{NAME}} in ("BotMessageReceived", "BotMessageSend")
        | project
            timestamp      = {{TS}},
            Source         = "Copilot Studio",
            Agent          = {{ROLEINST}},
            Direction      = iff({{NAME}} == "BotMessageReceived", "Prompt", "Response"),
            Text           = tostring({{PROPS}}.text),
            ConversationId = tostring({{PROPS}}.conversationId),
            Channel        = tostring({{PROPS}}.channelId),
            Model          = "",
            User           = coalesce(tostring({{PROPS}}.fromName), tostring({{PROPS}}.fromId), {{USERID}}),
            OperationId    = {{OPID}};
    // ---------- GenAI OpenTelemetry spans: Copilot Studio (environment-level), Foundry, custom agents ----------
    let genAi =
        {{DEPS}}
        | where {{TS}} > ago(lookback)
        | extend Op = tostring({{PROPS}}["gen_ai.operation.name"])
        | where Op in (traceOps)
        | extend inMsgs  = todynamic(tostring({{PROPS}}["gen_ai.input.messages"])),
                 outMsgs = todynamic(tostring({{PROPS}}["gen_ai.output.messages"]))
        | extend lastIn   = inMsgs[array_length(inMsgs) - 1],
                 firstOut = outMsgs[0]
        | extend PromptText = coalesce(
                     tostring(lastIn.parts[0].content),                    // semconv: parts[].content
                     tostring(lastIn.content),                             // flat {role, content}
                     tostring({{PROPS}}["gen_ai.prompt"]),                 // older semconv
                     tostring({{PROPS}}["gen_ai.input.messages"])),        // raw fallback
                 ResponseText = coalesce(
                     tostring(firstOut.parts[0].content),
                     tostring(firstOut.content),
                     tostring({{PROPS}}["gen_ai.completion"]),
                     tostring({{PROPS}}["gen_ai.output.messages"]))
        | extend rows = pack_array(
                     bag_pack("Direction", "Prompt",   "Text", PromptText),
                     bag_pack("Direction", "Response", "Text", ResponseText))
        | mv-expand rows
        | project
            timestamp      = {{TS}},
            Source         = iff(isnotempty(tostring({{PROPS}}["microsoft.channel.name"])), "Copilot Studio",
                                 iff({{ROLE}} in (customAgentRoles), "Custom", "Foundry")),
            Agent          = coalesce(tostring({{PROPS}}["gen_ai.agent.name"]), {{ROLE}}),
            Direction      = tostring(rows.Direction),
            Text           = tostring(rows.Text),
            ConversationId = coalesce(tostring({{PROPS}}["gen_ai.conversation.id"]), {{OPID}}),
            Channel        = coalesce(tostring({{PROPS}}["microsoft.channel.name"]), Op),
            Model          = tostring({{PROPS}}["gen_ai.request.model"]),
            User           = coalesce(tostring({{PROPS}}["user.email"]), tostring({{PROPS}}["enduser.id"]), {{AUTHUSER}}, {{USERID}}),
            OperationId    = {{OPID}};
    union csLegacy, genAi
    | where isnotempty(Text)
};'''

FN_TOOLS = r'''let AgentToolCalls = (lookback:timespan) {
    {{DEPS}}
    | where {{TS}} > ago(lookback)
    | where tostring({{PROPS}}["gen_ai.operation.name"]) == "execute_tool"
    | project
        timestamp      = {{TS}},
        Agent          = coalesce(tostring({{PROPS}}["gen_ai.agent.name"]), {{ROLE}}),
        Tool           = coalesce(tostring({{PROPS}}["gen_ai.tool.name"]), {{NAME}}),
        ToolType       = tostring({{PROPS}}["gen_ai.tool.type"]),
        Arguments      = tostring({{PROPS}}["gen_ai.tool.call.arguments"]),
        Result         = tostring({{PROPS}}["gen_ai.tool.call.result"]),
        Succeeded      = tobool({{OK}}),
        DurationMs     = todouble({{DUR}}),
        ResultCode     = tostring({{RES}}),
        ConversationId = coalesce(tostring({{PROPS}}["gen_ai.conversation.id"]), {{OPID}}),
        User           = coalesce(tostring({{PROPS}}["user.email"]), tostring({{PROPS}}["enduser.id"]), {{AUTHUSER}}, {{USERID}}),
        OperationId    = {{OPID}}
};'''


FN_TOKENS = r'''let AgentTokens = (lookback:timespan) {
    // Which span type carries token usage in your stack.
    //   "chat"         = one span per model call (Foundry, Semantic Kernel, most frameworks)
    //   "invoke_agent" = use this instead if usage is only reported on the agent run span
    // Never list both: a parent span and its child spans can report the same tokens and you would double count.
    let tokenOps = dynamic(["chat"]);
    {{DEPS}}
    | where {{TS}} > ago(lookback)
    | extend Op = tostring({{PROPS}}["gen_ai.operation.name"])
    | where Op in (tokenOps)
    | extend InTok  = tolong({{PROPS}}["gen_ai.usage.input_tokens"]),
             OutTok = tolong({{PROPS}}["gen_ai.usage.output_tokens"])
    | where isnotnull(InTok) or isnotnull(OutTok)
    | project
        timestamp      = {{TS}},
        Agent          = coalesce(tostring({{PROPS}}["gen_ai.agent.name"]), {{ROLE}}),
        Model          = coalesce(tostring({{PROPS}}["gen_ai.response.model"]), tostring({{PROPS}}["gen_ai.request.model"])),
        Operation      = Op,
        InputTokens    = coalesce(InTok, long(0)),
        OutputTokens   = coalesce(OutTok, long(0)),
        TotalTokens    = coalesce(InTok, long(0)) + coalesce(OutTok, long(0)),
        DurationMs     = todouble({{DUR}}),
        Succeeded      = tobool({{OK}}),
        ConversationId = coalesce(tostring({{PROPS}}["gen_ai.conversation.id"]), {{OPID}}),
        User           = coalesce(tostring({{PROPS}}["user.email"]), tostring({{PROPS}}["enduser.id"]), {{AUTHUSER}}, {{USERID}}),
        OperationId    = {{OPID}}
};'''

FN_COST = r'''let AgentCost = (lookback:timespan) {
    // EDIT THIS TABLE: US dollars per 1,000,000 tokens, copied from your own price card or contract.
    // ModelKey is the model name without the trailing date, e.g. gpt-4o-2024-08-06 is matched as gpt-4o.
    // Run query K8 to see the exact model names in your telemetry.
    let prices = datatable(ModelKey:string, InputPer1M:real, OutputPer1M:real) [
        "gpt-4o",      0.0, 0.0,
        "gpt-4o-mini", 0.0, 0.0,
        "gpt-4.1",     0.0, 0.0
    ];
    AgentTokens(lookback)
    | extend ModelKey = replace_regex(Model, @"-\d{4}-\d{2}-\d{2}$", "")
    | lookup kind=leftouter prices on ModelKey
    | extend PriceMissing = isnull(InputPer1M)
    | extend InputCost  = round(InputTokens  / 1000000.0 * coalesce(InputPer1M, 0.0), 6),
             OutputCost = round(OutputTokens / 1000000.0 * coalesce(OutputPer1M, 0.0), 6)
    | extend Cost = InputCost + OutputCost
    | project-away InputPer1M, OutputPer1M
};'''

FUNCS = [  # (key, name, text, blurb)
    ("traces", "AgentTraces", FN_TRACES),
    ("tools", "AgentToolCalls", FN_TOOLS),
    ("tokens", "AgentTokens", FN_TOKENS),
    ("cost", "AgentCost", FN_COST),
]

# ------------------------------------------------------------------ queries
# Each: id, title, desc, q, platforms, notes[]; alerts also carry rule{}
ALL3 = ["Copilot Studio", "Foundry", "Custom"]
SPANS = ["Copilot Studio (span telemetry)", "Foundry", "Custom"]
TOK = ["Foundry", "Custom", "Copilot Studio (if tokens are emitted)"]

VERIFY = [
    dict(id="V1", title="Which tables do I have here? (works in any Logs window)", platforms=ALL3,
         desc="Run this before anything else. It names all four possible tables and silently skips the ones that do not exist in the window you are in, so it never fails with an unresolved-table error. The result tells you which table-name switch to use.",
         q=r'''union isfuzzy=true customEvents, dependencies, AppEvents, AppDependencies
| summarize Rows = count() by Table = $table
| order by Table asc''',
         notes=["Rows for customEvents / dependencies: you are in the classic schema. Use the Application Insights switch.",
                "Rows for AppEvents / AppDependencies: you are in a workspace schema. Use the Log Analytics / Sentinel switch.",
                "No rows: nothing was ingested in the time range of the Logs window. Set it to 24 hours or longer and check section 1."]),
    dict(id="V2", title="Is telemetry flowing?", platforms=ALL3,
         desc="Which event names and GenAI operations arrived in the last hour, so you know which of the queries below can return data for you.",
         q=r'''union {{EVENTS}}, {{DEPS}}
| where {{TS}} > ago(1h)
| extend Op = tostring({{PROPS}}["gen_ai.operation.name"])
| where {{NAME}} startswith "Bot" or isnotempty(Op)
| summarize Records = count(), Agents = dcount(coalesce(tostring({{PROPS}}["gen_ai.agent.name"]), {{ROLE}})) by Table = $table, EventName = {{NAME}}, Op
| order by Records desc''',
         notes=["Only BotMessage* rows: agent-level Copilot Studio telemetry. You get prompts and responses, but tool calls and tokens need span-based telemetry.",
                "Op = invoke_agent / execute_tool / output_messages / chat: span-based telemetry is flowing."]),
    dict(id="V3", title="What does my telemetry actually contain?", platforms=SPANS,
         desc="For every agent and span type: how many spans carry prompt text, tool arguments, tool results and token counts. Use it to see which questions your current setup can answer, and which setting to change when one comes back as zero.",
         q=r'''{{DEPS}}
| where {{TS}} > ago(1d)
| extend Op = tostring({{PROPS}}["gen_ai.operation.name"])
| where isnotempty(Op)
| summarize Spans = count(),
            WithPromptText  = countif(isnotempty(tostring({{PROPS}}["gen_ai.input.messages"]))),
            WithToolArgs    = countif(isnotempty(tostring({{PROPS}}["gen_ai.tool.call.arguments"]))),
            WithToolResult  = countif(isnotempty(tostring({{PROPS}}["gen_ai.tool.call.result"]))),
            WithTokenCounts = countif(isnotempty(tostring({{PROPS}}["gen_ai.usage.input_tokens"])))
            by Agent = coalesce(tostring({{PROPS}}["gen_ai.agent.name"]), {{ROLE}}), Op
| order by Agent asc, Op asc''',
         notes=["WithPromptText = 0: content capture is off (section 1), so you see structure and tokens but not text.",
                "WithTokenCounts is 0 on invoke_agent but above 0 on chat: leave tokenOps = chat in AgentTokens. The reverse: change it to invoke_agent.",
                "WithTokenCounts = 0 everywhere for an agent: that platform does not emit usage. Cost for it cannot be computed from traces."]),
    dict(id="V4", title="Agent inventory from telemetry", platforms=ALL3,
         desc="Every agent that produced telemetry in 30 days, when it was last seen and how busy it is. Compare against your Agent 365 registry to find agents that exist but are not observed.",
         q=r'''AgentTraces(30d)
| summarize FirstSeen = min(timestamp), LastSeen = max(timestamp),
            Conversations = dcount(ConversationId), Prompts = countif(Direction == "Prompt"),
            Users = dcount(User) by Source, Agent
| order by LastSeen desc''',
         notes=["Agent names come from gen_ai.agent.name, falling back to the role name (Foundry/custom) or role instance (Copilot Studio agent-level)."]),
]

PROMPTS = [
    dict(id="P1", title="Recent prompts feed", platforms=ALL3,
         desc="The latest prompts across every agent: who asked what, through which channel. The simplest answer to 'what are people asking our agents?'.",
         q=r'''AgentTraces(1d)
| where Direction == "Prompt"
| project timestamp, Source, Agent, User, Channel, ConversationId, Prompt = Text
| order by timestamp desc
| take 200''',
         notes=["Text is empty (and rows are missing) unless content capture is on for that platform. See V3."]),
    dict(id="P2", title="Full conversation transcript: prompts, responses, tool calls and model calls", platforms=ALL3,
         desc="Paste a ConversationId and read the whole exchange in order: what the user asked, each tool the agent called (with the first 500 characters of arguments), each model call with its token counts, and the answer.",
         q=r'''let convId = "<paste ConversationId>";
union
    (AgentTraces(7d)    | where ConversationId == convId
                        | project timestamp, Kind = Direction, Agent, User, Detail = Text),
    (AgentToolCalls(7d) | where ConversationId == convId
                        | project timestamp, Kind = "Tool", Agent, User,
                                  Detail = strcat(Tool, " | args: ", substring(Arguments, 0, 500), " | succeeded: ", tostring(Succeeded))),
    (AgentTokens(7d)    | where ConversationId == convId
                        | project timestamp, Kind = "Model call", Agent, User,
                                  Detail = strcat(Model, " | in: ", tostring(InputTokens), " | out: ", tostring(OutputTokens), " | ", tostring(round(DurationMs)), " ms"))
| order by timestamp asc''',
         notes=["For spans without gen_ai.conversation.id the ConversationId falls back to the trace (OperationId), so the transcript shows one turn."]),
    dict(id="P3", title="Daily activity per agent", platforms=ALL3,
         desc="Conversations, prompts and active users per agent per day. A quick adoption view for a PoC read-out.",
         q=r'''AgentTraces(14d)
| summarize Conversations = dcount(ConversationId), Prompts = countif(Direction == "Prompt"), ActiveUsers = dcount(User)
            by Day = bin(timestamp, 1d), Source, Agent
| order by Day desc, Prompts desc''', notes=[]),
    dict(id="P4", title="Most common prompts", platforms=ALL3,
         desc="Groups near-identical prompts (case and spacing ignored, first 200 characters) to show what users really ask for. Good input for agent design: frequent questions deserve a dedicated topic or tool.",
         q=r'''AgentTraces(30d)
| where Direction == "Prompt"
| extend Normalized = trim(@"\s+", tolower(substring(Text, 0, 200)))
| summarize Count = count(), Users = dcount(User), Agents = make_set(Agent, 5), LastSeen = max(timestamp) by Normalized
| top 50 by Count desc''',
         notes=["Prompts that embed changing data (names, order numbers) will not group together. Shorten the substring length to group on the opening words."]),
    dict(id="P5", title="Conversation length", platforms=ALL3,
         desc="How many prompts a conversation takes and how long it lasts, per agent. Long conversations often mean the agent is not resolving the request on the first answer.",
         q=r'''AgentTraces(14d)
| summarize Turns = countif(Direction == "Prompt"), StartTime = min(timestamp), EndTime = max(timestamp) by Agent, ConversationId
| extend DurationMin = round((EndTime - StartTime) / 1m, 1)
| summarize Conversations = count(), AvgTurns = round(avg(Turns), 1), P95Turns = percentile(Turns, 95), MaxTurns = max(Turns),
            MedianMinutes = round(percentile(DurationMin, 50), 1), P95Minutes = round(percentile(DurationMin, 95), 1) by Agent
| order by Conversations desc''',
         notes=["Where ConversationId falls back to the trace ID, every conversation is one turn. Check V3 for gen_ai.conversation.id."]),
    dict(id="P6", title="Prompt and response size", platforms=ALL3,
         desc="Character length of prompts and responses per agent. Very long prompts are the first suspect when token cost is higher than expected.",
         q=r'''AgentTraces(7d)
| extend Chars = strlen(Text)
| summarize Messages = count(), AvgChars = round(avg(Chars)), P95Chars = round(percentile(Chars, 95)), MaxChars = max(Chars) by Agent, Direction
| order by Agent asc, Direction asc''',
         notes=["Roughly four characters per token for English text. Use K3 for the real token counts."]),
    dict(id="P7", title="Prompts with no recorded response", platforms=SPANS,
         desc="Turns where a prompt was recorded but no response was. Candidates for crashes, timeouts or content that was not captured.",
         q=r'''AgentTraces(1d)
| summarize Prompts = countif(Direction == "Prompt"), Responses = countif(Direction == "Response"),
            FirstSeen = min(timestamp), Prompt = take_any(iff(Direction == "Prompt", Text, "")) by Source, Agent, User, ConversationId, OperationId
| where Prompts > 0 and Responses == 0
| order by FirstSeen desc
| take 200''',
         notes=["Agent-level Copilot Studio events may not share an OperationId between received and sent messages, which would list normal turns here. Treat results for Source = Copilot Studio with care."]),
    dict(id="P8", title="Agent run latency and failures", platforms=SPANS,
         desc="End-to-end invoke_agent spans: runs, failures and latency percentiles per agent and model.",
         q=r'''{{DEPS}}
| where {{TS}} > ago(7d)
| where tostring({{PROPS}}["gen_ai.operation.name"]) == "invoke_agent"
| summarize Runs = count(), Failed = countif(tobool({{OK}}) == false),
            P50ms = round(percentile(todouble({{DUR}}), 50)), P95ms = round(percentile(todouble({{DUR}}), 95))
            by Agent = coalesce(tostring({{PROPS}}["gen_ai.agent.name"]), {{ROLE}}), Model = tostring({{PROPS}}["gen_ai.request.model"])
| extend FailureRatePct = round(100.0 * Failed / Runs, 1)
| order by Runs desc''', notes=[]),
]

TOOLS = [
    dict(id="T1", title="Which tools do my agents call?", platforms=SPANS,
         desc="Every tool ranked by use: calls, how many agents and users rely on it, failure rate and latency.",
         q=r'''AgentToolCalls(7d)
| summarize Calls = count(), Agents = dcount(Agent), Users = dcount(User), Failures = countif(Succeeded == false),
            P50ms = round(percentile(DurationMs, 50)), P95ms = round(percentile(DurationMs, 95)) by Tool, ToolType
| extend FailureRatePct = round(100.0 * Failures / Calls, 1)
| order by Calls desc''',
         notes=["Needs span-based telemetry. Agent-level Copilot Studio events carry no tool spans."]),
    dict(id="T2", title="Tool usage per agent", platforms=SPANS,
         desc="For each agent, which tools it uses and how often. Shows what an agent really does compared with what it was designed to do.",
         q=r'''AgentToolCalls(7d)
| summarize Calls = count(), Failures = countif(Succeeded == false), AvgMs = round(avg(DurationMs)) by Agent, Tool
| order by Agent asc, Calls desc''', notes=[]),
    dict(id="T3", title="Recent tool calls with arguments and results", platforms=SPANS,
         desc="A live feed of what agents called and with what: the first 300 characters of arguments and results.",
         q=r'''AgentToolCalls(1d)
| project timestamp, Agent, Tool, ToolType, Arguments = substring(Arguments, 0, 300), Result = substring(Result, 0, 300),
          Succeeded, DurationMs = round(DurationMs), User, ConversationId
| order by timestamp desc
| take 200''',
         notes=["Arguments and results are empty unless content capture is on. See V3 (WithToolArgs, WithToolResult)."]),
    dict(id="T4", title="Most common tool sequences", platforms=SPANS,
         desc="The order in which tools are called within one turn, counted per agent. Reveals the real workflow, repeated retries and tools that are always called together.",
         q=r'''AgentToolCalls(7d)
| order by timestamp asc
| summarize Tools = make_list(Tool, 50) by Agent, OperationId
| extend Sequence = strcat_array(Tools, " > ")
| summarize Turns = count() by Agent, Sequence
| top 30 by Turns desc''',
         notes=["Relies on the sorted order being kept by make_list, which is how it behaves in practice for a sorted input. A sequence such as A > A > A usually means the agent is retrying."]),
    dict(id="T5", title="Slowest tool calls", platforms=SPANS,
         desc="The 25 slowest calls in the last 7 days, with the tool, agent and conversation to pivot into P2.",
         q=r'''AgentToolCalls(7d)
| top 25 by DurationMs desc
| project timestamp, Agent, Tool, DurationMs = round(DurationMs), Succeeded, ConversationId, User''', notes=[]),
    dict(id="T6", title="Tool failures by result code", platforms=SPANS,
         desc="Only failed calls, grouped by tool and result code, with an example of the returned error text.",
         q=r'''AgentToolCalls(7d)
| where Succeeded == false
| summarize Failures = count(), LastSeen = max(timestamp), ExampleResult = take_any(substring(Result, 0, 200)) by Agent, Tool, ResultCode
| order by Failures desc''', notes=["ExampleResult is empty when tool results are not captured."]),
    dict(id="T7", title="Tool calls per prompt", platforms=SPANS,
         desc="How many tool calls it takes to answer one prompt, per agent. A rising average points to inefficient plans, retries or loops, and every extra call adds model tokens.",
         q=r'''AgentToolCalls(7d)
| summarize ToolCalls = count(), DistinctTools = dcount(Tool) by Agent, OperationId
| summarize Prompts = count(), AvgToolCalls = round(avg(ToolCalls), 1), P95ToolCalls = percentile(ToolCalls, 95),
            MaxToolCalls = max(ToolCalls), AvgDistinctTools = round(avg(DistinctTools), 1) by Agent
| order by AvgToolCalls desc''', notes=["Counts only prompts that called at least one tool."]),
    dict(id="T8", title="Tools that disappeared", platforms=SPANS,
         desc="Tools used in the last 30 days but not in the last 2 days. A tool that stops being called after a change usually means a broken connector or a changed agent.",
         q=r'''AgentToolCalls(30d)
| summarize FirstSeen = min(timestamp), LastSeen = max(timestamp), Calls = count() by Agent, Tool
| where LastSeen < ago(2d)
| order by LastSeen desc''', notes=[]),
    dict(id="T9", title="New tools seen recently", platforms=SPANS,
         desc="Tools that appeared for the first time in the last 2 days. Useful after releases, to confirm a new tool is actually being used.",
         q=r'''AgentToolCalls(30d)
| summarize FirstSeen = min(timestamp), Calls = count() by Agent, Tool
| where FirstSeen > ago(2d)
| order by FirstSeen desc''', notes=["Anything first seen inside the 30-day window can look new on a fresh deployment."]),
]

COST = [
    dict(id="K1", title="Token usage per agent, model and day", platforms=TOK,
         desc="Input and output tokens, model calls and average call latency. This is the base view for every cost conversation.",
         q=r'''AgentTokens(30d)
| summarize ModelCalls = count(), InputTokens = sum(InputTokens), OutputTokens = sum(OutputTokens), TotalTokens = sum(TotalTokens),
            AvgMs = round(avg(DurationMs)) by Day = bin(timestamp, 1d), Agent, Model
| order by Day desc, TotalTokens desc''',
         notes=["If this returns nothing but V3 shows token counts on another operation, change tokenOps in AgentTokens."]),
    dict(id="K2", title="Estimated cost per agent and day", platforms=TOK,
         desc="Tokens priced with your price table in AgentCost. UnpricedCalls counts model calls whose model is not in the table, so you know when a number is incomplete.",
         q=r'''AgentCost(30d)
| summarize TotalTokens = sum(TotalTokens), InputCost = round(sum(InputCost), 4), OutputCost = round(sum(OutputCost), 4),
            Cost = round(sum(Cost), 4), UnpricedCalls = countif(PriceMissing) by Day = bin(timestamp, 1d), Agent
| order by Day desc, Cost desc''',
         notes=["Cost is an estimate from token counts and your own prices. It excludes platform fees, provisioned throughput, cached-token discounts, Copilot Studio credits and any other charges on your invoice.",
                "All zeros: the price table in AgentCost still has placeholder 0.0 prices."]),
    dict(id="K3", title="Tokens and cost per prompt, by agent", platforms=TOK,
         desc="What a single prompt costs on average and at the 95th percentile, and how many model calls it takes. A prompt here is one trace (OperationId), including all model calls the agent makes while answering it.",
         q=r'''AgentCost(7d)
| summarize Tokens = sum(TotalTokens), Cost = sum(Cost), ModelCalls = count() by Agent, OperationId
| summarize Prompts = count(), AvgTokens = round(avg(Tokens)), P95Tokens = round(percentile(Tokens, 95)),
            AvgCost = round(avg(Cost), 5), P95Cost = round(percentile(Cost, 95), 5), AvgModelCalls = round(avg(ModelCalls), 1) by Agent
| order by AvgCost desc, AvgTokens desc''',
         notes=["Works best when the framework puts all model calls of one turn under one trace, which is the default for OpenTelemetry."]),
    dict(id="K4", title="Most expensive prompts", platforms=TOK,
         desc="The 25 prompts that consumed the most tokens, with the prompt text, so you can see which kinds of requests are driving cost.",
         q=r'''let spend =
    AgentCost(7d)
    | summarize Tokens = sum(TotalTokens), InputTokens = sum(InputTokens), OutputTokens = sum(OutputTokens),
                Cost = round(sum(Cost), 5), ModelCalls = count(), Models = make_set(Model, 5) by OperationId;
let asked =
    AgentTraces(7d)
    | where Direction == "Prompt"
    | extend Prompt = substring(Text, 0, 300)
    | summarize arg_max(timestamp, Agent, User, ConversationId, Prompt) by OperationId;
spend
| join kind=inner asked on OperationId
| project timestamp, Agent, User, ConversationId, Prompt, Tokens, InputTokens, OutputTokens, Cost, ModelCalls, Models
| order by Cost desc, Tokens desc
| take 25''',
         notes=["Prompt text needs content capture. Without it the join returns nothing; use K5 or K3, which work on token counts alone.",
                "Large InputTokens with a short prompt means the agent is sending a lot of context (instructions, history, tool results) on every call."]),
    dict(id="K5", title="Most expensive conversations", platforms=TOK,
         desc="Conversations ranked by total tokens and cost, with the number of turns and models used.",
         q=r'''AgentCost(7d)
| summarize Tokens = sum(TotalTokens), Cost = round(sum(Cost), 5), ModelCalls = count(), Turns = dcount(OperationId),
            Models = make_set(Model, 5), Start = min(timestamp), End = max(timestamp) by Agent, User, ConversationId
| order by Cost desc, Tokens desc
| take 25''',
         notes=["Where spans carry no gen_ai.conversation.id, each trace is its own conversation here."]),
    dict(id="K6", title="Tokens and cost per user", platforms=TOK,
         desc="Which users generate the most usage, with tokens per prompt. Use it for chargeback conversations and to find heavy or automated use.",
         q=r'''AgentCost(30d)
| summarize Tokens = sum(TotalTokens), Cost = round(sum(Cost), 4), Prompts = dcount(OperationId), Agents = make_set(Agent, 5) by User
| extend TokensPerPrompt = round(1.0 * Tokens / Prompts)
| order by Cost desc, Tokens desc
| take 50''',
         notes=["User identity depends on what each platform emits. Check how much of the result is blank or a technical ID before using it for chargeback."]),
    dict(id="K7", title="Input versus output tokens", platforms=TOK,
         desc="The share of input tokens per agent and model. Cost is usually dominated by input (instructions, history, tool results). A high input share means trimming context saves more than shortening answers.",
         q=r'''AgentTokens(7d)
| summarize InputTokens = sum(InputTokens), OutputTokens = sum(OutputTokens), ModelCalls = count() by Agent, Model
| extend InputSharePct = round(100.0 * InputTokens / (InputTokens + OutputTokens), 1),
         AvgInputPerCall = round(1.0 * InputTokens / ModelCalls), AvgOutputPerCall = round(1.0 * OutputTokens / ModelCalls)
| order by InputTokens desc''', notes=[]),
    dict(id="K8", title="Model mix and the exact model names to price", platforms=TOK,
         desc="Which models are used, by how many agents, and the exact name strings in your telemetry. Copy the names (without the date suffix) into the price table in AgentCost.",
         q=r'''AgentTokens(30d)
| extend ModelKey = replace_regex(Model, @"-\d{4}-\d{2}-\d{2}$", "")
| summarize ModelCalls = count(), Agents = dcount(Agent), TotalTokens = sum(TotalTokens), RawNames = make_set(Model, 5) by ModelKey
| order by TotalTokens desc''',
         notes=["Deployment names in Azure OpenAI can differ from the model name. If ModelKey shows deployment names, price those instead."]),
    dict(id="K9", title="Week-over-week change", platforms=TOK,
         desc="Tokens and cost for the last 7 days against the 7 days before, per agent. Spot an agent whose consumption is climbing before the invoice does.",
         q=r'''AgentCost(14d)
| extend Recent = timestamp > ago(7d)
| summarize TokensLast7d = sumif(TotalTokens, Recent), TokensPrior7d = sumif(TotalTokens, not(Recent)),
            CostLast7d = round(sumif(Cost, Recent), 4), CostPrior7d = round(sumif(Cost, not(Recent)), 4) by Agent
| extend TokenChangePct = iff(TokensPrior7d == 0, real(null), round(100.0 * (TokensLast7d - TokensPrior7d) / TokensPrior7d, 1))
| order by TokensLast7d desc''', notes=[]),
]

ALERTS = [
    dict(id="O1", title="Token spike versus the last 24 hours", platforms=TOK,
         desc="Fires when an agent used more than three times its normal hourly tokens in the last hour. Catches loops, prompt bloat after a change, or a sudden traffic burst.",
         q=r'''let spikeFactor = 3.0;
let minTokens = 50000;     // ignore small agents
let data = AgentTokens(25h) | extend LastHour = timestamp > ago(1h);
data
| summarize LastHourTokens = sumif(TotalTokens, LastHour), BaselineHourlyAvg = round(sumif(TotalTokens, not(LastHour)) / 24.0)
            by Agent
| where LastHourTokens > minTokens and LastHourTokens > spikeFactor * BaselineHourlyAvg''',
         notes=["A brand-new agent has no baseline, so any usage above minTokens fires once."],
         rule=dict(freq="Every 1 hour", window="25 hours", sev="2 (Warning)", split="Agent")),
    dict(id="O2", title="Daily token or cost budget exceeded", platforms=TOK,
         desc="Fires when an agent passes a rolling 24-hour budget. Set either or both budgets. Without prices filled in, use the token budget.",
         q=r'''let dailyCostBudgetUsd = 50.0;
let dailyTokenBudget = 5000000;
AgentCost(1d)
| summarize Tokens = sum(TotalTokens), Cost = round(sum(Cost), 2), ModelCalls = count() by Agent
| where Cost > dailyCostBudgetUsd or Tokens > dailyTokenBudget''',
         notes=["The window is a rolling 24 hours, not a calendar day. Remove 'by Agent' for a total across all agents."],
         rule=dict(freq="Every 1 hour", window="1 day", sev="2 (Warning)", split="Agent")),
    dict(id="O3", title="Agent latency or failure regression", platforms=SPANS,
         desc="Fires when an agent's 95th percentile run time doubles, or its failure rate rises by 20 points, compared with the previous 24 hours.",
         q=r'''let runs =
    {{DEPS}}
    | where {{TS}} > ago(25h)
    | where tostring({{PROPS}}["gen_ai.operation.name"]) == "invoke_agent"
    | project timestamp = {{TS}}, Agent = coalesce(tostring({{PROPS}}["gen_ai.agent.name"]), {{ROLE}}),
              DurationMs = todouble({{DUR}}), Failed = (tobool({{OK}}) == false);
let current =
    runs | where timestamp > ago(1h)
    | summarize Runs = count(), Failures = countif(Failed), P95ms = percentile(DurationMs, 95) by Agent;
let history =
    runs | where timestamp <= ago(1h)
    | summarize BaseP95ms = percentile(DurationMs, 95), BaseFailureRate = 1.0 * countif(Failed) / count() by Agent;
current
| join kind=inner history on Agent
| extend FailureRate = round(1.0 * Failures / Runs, 2)
| where Runs >= 5 and (P95ms > 2 * BaseP95ms or FailureRate > BaseFailureRate + 0.2)
| project Agent, Runs, Failures, FailureRate, BaseFailureRate = round(BaseFailureRate, 2), P95ms = round(P95ms), BaseP95ms = round(BaseP95ms)''',
         notes=["Pair with an alert on the model deployment itself if latency comes from throttling (HTTP 429)."],
         rule=dict(freq="Every 1 hour", window="25 hours", sev="2 (Warning)", split="Agent")),
    dict(id="O4", title="Tool failure spike", platforms=SPANS,
         desc="Fires when a tool's failure rate in the last hour is 20 points above its previous 24 hours and it failed at least 5 times. Usually a broken connector, expired credential or changed API.",
         q=r'''let current =
    AgentToolCalls(25h) | where timestamp > ago(1h)
    | summarize Calls = count(), Failures = countif(Succeeded == false), ExampleResult = take_any(substring(Result, 0, 200)) by Agent, Tool;
let history =
    AgentToolCalls(25h) | where timestamp <= ago(1h)
    | summarize BaseFailureRate = 1.0 * countif(Succeeded == false) / count() by Agent, Tool;
current
| join kind=leftouter history on Agent, Tool
| extend FailureRate = round(1.0 * Failures / Calls, 2), BaseFailureRate = round(coalesce(BaseFailureRate, 0.0), 2)
| where Failures >= 5 and FailureRate > BaseFailureRate + 0.2
| project Agent, Tool, Calls, Failures, FailureRate, BaseFailureRate, ExampleResult''', notes=[],
         rule=dict(freq="Every 1 hour", window="25 hours", sev="2 (Warning)", split="Agent, Tool")),
    dict(id="O5", title="Runaway turn: too many tool calls in one prompt", platforms=SPANS,
         desc="Fires when a single prompt triggers more than 50 tool calls. Almost always a planning loop, and each call burns tokens.",
         q=r'''let maxToolCallsPerPrompt = 50;
AgentToolCalls(1h)
| summarize ToolCalls = count(), DistinctTools = dcount(Tool), Tools = make_set(Tool, 10), FirstSeen = min(timestamp)
            by Agent, User, ConversationId, OperationId
| where ToolCalls > maxToolCallsPerPrompt''', notes=["Lower the threshold once T7 shows what normal looks like for your agents."],
         rule=dict(freq="Every 15 minutes", window="1 hour", sev="2 (Warning)", split="Agent")),
    dict(id="O6", title="Agent telemetry went silent", platforms=SPANS,
         desc="Fires for agents that produced GenAI spans in the previous 24-48 hours but nothing in the last 24 hours. Catches a broken telemetry connection or an agent that stopped being used.",
         q=r'''{{DEPS}}
| where {{TS}} > ago(2d)
| extend Op = tostring({{PROPS}}["gen_ai.operation.name"])
| where isnotempty(Op)
| summarize LastSeen = max({{TS}}), Spans = count() by Agent = coalesce(tostring({{PROPS}}["gen_ai.agent.name"]), {{ROLE}})
| where LastSeen < ago(24h)''', notes=["Agents that are legitimately quiet at weekends will fire. Use a longer window (the Azure Monitor limit applies) or exclude them."],
         rule=dict(freq="Every 1 hour", window="2 days", sev="3 (Informational)", split="Agent")),
]

SECTIONS_Q = [VERIFY, PROMPTS, TOOLS, COST, ALERTS]
