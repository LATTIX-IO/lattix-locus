// Out-of-band confirmation for every capability-widening request (LOCUS-357).
//
// On the desktop install the backend authenticates every loopback request as
// the operator (local-operator bootstrap). So approving an agent's request,
// clearing the panic stop, loosening platform settings, adding integrations,
// skills, provider keys, triggers or schedules, or touching the active
// guardrails needs a proof that only this shell can produce, after the human
// confirms a native OS dialog (browser_tier.rs covers the browser tier and
// pairing the same way).
//
// The webview invokes `confirm_action { action, path, body }`. The shell:
//   1. looks the action up in ACTIONS (mirrored byte for byte from
//      apps/backend/app/request_security.py; tests/backend/test_desktop_packaging.py
//      checks it) and refuses a path that does not match its template;
//   2. reads the current state from the backend where the action names one,
//      and builds the dialog text itself from the request and that state
//      (never from text supplied by the webview), with secrets masked;
//   3. only on "Allow", serialises the body canonically (sorted keys, compact,
//      whole numbers only), signs
//      HMAC-SHA256(secret, "locus-shell-proof/v1|<action>|<digest>|<nonce>|<ts>")
//      where digest = SHA-256(METHOD "\n" PATH "\n" CANONICAL_BODY), and sends
//      exactly those bytes itself. The webview never sees the secret or the proof.
//
// The backend recomputes the digest over the request it received
// (locus_tooling/shell_confirmation.py::verify_request); a proof is single-use
// and valid for 60 s.

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::browser_tier::{ask_human, hex, proof, send, MESSAGE_PREFIX};

const MAX_BODY_BYTES: usize = 256 * 1024;
const MAX_SEGMENT_CHARS: usize = 200;
const MAX_LINE_CHARS: usize = 1000;
const MAX_DETAIL_LINES: usize = 40;
const ESCALATION_APPROVE: &str = "workflow.run.escalations.approve";

struct ShellAction {
    id: &'static str,
    method: &'static str,
    path: &'static str,
    title: &'static str,
    risk: &'static str,
    current: Option<&'static str>,
}

fn request_digest(method: &str, path: &str, canonical_body: &str) -> String {
    hex(&Sha256::digest(
        format!("{method}\n{path}\n{canonical_body}").as_bytes(),
    ))
}

fn action_message(action: &str, digest: &str, nonce: &str, ts: u64) -> String {
    format!("{MESSAGE_PREFIX}|{action}|{digest}|{nonce}|{ts}")
}

fn json_string(text: &str) -> Result<String, String> {
    serde_json::to_string(text).map_err(|_| "unsupported text in the request".to_string())
}

/// Canonical JSON, identical to Python's
/// `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
/// for every value it accepts. Non-integer numbers are refused.
fn canonical_json(value: &Value, out: &mut String) -> Result<(), String> {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Value::Number(number) => {
            if !(number.is_i64() || number.is_u64()) {
                return Err("only whole numbers can be confirmed".to_string());
            }
            out.push_str(&number.to_string());
        }
        Value::String(text) => out.push_str(&json_string(text)?),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                canonical_json(item, out)?;
            }
            out.push(']');
        }
        Value::Object(map) => {
            let mut entries: Vec<(&String, &Value)> = map.iter().collect();
            entries.sort_by(|a, b| a.0.cmp(b.0));
            out.push('{');
            for (index, (key, item)) in entries.into_iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                out.push_str(&json_string(key)?);
                out.push(':');
                canonical_json(item, out)?;
            }
            out.push('}');
        }
    }
    Ok(())
}

fn valid_segment(segment: &str) -> bool {
    !segment.is_empty()
        && segment.len() <= MAX_SEGMENT_CHARS
        && segment.chars().any(|c| c != '.')
        && segment
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | ':'))
}

/// The path parameters when `path` matches `template` exactly (no query).
fn path_params(template: &str, path: &str) -> Option<Vec<(String, String)>> {
    let expected: Vec<&str> = template.split('/').collect();
    let actual: Vec<&str> = path.split('/').collect();
    if expected.len() != actual.len() {
        return None;
    }
    let mut params = Vec::new();
    for (want, got) in expected.iter().zip(actual.iter()) {
        if let Some(name) = want
            .strip_prefix('{')
            .and_then(|rest| rest.strip_suffix('}'))
        {
            if !valid_segment(got) {
                return None;
            }
            params.push((name.to_string(), got.to_string()));
        } else if want != got {
            return None;
        }
    }
    Some(params)
}

fn fill(template: &str, params: &[(String, String)]) -> String {
    let mut out = template.to_string();
    for (name, value) in params {
        out = out.replace(&format!("{{{name}}}"), value);
    }
    out
}

fn param<'a>(params: &'a [(String, String)], name: &str) -> &'a str {
    params
        .iter()
        .find(|(key, _)| key.as_str() == name)
        .map(|(_, value)| value.as_str())
        .unwrap_or("")
}

async fn get_json(path: String) -> Result<Value, String> {
    let (status, body) = tauri::async_runtime::spawn_blocking(move || send("GET", &path, "", None))
        .await
        .map_err(|_| "request task failed".to_string())??;
    if status != 200 {
        return Err(format!("could not read the current state (HTTP {status})"));
    }
    serde_json::from_str(&body).map_err(|_| "the backend sent an unreadable state".to_string())
}

fn secret_field(key: &str) -> bool {
    let key = key.to_ascii_lowercase();
    key.ends_with("api_key")
        || key.contains("secret")
        || key.contains("password")
        || key.contains("token")
}

/// A copy with every secret value replaced by a label, at any depth.
fn masked(value: &Value, set_label: &str) -> Value {
    match value {
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                let shown = if secret_field(key) {
                    Value::String(
                        match item {
                            Value::Null => "(none)",
                            Value::String(text) if text.trim().is_empty() => "(unchanged)",
                            Value::String(text) if text == "__clear__" => "(removed)",
                            _ => set_label,
                        }
                        .to_string(),
                    )
                } else {
                    masked(item, set_label)
                };
                out.insert(key.clone(), shown);
            }
            Value::Object(out)
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(|item| masked(item, set_label)).collect())
        }
        other => other.clone(),
    }
}

fn show(value: &Value) -> String {
    match value {
        Value::Null => "(none)".to_string(),
        Value::String(text) if text.is_empty() => "(empty)".to_string(),
        Value::String(text) => text.clone(),
        Value::Array(items) if items.is_empty() => "(none)".to_string(),
        Value::Array(items) => items.iter().map(show).collect::<Vec<_>>().join(", "),
        other => other.to_string(),
    }
}

/// Control and bidirectional-override characters cannot reshape the dialog.
fn clean(text: &str) -> String {
    text.chars()
        .map(|c| {
            if c.is_control()
                || matches!(c, '\u{200E}' | '\u{200F}' | '\u{202A}'..='\u{202E}' | '\u{2066}'..='\u{2069}')
            {
                '?'
            } else {
                c
            }
        })
        .collect()
}

/// One line per changed field: list additions and removals, old -> new, or
/// the new value when the current state is unknown. Unchanged fields are left out.
fn change_lines(body: &Value, current: Option<&Value>) -> Vec<String> {
    let Some(fields) = body.as_object() else {
        return vec![show(body)];
    };
    let before = current.and_then(Value::as_object);
    let mut entries: Vec<(&String, &Value)> = fields.iter().collect();
    entries.sort_by(|a, b| a.0.cmp(b.0));
    let mut lines = Vec::new();
    for (key, after) in entries {
        match (before.and_then(|map| map.get(key.as_str())), after) {
            (Some(old), _) if old == after => {}
            (Some(Value::Array(old_items)), Value::Array(new_items)) => {
                let added: Vec<String> = new_items
                    .iter()
                    .filter(|item| !old_items.contains(*item))
                    .map(show)
                    .collect();
                let removed: Vec<String> = old_items
                    .iter()
                    .filter(|item| !new_items.contains(*item))
                    .map(show)
                    .collect();
                if !added.is_empty() {
                    lines.push(format!("{key}: add {}", added.join(", ")));
                }
                if !removed.is_empty() {
                    lines.push(format!("{key}: remove {}", removed.join(", ")));
                }
            }
            (Some(old), _) => lines.push(format!("{key}: {} -> {}", show(old), show(after))),
            (None, _) => lines.push(format!("{key}: {}", show(after))),
        }
    }
    lines
}

/// "Approve: <tool> on <target> for run <id>", read from the backend's own
/// record of the pending request (the escalation), not from the webview.
fn escalation_lines(
    params: &[(String, String)],
    body: Option<&Value>,
    current: Option<&Value>,
) -> Result<Vec<String>, String> {
    let run_id = param(params, "run_id");
    let escalation_id = param(params, "escalation_id");
    let entry = current
        .and_then(|state| state.get("escalations"))
        .and_then(Value::as_array)
        .and_then(|items| {
            items
                .iter()
                .find(|item| item.get("id").and_then(Value::as_str) == Some(escalation_id))
        })
        .ok_or_else(|| "this request is no longer pending".to_string())?;
    if entry.get("status").and_then(Value::as_str) != Some("pending") {
        return Err("this request is no longer pending".to_string());
    }
    let field = |key: &str| entry.get(key).map(show).unwrap_or_default();
    let tool = field("tool");
    let what = if tool.is_empty() {
        "access to a folder".to_string()
    } else {
        tool
    };
    let mut lines = vec![format!(
        "Approve: {what} on {} for run {run_id}",
        field("path")
    )];
    let risk = field("risk");
    if !risk.is_empty() {
        lines.push(format!("Risk: {risk}"));
    }
    let reasons = field("reasons");
    if !reasons.is_empty() && reasons != "(none)" {
        lines.push(format!("Why it asks: {reasons}"));
    }
    let scope = body
        .and_then(|value| value.get("scope"))
        .and_then(Value::as_str)
        .unwrap_or("once");
    let pinned = body
        .and_then(|value| value.get("pin"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    lines.push(
        match (scope, pinned) {
            ("run", _) => "Scope: every matching action in this run",
            ("standing", true) => "Scope: a standing grant, pinned (it never expires)",
            ("standing", false) => "Scope: a standing grant for 30 days",
            _ => "Scope: this action, once",
        }
        .to_string(),
    );
    Ok(lines)
}

/// Ask the principal, in a native dialog, to confirm a capability-widening
/// request; on "Allow", sign it and send it to the backend. Returns the
/// backend's JSON response.
#[tauri::command]
pub async fn confirm_action(
    app: tauri::AppHandle,
    action: String,
    path: String,
    body: Option<Value>,
) -> Result<String, String> {
    let spec = ACTIONS
        .iter()
        .find(|candidate| candidate.id == action)
        .ok_or_else(|| "unknown action".to_string())?;
    let params = path_params(spec.path, &path)
        .ok_or_else(|| "the path does not match this action".to_string())?;
    let canonical = match &body {
        None => String::new(),
        Some(value @ Value::Object(_)) => {
            let mut out = String::new();
            canonical_json(value, &mut out)?;
            out
        }
        Some(_) => return Err("the request body must be a JSON object".to_string()),
    };
    if canonical.len() > MAX_BODY_BYTES {
        return Err("the request is too large to confirm".to_string());
    }
    let current = match spec.current {
        Some(template) => Some(get_json(fill(template, &params)).await?),
        None => None,
    };
    let details = if spec.id == ESCALATION_APPROVE {
        escalation_lines(&params, body.as_ref(), current.as_ref())?
    } else {
        let mut lines: Vec<String> = params
            .iter()
            .map(|(name, value)| format!("{name}: {value}"))
            .collect();
        if let Some(value) = &body {
            let before = current.as_ref().map(|state| masked(state, "(stored)"));
            lines.extend(change_lines(
                &masked(value, "(new secret value)"),
                before.as_ref(),
            ));
        }
        lines
    };
    if details.len() > MAX_DETAIL_LINES
        || details
            .iter()
            .any(|line| line.chars().count() > MAX_LINE_CHARS)
    {
        return Err("too many changes to confirm at once: make smaller changes".to_string());
    }
    let shown = if details.is_empty() {
        "(no visible change)".to_string()
    } else {
        details
            .iter()
            .map(|line| clean(line))
            .collect::<Vec<_>>()
            .join("\n")
    };
    let text = format!(
        "{}\n\n{}\n\n{shown}\n\nOnly allow this if you asked for it.",
        spec.title, spec.risk
    );
    if !ask_human(app, format!("Lattix Locus: {}", spec.title), text).await {
        return Err("cancelled".to_string());
    }
    let digest = request_digest(spec.method, &path, &canonical);
    let id = spec.id;
    let header = proof(|nonce, ts| action_message(id, &digest, nonce, ts))?;
    let method = spec.method;
    let (status, response) = tauri::async_runtime::spawn_blocking(move || {
        send(method, &path, &canonical, Some(header.as_str()))
    })
    .await
    .map_err(|_| "request task failed".to_string())??;
    if (200..300).contains(&status) {
        Ok(response)
    } else {
        Err(format!("the backend refused the request (HTTP {status})"))
    }
}

// Mirrors the widening / conditional rules of apps/backend/app/request_security.py
// (generic proof format) byte for byte; tests/backend/test_desktop_packaging.py
// fails when the two differ.
static ACTIONS: &[ShellAction] = &[
    ShellAction {
        id: "skills.user.write",
        method: "PUT",
        path: "/skills/user",
        title: "Add skills to your agents",
        risk: "The added skills will be loaded into your agents.",
        current: Some("/skills/user"),
    },
    ShellAction {
        id: "skill.save",
        method: "POST",
        path: "/skills",
        title: "Enable or change a skill",
        risk: "The instructions of this skill will be given to the agents.",
        current: None,
    },
    ShellAction {
        id: "skill.promote",
        method: "POST",
        path: "/skills/{skill_id}/promote",
        title: "Trust and promote a skill",
        risk: "Signs the skill as trusted and raises its tier. Its scripts may then run.",
        current: None,
    },
    ShellAction {
        id: "skill.import",
        method: "POST",
        path: "/skills/import",
        title: "Install a skill",
        risk: "Fetches the skill and installs it in quarantine. It cannot run until it is scanned and promoted.",
        current: None,
    },
    ShellAction {
        id: "runtime.user_providers.write",
        method: "PUT",
        path: "/runtime/user-providers/{provider}",
        title: "Set a model provider key",
        risk: "Agents may send your prompts and data to this model provider using this key.",
        current: None,
    },
    ShellAction {
        id: "models.provider.key.set",
        method: "PUT",
        path: "/models/providers/{provider_id}/key",
        title: "Set a model provider key",
        risk: "Agents may send your prompts and data to this model provider using this key.",
        current: None,
    },
    ShellAction {
        id: "user.settings.save",
        method: "PUT",
        path: "/user/settings",
        title: "Change your default chat mode",
        risk: "New chats will start in a mode that lets the agent do more, without you switching modes.",
        current: Some("/user/settings"),
    },
    ShellAction {
        id: "platform.settings.save",
        method: "POST",
        path: "/platform/settings",
        title: "Loosen platform settings",
        risk: "These changes loosen platform security for every agent: allowlists, approvals, limits, guardrail enforcement, runtimes or model providers.",
        current: Some("/platform/settings"),
    },
    ShellAction {
        id: "workflow.run.escalations.approve",
        method: "POST",
        path: "/workflow-runs/{run_id}/escalations/{escalation_id}/approve",
        title: "Approve an agent request",
        risk: "The agent may perform this action. A run or standing scope also mints a grant, so matching actions stop asking.",
        current: Some("/workflow-runs/{run_id}/escalations"),
    },
    ShellAction {
        id: "approval.submit",
        method: "POST",
        path: "/approvals",
        title: "Approve a run",
        risk: "Marks the result of the run as approved and the run as done.",
        current: None,
    },
    ShellAction {
        id: "computer_use.reset",
        method: "POST",
        path: "/computer-use/reset",
        title: "Resume computer use",
        risk: "Clears the panic stop: the agent may control the desktop and browsers again.",
        current: None,
    },
    ShellAction {
        id: "workflow.trigger.create",
        method: "POST",
        path: "/workflow-definitions/{item_id}/triggers",
        title: "Create a webhook trigger",
        risk: "Anyone who has the webhook URL can start this workflow, without you.",
        current: None,
    },
    ShellAction {
        id: "workflow.schedule.create",
        method: "POST",
        path: "/workflow-definitions/{item_id}/schedules",
        title: "Schedule a workflow",
        risk: "The workflow will start on its own on this schedule, without you.",
        current: None,
    },
    ShellAction {
        id: "workflow.schedule.toggle",
        method: "POST",
        path: "/schedules/{schedule_id}/toggle",
        title: "Turn on a schedule",
        risk: "The workflow will start on its own on this schedule, without you.",
        current: None,
    },
    ShellAction {
        id: "integration.catalog.install",
        method: "POST",
        path: "/integrations/catalog/{catalog_id}/install",
        title: "Install an integration",
        risk: "Adds this integration to the services the agents may use.",
        current: None,
    },
    ShellAction {
        id: "integration.mcp.save",
        method: "POST",
        path: "/integrations/mcp",
        title: "Add or change an MCP server",
        risk: "Registers this MCP server. Once approved, the agents may call its tools.",
        current: None,
    },
    ShellAction {
        id: "integration.mcp.approve",
        method: "POST",
        path: "/integrations/mcp/{connection_id}/approve",
        title: "Approve an MCP server",
        risk: "The agents may call the tools of this MCP server.",
        current: None,
    },
    ShellAction {
        id: "integration.oauth.connect",
        method: "POST",
        path: "/integrations/{integration_id}/oauth/connect",
        title: "Connect an account",
        risk: "Starts sign-in so the agents may act with the access of this account.",
        current: None,
    },
    ShellAction {
        id: "integration.save",
        method: "POST",
        path: "/integrations",
        title: "Add or change an integration",
        risk: "The agents may call this service with the credentials stored for it.",
        current: None,
    },
    ShellAction {
        id: "guardrail.ruleset.publish",
        method: "POST",
        path: "/guardrail-rulesets/{item_id}/publish",
        title: "Publish a guardrail ruleset",
        risk: "Changes the guardrails the agents run under. The rules that become active may be weaker than the current ones.",
        current: None,
    },
    ShellAction {
        id: "guardrail.ruleset.activate",
        method: "POST",
        path: "/guardrail-rulesets/{item_id}/activate",
        title: "Activate a guardrail ruleset revision",
        risk: "Changes the guardrails the agents run under. The rules that become active may be weaker than the current ones.",
        current: None,
    },
    ShellAction {
        id: "guardrail.ruleset.rollback",
        method: "POST",
        path: "/guardrail-rulesets/{item_id}/rollback",
        title: "Roll back a guardrail ruleset",
        risk: "Changes the guardrails the agents run under. The rules that become active may be weaker than the current ones.",
        current: None,
    },
    ShellAction {
        id: "guardrail.ruleset.archive",
        method: "POST",
        path: "/guardrail-rulesets/{item_id}/archive",
        title: "Archive a guardrail ruleset",
        risk: "Removes this ruleset from the active guardrails. Workflows that use it lose its rules.",
        current: None,
    },
    ShellAction {
        id: "guardrail.ruleset.delete",
        method: "DELETE",
        path: "/guardrail-rulesets/{item_id}",
        title: "Delete a guardrail ruleset",
        risk: "Removes this ruleset from the active guardrails. Workflows that use it lose its rules.",
        current: None,
    },
];
