package lattix.filesystem_access

# filesystem_access: where an agent may read and write, and how risky the
# path itself is (LOCUS-332 roots; LOCUS-362 secret files and gate definitions).
#
# Input (built by locus_runtime.gateway.filesystem_access_input):
#   action               "read" | "write"
#   path                 the target (absolute; "\" or "/" separators)
#   allowed_paths        read roots
#   allowed_write_paths  write roots (the run workspace and granted paths)
#
# Outputs besides `allow`:
#   risk_floor   0-4. The gateway raises the action's risk class to at least
#                this (never lowers it): 3 = ask, 4 = deny.
#   deny_reason  a label for a deny (never a decision input).
#
# Risk classes (mirrored by locus_runtime.gateway.secret_read_class and
# gate_definition_write; tests/policy/test_policy_parity.py asserts the
# patterns and lists below equal the Python ones):
#   read  credential store, key material, named secret file      R4 (deny)
#   read  secret-like name (credentials.*, secrets.*, *.env, ...) R3 (ask;
#         once approved the content is masked before the model sees it)
#   write credential store                                       R4 (deny)
#   write gate / CI definition inside a workspace                R3 (ask)

import rego.v1

default allow := false

# --- secret-bearing files (patterns on the lower-cased "/" path) ----------------

credential_store_pattern := `(^|/)(\.ssh|\.gnupg|\.aws|\.kube|\.docker|\.azure|\.password-store)(/|$)|(^|/)\.config/gcloud(/|$)|(^|/)library/keychains(/|$)|\.keychain(-db)?$|(^|/)microsoft/(protect|credentials|vault)(/|$)|(^|/)(id_(rsa|dsa|ecdsa|ed25519)(_sk)?|authorized_keys|\.netrc|_netrc|\.pypirc|\.npmrc|\.git-credentials|\.htpasswd)$`

key_material_pattern := `\.(pem|key|p12|pfx|p8|jks|keystore|kdbx|ppk|asc|csr)$|(^|/)id_[a-z0-9_-]+$`

named_secret_pattern := `(^|/)\.env(\.[^/]*)?$|(^|/)credentials$|(^|/)secrets?\.(json|ya?ml)$|(^|/)service[-_]account[^/]*\.json$|(^|/)token\.json$`

secret_like_pattern := `(^|/)credentials\.[a-z0-9]+$|(^|/)secrets?\.[a-z0-9]+$|\.secrets?$|(^|/)\.envrc$|(^|/)[^/]+\.env$|(^|/)\.dev\.vars$|\.tfvars(\.json)?$|\.tfstate(\.backup)?$`

# --- gate / CI definitions (shared with the D-22 merge guard) -------------------

gate_write_basenames := {
	"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "ruff.toml", ".ruff.toml",
	"mypy.ini", ".mypy.ini", ".pre-commit-config.yaml", ".coveragerc", "noxfile.py",
	".gitlab-ci.yml", "azure-pipelines.yml", "jenkinsfile",
	"pyproject.toml", "makefile", "gnumakefile",
	"codeowners",
	# Agent instruction / memory files (injection persistence, P8).
	"agents.md", "claude.md", "claude.local.md", "gemini.md", ".cursorrules", ".windsurfrules",
}

gate_write_paths := {
	".github/workflows/", ".circleci/", "policies/", "scripts/run_opa.py",
	"precommit.sh", "precommit.ps1", ".github/",
	".claude/", ".cursor/", ".ai-memory/",
	# RSI scorecard (LOCUS-351): eval suite, graders, held-out split, comparator.
	"apps/evals/locus_evals/suite/", "locus_runtime/rsi/",
}

# --- facts -------------------------------------------------------------------

lower_path := lower(replace(sprintf("%v", [object.get(input, "path", "")]), "\\", "/"))

secret_path := trim_right(lower_path, "/")

basename := name if {
	parts := split(secret_path, "/")
	name := parts[count(parts) - 1]
}

credential_store if regex.match(credential_store_pattern, secret_path)

key_material if regex.match(key_material_pattern, secret_path)

named_secret if regex.match(named_secret_pattern, secret_path)

secret_like if regex.match(secret_like_pattern, secret_path)

read_class := 4 if {
	secret_path != ""
	credential_store
} else := 4 if {
	secret_path != ""
	key_material
} else := 4 if {
	secret_path != ""
	named_secret
} else := 3 if {
	secret_path != ""
	secret_like
} else := 0

lower_segments(path) := [segment |
	some segment in split(lower(replace(sprintf("%v", [path]), "\\", "/")), "/")
	segment != ""
	segment != "."
]

# The target relative to each write root that contains it.
write_relatives contains relative if {
	some root in object.get(input, "allowed_write_paths", [])
	root_segments := lower_segments(root)
	path_segments := lower_segments(input.path)
	count(root_segments) > 0
	count(path_segments) > count(root_segments)
	array.slice(path_segments, 0, count(root_segments)) == root_segments
	relative := concat("/", array.slice(path_segments, count(root_segments), count(path_segments)))
}

prefix_matches(relative, prefix) if relative == trim_suffix(prefix, "/")

prefix_matches(relative, prefix) if startswith(relative, prefix)

gate_definition if basename in gate_write_basenames

gate_definition if {
	some relative in write_relatives
	some prefix in gate_write_paths
	prefix_matches(relative, prefix)
}

# A CI workflow directory at any depth (nested repositories, unknown roots).
gate_definition if contains(concat("", ["/", lower_path]), "/.github/workflows/")

write_class := 4 if {
	credential_store
} else := 3 if {
	gate_definition
} else := 0

risk_floor := read_class if input.action == "read"

risk_floor := write_class if input.action == "write"

# --- allow -------------------------------------------------------------------

allow if {
	input.action == "read"
	some allowed_path in input.allowed_paths
	path_within_allowed_root(input.path, allowed_path)
	read_class < 4
}

# Writes are allowed only under an explicit write root (the run workspace and
# granted extra paths). Reads never imply writes. (LOCUS-332 gateway)
allow if {
	input.action == "write"
	some allowed_path in input.allowed_write_paths
	path_within_allowed_root(input.path, allowed_path)
	write_class < 4
}

path_within_allowed_root(path, allowed_path) if {
	candidate_segments := normalized_path_segments(path)
	allowed_segments := normalized_path_segments(allowed_path)
	count(allowed_segments) > 0
	count(candidate_segments) >= count(allowed_segments)
	array.slice(candidate_segments, 0, count(allowed_segments)) == allowed_segments
}

normalized_path_segments(path) := segments if {
	normalized := replace(sprintf("%v", [path]), "\\", "/")
	not has_parent_reference(normalized)
	raw_segments := split(normalized, "/")
	segments := [segment |
		some i
		segment := raw_segments[i]
		segment != ""
		segment != "."
	]
}

has_parent_reference(path) if {
	raw_segments := split(path, "/")
	some segment in raw_segments
	segment == ".."
}

# --- deny labels (reported by the gateway; never an allow input) ---------------

deny_reason := "credential_file" if {
	not allow
	input.action == "read"
	read_class == 4
} else := "credential_store" if {
	not allow
	input.action == "write"
	write_class == 4
} else := "outside_allowed_roots" if {
	not allow
}
