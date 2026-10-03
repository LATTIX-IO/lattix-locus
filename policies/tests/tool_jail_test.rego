package lattix.tool_jail_test

import rego.v1

import data.lattix.tool_jail

# --- POSIX tiers (bwrap / seatbelt / hardened docker) ---------------------------

posix_input := {
  "isolation_tier": "hardened-docker",
  "readonly_rootfs": true,
  "require_egress_mediation": true,
  "allow_network": false,
  "run_as_user": "1000:1000",
  "command": ["python", "-c", "1+1"],
  "allowed_executables": ["python"]
}

test_allow_safe_tool_jail if {
  tool_jail.allow with input as {
    "isolation_tier": "hardened-docker",
    "readonly_rootfs": true,
    "require_egress_mediation": true,
    "allow_network": true,
    "run_as_user": "1000:1000",
    "command": ["python", "-c", "1+1"],
    "allowed_executables": ["python"],
    "allowed_hosts": ["api.example.com"],
    "requested_hosts": ["api.example.com"]
  }
}

test_allow_bwrap if {
  tool_jail.allow with input as object.union(posix_input, {"isolation_tier": "kernel-bwrap"})
}

test_allow_seatbelt if {
  tool_jail.allow with input as object.union(posix_input, {"isolation_tier": "kernel-seatbelt"})
}

test_deny_posix_facts_without_a_tier if not tool_jail.allow with input as object.remove(posix_input, ["isolation_tier"])

test_deny_root_user if not tool_jail.allow with input as object.union(posix_input, {"run_as_user": "0:0"})

test_deny_invalid_run_as_user if not tool_jail.allow with input as object.union(posix_input, {"run_as_user": "nobody:1000"})

test_deny_writable_rootfs if not tool_jail.allow with input as object.union(posix_input, {"readonly_rootfs": false})

test_deny_missing_allowed_executables if not tool_jail.allow with input as object.remove(posix_input, ["allowed_executables"])

test_deny_requested_hosts_when_network_disabled if not tool_jail.allow with input as object.union(posix_input, {"requested_hosts": ["api.example.com"]})

test_deny_unallowlisted_requested_hosts if not tool_jail.allow with input as object.union(posix_input, {
  "allow_network": true,
  "allowed_hosts": ["api.example.com"],
  "requested_hosts": ["evil.example.com"]
})

# --- Windows AppContainer + Job Object ------------------------------------------

appcontainer_input := {
  "isolation_tier": "windows-appcontainer",
  "appcontainer": true,
  "job_object": true,
  "require_appcontainer": true,
  "readonly_rootfs": false,
  "run_as_user": "",
  "allow_network": false,
  "require_egress_mediation": false,
  "command": ["cmd"],
  "allowed_executables": ["cmd"],
  "requested_hosts": []
}

test_allow_appcontainer_with_require_flag if {
  tool_jail.allow with input as appcontainer_input
}

test_deny_appcontainer_without_require_flag if {
  facts := object.union(appcontainer_input, {"require_appcontainer": false})
  not tool_jail.allow with input as facts
  tool_jail.deny_reason == "appcontainer_not_required" with input as facts
}

test_deny_appcontainer_require_flag_missing if not tool_jail.allow with input as object.remove(appcontainer_input, ["require_appcontainer"])

test_deny_job_object_only if not tool_jail.allow with input as object.union(appcontainer_input, {"appcontainer": false})

test_deny_appcontainer_without_job_object if not tool_jail.allow with input as object.union(appcontainer_input, {"job_object": false})

test_deny_appcontainer_unmediated_network if not tool_jail.allow with input as object.union(appcontainer_input, {"allow_network": true})

test_deny_appcontainer_unlisted_executable if {
  facts := object.union(appcontainer_input, {"command": ["powershell"]})
  not tool_jail.allow with input as facts
  tool_jail.deny_reason == "executable_not_allowlisted" with input as facts
}

# Windows agent toolchain (LOCUS-333): the executor asks with the logical name
# (`sh`, `python`) and maps it to the Locus toolchain only after an allow; a path
# to a toolchain binary is not a logical name and stays outside the allowlist.
test_allow_appcontainer_toolchain_shell if {
  tool_jail.allow with input as object.union(appcontainer_input, {"command": ["sh"], "allowed_executables": ["sh", "python"]})
}

test_deny_appcontainer_toolchain_absolute_path if {
  facts := object.union(appcontainer_input, {
    "command": ["C:/Users/u/AppData/Local/Lattix/Locus/toolchain/busybox/busybox.exe"],
    "allowed_executables": ["sh", "python"],
  })
  not tool_jail.allow with input as facts
  tool_jail.deny_reason == "executable_not_allowlisted" with input as facts
}

# --- host execution is never a jail --------------------------------------------

test_deny_local_direct if {
  facts := object.union(posix_input, {"isolation_tier": "local-direct"})
  not tool_jail.allow with input as facts
  tool_jail.deny_reason == "host_exec_not_confined" with input as facts
}

test_deny_restricted_process if not tool_jail.allow with input as object.union(posix_input, {"isolation_tier": "restricted-process"})

test_deny_no_sandbox_with_reason if {
  facts := object.union(posix_input, {"isolation_tier": "unavailable"})
  not tool_jail.allow with input as facts
  tool_jail.deny_reason == "no_confining_sandbox" with input as facts
}

# --- evaluation containers (SWE-bench) -------------------------------------------

eval_input := {
  "isolation_tier": "docker-exec",
  "runtime_profile": "evals",
  "readonly_rootfs": false,
  "run_as_user": "",
  "allow_network": false,
  "command": ["bash"],
  "allowed_executables": ["bash"],
  "requested_hosts": []
}

test_allow_eval_container_with_evals_profile_and_no_network if {
  tool_jail.allow with input as eval_input
}

test_deny_eval_container_without_evals_profile if {
  facts := object.union(eval_input, {"runtime_profile": ""})
  not tool_jail.allow with input as facts
  tool_jail.deny_reason == "eval_container_needs_evals_profile_and_no_network" with input as facts
}

test_deny_eval_container_for_other_profile if not tool_jail.allow with input as object.union(eval_input, {"runtime_profile": "local-native"})

test_deny_eval_container_with_network if not tool_jail.allow with input as object.union(eval_input, {"allow_network": true, "require_egress_mediation": true})

test_deny_eval_container_unknown_network if not tool_jail.allow with input as object.remove(eval_input, ["allow_network"])

test_deny_evals_profile_on_host_exec if not tool_jail.allow with input as object.union(eval_input, {"isolation_tier": "local-direct"})
