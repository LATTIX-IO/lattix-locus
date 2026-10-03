package lattix.budget_policy_test

import rego.v1

import data.lattix.budget_policy

test_empty_input_denied if {
  not budget_policy.allow with input as {}
}

test_within_token_budget_allowed if {
  budget_policy.allow with input as {"tokens_used": 100, "max_tokens": 1000}
}

test_at_token_limit_allowed if {
  budget_policy.allow with input as {"tokens_used": 1000, "max_tokens": 1000}
}

test_over_token_budget_denied if {
  not budget_policy.allow with input as {"tokens_used": 1001, "max_tokens": 1000}
}

test_missing_token_limit_denied if {
  not budget_policy.allow with input as {"tokens_used": 1}
}

test_non_numeric_denied if {
  not budget_policy.allow with input as {"tokens_used": "1", "max_tokens": 1000}
}

test_negative_usage_denied if {
  not budget_policy.allow with input as {"tokens_used": -1, "max_tokens": 1000}
}

test_over_duration_denied if {
  not budget_policy.allow with input as {"tokens_used": 1, "max_tokens": 10, "duration_used_seconds": 61, "max_duration_seconds": 60}
}

test_over_cost_denied if {
  not budget_policy.allow with input as {"tokens_used": 1, "max_tokens": 10, "cost_used_usd": 2.5, "max_cost_usd": 2}
}

test_all_limits_respected_allowed if {
  budget_policy.allow with input as {"tokens_used": 1, "max_tokens": 10, "duration_used_seconds": 30, "max_duration_seconds": 60, "cost_used_usd": 1, "max_cost_usd": 2}
}
