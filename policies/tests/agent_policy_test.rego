package lattix.agent_policy_test

import rego.v1

import data.lattix.agent_policy

test_allow_registered_tool if {
  agent_policy.allow with input as {
    "agent_id": "orchestrator",
    "tool": "execute_step",
    "allowed_tools": ["execute_step"],
    "resource": "workflow",
    "budget": {"tokens_used": 0, "max_tokens": 10},
    "action": "execute_step",
    "classification": "internal",
    "provider": "local"
  }
}

test_allow_backend_execute_step if {
  agent_policy.allow with input as {
    "agent_id": "backend",
    "tool": "execute_step",
    "allowed_tools": ["execute_step"],
    "resource": "workflow",
    "budget": {"tokens_used": 0, "max_tokens": 10},
    "action": "execute_step",
    "classification": "internal",
    "provider": "local"
  }
}

test_allow_dynamic_allowed_tools_for_custom_agent if {
  agent_policy.allow with input as {
    "agent_id": "custom-agent",
    "tool": "generate_code",
    "allowed_tools": ["generate_code"],
    "resource": "artifact.py",
    "budget": {"tokens_used": 0, "max_tokens": 10},
    "action": "execute_step",
    "classification": "internal",
    "provider": "local"
  }
}

test_allow_when_action_matches_and_tool_is_missing if {
  agent_policy.allow with input as {
    "agent_id": "custom-agent",
    "allowed_tools": ["generate_code"],
    "resource": "artifact.py",
    "budget": {"tokens_used": 0, "max_tokens": 10},
    "action": "generate_code",
    "classification": "internal",
    "provider": "local"
  }
}

test_deny_credential_file if {
  agent_policy.deny with input as {
    "agent_id": "research",
    "tool": "read_file",
    "allowed_tools": ["read_file"],
    "resource": ".env",
    "budget": {"tokens_used": 0, "max_tokens": 10},
    "action": "read_file",
    "classification": "internal",
    "provider": "local"
  }
}

test_deny_secret_json_filename if {
  agent_policy.deny with input as {
    "agent_id": "research",
    "tool": "read_file",
    "allowed_tools": ["read_file"],
    "resource": "backup/service-account-prod.json",
    "budget": {"tokens_used": 0, "max_tokens": 10},
    "action": "read_file",
    "classification": "internal",
    "provider": "local"
  }
}

test_deny_bearer_token_like_filename if {
  agent_policy.deny with input as {
    "agent_id": "research",
    "tool": "read_file",
    "allowed_tools": ["read_file"],
    "resource": "tmp/bearer_token_backup.txt",
    "budget": {"tokens_used": 0, "max_tokens": 10},
    "action": "read_file",
    "classification": "internal",
    "provider": "local"
  }
}

test_deny_restricted_external_llm if {
  agent_policy.deny with input as {
    "agent_id": "orchestrator",
    "tool": "llm_call",
    "resource": "workflow",
    "budget": {"tokens_used": 0, "max_tokens": 10},
    "action": "llm_call",
    "classification": "restricted",
    "provider": "openai"
  }
}

test_deny_tool_call_budget_exceeded if {
  agent_policy.deny with input as {
    "agent_id": "backend",
    "tool": "execute_step",
    "resource": "workflow",
    "budget": {"tokens_used": 0, "max_tokens": 10},
    "action": "execute_step",
    "classification": "internal",
    "provider": "local",
    "max_tool_calls": 1,
    "tool_calls_used": 2
  }
}

test_deny_run_action_budget_exceeded if {
  agent_policy.deny with input as {
    "agent_id": "codex-mcp-tools",
    "tool": "read_file",
    "allowed_tools": ["read_file"],
    "resource": "src/app.py",
    "action": "read_file",
    "max_actions": 1,
    "actions_used": 2
  }
}

test_allow_run_action_budget_at_limit if {
  agent_policy.allow with input as {
    "agent_id": "codex-mcp-tools",
    "tool": "read_file",
    "allowed_tools": ["read_file"],
    "resource": "src/app.py",
    "action": "read_file",
    "max_actions": 1,
    "actions_used": 1
  }
}

test_deny_zero_run_action_budget if {
  agent_policy.deny with input as {
    "agent_id": "codex-mcp-tools",
    "tool": "read_file",
    "allowed_tools": ["read_file"],
    "resource": "src/app.py",
    "action": "read_file",
    "max_actions": 0,
    "actions_used": 1
  }
}

# LOCUS-362: the read_file deny rules match Windows paths too.
test_deny_dotenv_windows_path if {
  agent_policy.deny with input as {
    "agent_id": "research",
    "tool": "read_file",
    "allowed_tools": ["read_file"],
    "resource": `C:\Users\dev\ws\.env`,
    "action": "read_file"
  }
}

test_deny_ssh_dir_windows_path if {
  agent_policy.deny with input as {
    "agent_id": "research",
    "tool": "read_file",
    "allowed_tools": ["read_file"],
    "resource": `C:\Users\dev\.ssh\config`,
    "action": "read_file"
  }
}

test_allow_ordinary_windows_read if {
  agent_policy.allow with input as {
    "agent_id": "research",
    "tool": "read_file",
    "allowed_tools": ["read_file"],
    "resource": `C:\Users\dev\ws\src\app.py`,
    "action": "read_file"
  }
}
