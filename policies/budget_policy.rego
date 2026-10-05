package lattix.budget_policy

import rego.v1

# Deny by default (P7): allow only when the token budget is declared and
# respected, and every other declared limit is respected too.
default allow := false

allow if {
  within(object.get(input, "tokens_used", null), object.get(input, "max_tokens", null))
  optional_within("duration_used_seconds", "max_duration_seconds")
  optional_within("cost_used_usd", "max_cost_usd")
}

within(used, limit) if {
  is_number(used)
  is_number(limit)
  used >= 0
  used <= limit
}

# A limit that isn't declared doesn't constrain; a declared one must be met.
optional_within(_, limit_key) if object.get(input, limit_key, null) == null

optional_within(used_key, limit_key) if {
  within(object.get(input, used_key, null), object.get(input, limit_key, null))
}
