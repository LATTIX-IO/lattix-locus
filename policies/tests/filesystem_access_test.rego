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
