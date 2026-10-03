package lattix.tool_jail

# tool_jail: a process exec is allowed only inside a real OS confinement tier
# (principal decision 2026-10-03, "Accept real OS jails"). The executor reports
# the tier it actually launches with (`isolation_tier` plus tier facts); the
# run profile comes from the registered gateway session, never from the agent.
#
# Accepted tiers:
#   kernel-bwrap / kernel-seatbelt / hardened-docker
#       read-only root filesystem and a numeric non-root uid
#   windows-appcontainer
#       AppContainer + Job Object, launched with require_appcontainer so the
#       launcher fails closed instead of degrading to Job-Object-only (Windows
#       has no numeric uid; the AppContainer token is the confinement)
#   docker-exec (evaluation container, e.g. SWE-bench)
#       only for an `evals` session profile and with networking disabled
# Every tier also needs an allowlisted executable and safe egress/host facts.
# Executables are matched by exact logical name. On Windows the executor maps
# `sh`/`bash`/`python`/`python3` to the Locus-owned agent toolchain only after
# this policy allowed the logical name (LOCUS-333); a binary path is never a
# logical name, so it is denied unless an operator allowlists that path.
# Host execution (local-direct, restricted-process, none) is never a jail.

import rego.v1

default allow = false

posix_tiers := {"kernel-bwrap", "kernel-seatbelt", "hardened-docker"}

host_tiers := {"local-direct", "restricted-process", "none"}

command_executable := value if {
  command := object.get(input, "command", [])
  is_array(command)
  count(command) > 0
  raw := command[0]
  value := trim(sprintf("%v", [raw]), " ")
  value != ""
}

command_executable := value if {
  command := object.get(input, "command", [])
  not is_array(command)
  value := trim(object.get(input, "tool", object.get(input, "action", "")), " ")
  value != ""
}

valid_uid(uid) if {
  regex.match("^[0-9]+$", uid)
}

non_root_user if {
  run_as_user := trim(object.get(input, "run_as_user", ""), " ")
  run_as_user != ""
  uid := split(run_as_user, ":")[0]
  valid_uid(uid)
  to_number(uid) > 0
}

network_safe if {
  input.allow_network != true
}

network_safe if {
  input.require_egress_mediation == true
}

executable_safe if {
  allowed_executables := object.get(input, "allowed_executables", [])
  is_array(allowed_executables)
  count(allowed_executables) > 0
  executable := command_executable
  executable in allowed_executables
}

network_targets_safe if {
  input.allow_network != true
  requested_hosts := object.get(input, "requested_hosts", [])
  count(requested_hosts) == 0
}

network_targets_safe if {
  input.allow_network == true
  allowed_hosts := object.get(input, "allowed_hosts", [])
  requested_hosts := object.get(input, "requested_hosts", [])
  count(allowed_hosts) > 0
  count(requested_hosts) > 0
  every host in requested_hosts {
    host in allowed_hosts
  }
}

tier := object.get(input, "isolation_tier", "")

# --- confinement tiers ----------------------------------------------------------

posix_jail if {
  tier in posix_tiers
  input.readonly_rootfs == true
  non_root_user
  network_safe
}

windows_appcontainer_jail if {
  tier == "windows-appcontainer"
  input.appcontainer == true
  input.job_object == true
  input.require_appcontainer == true
  network_safe
}

eval_container_jail if {
  tier == "docker-exec"
  input.runtime_profile == "evals"
  input.allow_network == false
}

jailed if posix_jail

jailed if windows_appcontainer_jail

jailed if eval_container_jail

allow if {
  jailed
  executable_safe
  network_targets_safe
}

# --- deny labels (reported by the gateway; never an allow input) ---------------

deny_reason := "no_confining_sandbox" if {
  not allow
  tier == "unavailable"
} else := "host_exec_not_confined" if {
  not allow
  tier in host_tiers
} else := "appcontainer_not_required" if {
  not allow
  tier == "windows-appcontainer"
  not input.require_appcontainer == true
} else := "eval_container_needs_evals_profile_and_no_network" if {
  not allow
  tier == "docker-exec"
  not eval_container_jail
} else := "executable_not_allowlisted" if {
  not allow
  jailed
  not executable_safe
} else := "not_jailed" if {
  not allow
}
