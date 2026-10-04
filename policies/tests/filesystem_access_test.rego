package lattix.filesystem_access_test

import rego.v1

import data.lattix.filesystem_access

test_allow_read_under_allowed_root if {
  filesystem_access.allow with input as {
    "action": "read",
    "path": "/workspace/project/file.txt",
    "allowed_paths": ["/workspace/project"]
  }
}

test_deny_read_outside_allowed_root if not filesystem_access.allow with input as {
  "action": "read",
  "path": "/etc/passwd",
  "allowed_paths": ["/workspace/project"]
}

test_deny_prefix_bypass_path if not filesystem_access.allow with input as {
  "action": "read",
  "path": "/workspace/project-evil/secrets.txt",
  "allowed_paths": ["/workspace/project"]
}

test_deny_read_with_parent_traversal_escape if not filesystem_access.allow with input as {
  "action": "read",
  "path": "/workspace/project/../secrets.txt",
  "allowed_paths": ["/workspace/project"]
}

test_allow_read_with_dot_segments_under_allowed_root if {
  filesystem_access.allow with input as {
    "action": "read",
    "path": "/workspace/project/./nested/file.txt",
    "allowed_paths": ["/workspace/project/"]
  }
}

test_allow_write_under_write_root if {
  filesystem_access.allow with input as {
    "action": "write",
    "path": "/workspace/project/src/app.py",
    "allowed_paths": ["/workspace/project"],
    "allowed_write_paths": ["/workspace/project"]
  }
}

test_deny_write_with_only_read_roots if not filesystem_access.allow with input as {
  "action": "write",
  "path": "/workspace/project/src/app.py",
  "allowed_paths": ["/workspace/project"]
}

test_deny_write_outside_write_root if not filesystem_access.allow with input as {
  "action": "write",
  "path": "/workspace/other/app.py",
  "allowed_paths": ["/workspace"],
  "allowed_write_paths": ["/workspace/project"]
}

test_deny_write_with_parent_traversal if not filesystem_access.allow with input as {
  "action": "write",
  "path": "/workspace/project/../../etc/passwd",
  "allowed_write_paths": ["/workspace/project"]
}

# --- LOCUS-362: secret-bearing reads and gate-definition writes ---------------

read_input(path) := {
  "action": "read",
  "path": path,
  "allowed_paths": ["/workspace/project", "C:/ws"],
  "allowed_write_paths": ["/workspace/project", "C:/ws"]
}

write_input(path) := {
  "action": "write",
  "path": path,
  "allowed_paths": ["/workspace/project", "C:/ws"],
  "allowed_write_paths": ["/workspace/project", "C:/ws"]
}

test_ordinary_read_has_no_floor if {
  filesystem_access.allow with input as read_input("/workspace/project/src/app.py")
  filesystem_access.risk_floor == 0 with input as read_input("/workspace/project/src/app.py")
}

test_deny_dotenv_read if {
  not filesystem_access.allow with input as read_input("/workspace/project/.env")
  filesystem_access.risk_floor == 4 with input as read_input("/workspace/project/.env")
  filesystem_access.deny_reason == "credential_file" with input as read_input("/workspace/project/.env")
}

test_deny_dotenv_variant_read if {
  not filesystem_access.allow with input as read_input("/workspace/project/.env.production")
}

test_deny_dotenv_read_windows_path if {
  not filesystem_access.allow with input as read_input(`C:\ws\.env`)
  filesystem_access.risk_floor == 4 with input as read_input(`C:\ws\.env`)
}

test_deny_private_key_reads if {
  not filesystem_access.allow with input as read_input("/workspace/project/deploy/tls.key")
  not filesystem_access.allow with input as read_input("/workspace/project/certs/server.pem")
  not filesystem_access.allow with input as read_input("/workspace/project/keys/id_ed25519")
}

test_public_key_read_allowed if {
  filesystem_access.allow with input as read_input("/workspace/project/keys/id_ed25519.pub")
}

test_deny_credential_store_reads if {
  not filesystem_access.allow with input as read_input("/workspace/project/.npmrc")
  not filesystem_access.allow with input as read_input("/workspace/project/.git-credentials")
  not filesystem_access.allow with input as read_input("/workspace/project/.netrc")
  not filesystem_access.allow with input as read_input("/workspace/project/.pypirc")
}

test_deny_keychain_and_dpapi_reads if {
  not filesystem_access.allow with input as {
    "action": "read",
    "path": "/Users/dev/Library/Keychains/login.keychain-db",
    "allowed_paths": ["/Users/dev"]
  }
  not filesystem_access.allow with input as {
    "action": "read",
    "path": `C:\Users\dev\AppData\Roaming\Microsoft\Protect\S-1-5-21\key`,
    "allowed_paths": ["C:/Users/dev"]
  }
}

test_secret_like_read_asks if {
  filesystem_access.allow with input as read_input("/workspace/project/config/credentials.toml")
  filesystem_access.risk_floor == 3 with input as read_input("/workspace/project/config/credentials.toml")
  filesystem_access.risk_floor == 3 with input as read_input("/workspace/project/secrets.toml")
  filesystem_access.risk_floor == 3 with input as read_input("/workspace/project/deploy/prod.env")
  filesystem_access.risk_floor == 3 with input as read_input("/workspace/project/.envrc")
  filesystem_access.risk_floor == 3 with input as read_input("/workspace/project/infra/prod.tfvars")
}

test_deny_credential_store_write if {
  not filesystem_access.allow with input as write_input("/workspace/project/.ssh/authorized_keys")
  filesystem_access.deny_reason == "credential_store" with input as write_input("/workspace/project/.ssh/authorized_keys")
}

test_dotenv_write_is_an_ordinary_write if {
  filesystem_access.allow with input as write_input("/workspace/project/.env")
  filesystem_access.risk_floor == 0 with input as write_input("/workspace/project/.env")
}

test_gate_definition_writes_ask if {
  filesystem_access.allow with input as write_input("/workspace/project/.github/workflows/ci.yml")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/.github/workflows/ci.yml")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/.github/CODEOWNERS")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/policies/agent_policy.rego")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/tests/conftest.py")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/pyproject.toml")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/Makefile")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/.pre-commit-config.yaml")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/ruff.toml")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/mypy.ini")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/pytest.ini")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/setup.cfg")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/tox.ini")
}

# LOCUS-351: the RSI suite (tasks, graders, held-out split) and the scorecard /
# comparator / candidate-isolation code are gate definitions.
test_rsi_scorecard_writes_ask if {
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/apps/evals/locus_evals/suite/tasks/heldout/ho-csv-quoting.yaml")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/apps/evals/locus_evals/suite/graders.py")
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/locus_runtime/rsi/scorecard.py")
  filesystem_access.risk_floor == 3 with input as write_input(`C:\ws\locus_runtime\rsi\candidate.py`)
  filesystem_access.risk_floor == 0 with input as write_input("/workspace/project/apps/evals/locus_evals/runner.py")
  filesystem_access.risk_floor == 0 with input as write_input("/workspace/project/locus_runtime/rsi_notes.md")
}

test_gate_definition_write_windows_path if {
  filesystem_access.risk_floor == 3 with input as write_input(`C:\ws\.github\workflows\ci.yml`)
}

test_nested_workflow_dir_asks if {
  filesystem_access.risk_floor == 3 with input as write_input("/workspace/project/vendor/lib/.github/workflows/x.yml")
}

test_source_edits_have_no_floor if {
  filesystem_access.risk_floor == 0 with input as write_input("/workspace/project/src/app.py")
  filesystem_access.risk_floor == 0 with input as write_input("/workspace/project/src/policies/rules.py")
  filesystem_access.risk_floor == 0 with input as write_input("/workspace/project/tests/test_app.py")
}
