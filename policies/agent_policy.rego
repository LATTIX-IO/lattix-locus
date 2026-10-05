package lattix.agent_policy

import rego.v1

default allow = false

network_allowlist := {target | some target in input.allowed_targets}

# The resource as a lower-case "/" path, so the read_file deny rules also match
# Windows paths ("C:\ws\.env"); before LOCUS-362 they silently missed them.
resource_path := lower(replace(sprintf("%v", [object.get(input, "resource", "")]), "\\", "/"))

tool_calls_used := object.get(input, "tool_calls_used", object.get(input, "tool_calls", 0))

operation := value if {
  value := object.get(input, "tool", "")
  is_string(value)
  trim(value, " ") != ""
}

operation := value if {
  tool := object.get(input, "tool", "")
  not is_string(tool)
  value := object.get(input, "action", "")
  is_string(value)
}

operation := value if {
  tool := object.get(input, "tool", "")
  is_string(tool)
  trim(tool, " ") == ""
  value := object.get(input, "action", "")
  is_string(value)
}

allowed_tool(tool) if {
  provided_allowed_tools := object.get(input, "allowed_tools", null)
  is_array(provided_allowed_tools)
  tool in provided_allowed_tools
}

allow if {
  allowed_tool(operation)
  not deny
}

deny if {
  operation == "read_file"
  regex.match("(^|/)(\\.env(\\..+)?)$", resource_path)
}

deny if {
  operation == "read_file"
  regex.match("(^|/)(id_rsa|id_dsa|id_ed25519|authorized_keys|credentials|secrets?\\.(json|ya?ml)|service[-_]account.*\\.json|token\\.json|\\.npmrc|\\.pypirc|\\.netrc)$", resource_path)
}

deny if {
  operation == "read_file"
  regex.match("(\\.ssh/|\\.gnupg/|\\.aws/|/\\.config/gcloud/|\\.kube/)", resource_path)
}

deny if {
  operation == "read_file"
  regex.match("\\.(pem|key|p12|pfx|kdbx|asc|p8|csr|keystore|jks)$", resource_path)
}

deny if {
  operation == "read_file"
  regex.match("(apikey|api-key|access_token|access-token|auth_token|auth-token|bearer|client_secret|client-secret|private(_|-)?key|refresh_token|refresh-token|service[-_]account|oauth)", resource_path)
}

deny if {
  operation == "network_egress"
  count(network_allowlist) == 0
}

deny if {
  operation == "network_egress"
  not input.target in network_allowlist
}

deny if {
  input.budget.tokens_used > input.budget.max_tokens
}

deny if {
  object.get(input, "max_tool_calls", 0) > 0
  tool_calls_used > input.max_tool_calls
}

deny if {
  operation == "llm_call"
  input.classification == "restricted"
  input.provider != "local"
}
