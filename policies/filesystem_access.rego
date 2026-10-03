package lattix.filesystem_access

import rego.v1

default allow = false

allow if {
  input.action == "read"
  some allowed_path in input.allowed_paths
  path_within_allowed_root(input.path, allowed_path)
}

# Writes are allowed only under an explicit write root (the run workspace and
# granted extra paths). Reads never imply writes. (LOCUS-332 gateway)
allow if {
  input.action == "write"
  some allowed_path in input.allowed_write_paths
  path_within_allowed_root(input.path, allowed_path)
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
