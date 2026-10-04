package lattix.user_browser_test

import rego.v1

import data.lattix.user_browser

# A paired, un-panicked click on a shared tab of example.com.
base(overrides) := object.union(
	{
		"action": "user_browser_act",
		"profile": "user",
		"control": "click",
		"site": "example.com",
		"url_scheme": "",
		"tab_shared": true,
		"sensitive_field": false,
		"risk": "R2",
		"tier": "strict",
		"tier_consent": false,
		"allowlisted_sites": ["example.com"],
		"granted_sites": ["example.com"],
		"extension_paired": true,
		"panicked": false,
		"protected_action": false,
	},
	overrides,
)

tier(name, overrides) := base(object.union({"tier": name, "tier_consent": name != "strict"}, overrides))

observe := {"action": "user_browser_read", "control": "observe", "risk": "R0"}

tabs := {"action": "user_browser_read", "control": "tabs", "risk": "R0"}

screenshot := {"action": "user_browser_read", "control": "screenshot", "risk": "R1"}

navigate := {"action": "user_browser_navigate", "control": "navigate", "url_scheme": "https", "risk": "R2"}

click := {"action": "user_browser_act", "control": "click", "risk": "R2"}

fill := {"action": "user_browser_act", "control": "fill", "risk": "R2"}

scroll := {"action": "user_browser_act", "control": "scroll", "risk": "R1"}

send := {"action": "user_browser_act", "control": "click", "risk": "R3"}

# A payment / purchase or account-security change (the gateway sets the fact).
buy := object.union(send, {"protected_action": true})

# --- payments and account security ask in every tier, Open included -----------

test_protected_actions_ask_in_every_tier if {
	every name in ["strict", "assisted", "trusted", "open"] {
		decision(name, buy, {}) == "ask"
	}
	not user_browser.tier_allows_irreversible with input as tier("open", buy)
	user_browser.require_approval with input as tier("open", buy)
}

test_missing_protected_fact_counts_as_protected if {
	user_browser.decision == "ask" with input as object.remove(tier("open", send), ["protected_action"])
	decision("open", send, {"protected_action": "false"}) == "ask"
	decision("open", send, {"protected_action": null}) == "ask"
}

test_protected_fact_does_not_affect_reads_or_navigation if {
	decision("open", observe, {"protected_action": true}) == "allow"
	decision("open", navigate, {"protected_action": true}) == "allow"
}

decision(name, act, extra) := d if {
	d := user_browser.decision with input as tier(name, object.union(act, extra))
}

# --- the tier x action matrix (listed / shared site) --------------------------

test_strict_matrix if {
	decision("strict", tabs, {}) == "allow"
	decision("strict", observe, {}) == "allow"
	decision("strict", screenshot, {}) == "allow"
	decision("strict", scroll, {}) == "allow"
	decision("strict", navigate, {}) == "ask"
	decision("strict", click, {}) == "ask"
	decision("strict", fill, {}) == "ask"
	decision("strict", send, {}) == "ask"
}

test_assisted_matrix if {
	decision("assisted", tabs, {}) == "allow"
	decision("assisted", observe, {}) == "allow"
	decision("assisted", screenshot, {}) == "allow"
	decision("assisted", scroll, {}) == "allow"
	decision("assisted", navigate, {}) == "allow"
	decision("assisted", click, {}) == "ask"
	decision("assisted", fill, {}) == "ask"
	decision("assisted", send, {}) == "ask"
}

test_trusted_matrix if {
	decision("trusted", tabs, {}) == "allow"
	decision("trusted", observe, {}) == "allow"
	decision("trusted", screenshot, {}) == "allow"
	decision("trusted", navigate, {}) == "allow"
	decision("trusted", click, {}) == "allow"
	decision("trusted", fill, {}) == "allow"
	decision("trusted", send, {}) == "ask"
}

test_open_matrix if {
	decision("open", tabs, {}) == "allow"
	decision("open", observe, {}) == "allow"
	decision("open", navigate, {}) == "allow"
	decision("open", click, {}) == "allow"
	decision("open", fill, {}) == "allow"
	decision("open", send, {}) == "allow"
	user_browser.tier_allows_irreversible with input as tier("open", send)
}

test_only_open_covers_irreversible if {
	not user_browser.tier_allows_irreversible with input as tier("trusted", send)
	not user_browser.tier_allows_irreversible with input as tier("assisted", send)
	not user_browser.tier_allows_irreversible with input as tier("strict", send)
	not user_browser.tier_allows_irreversible with input as tier("open", click)
}

test_require_approval_follows_decision if {
	user_browser.require_approval with input as tier("strict", click)
	not user_browser.require_approval with input as tier("strict", observe)
	user_browser.require_approval with input as tier("open", {"panicked": true})
}

# --- sites outside the tier's lists fall back to the lower tier ---------------

unlisted := {"site": "other.org", "tab_shared": false}

test_assisted_unlisted_site_is_strict if {
	decision("assisted", observe, unlisted) == "deny"
	user_browser.deny_reason == "tab_not_shared" with input as tier("assisted", object.union(observe, unlisted))
	decision("assisted", navigate, unlisted) == "ask"
	decision("assisted", click, unlisted) == "ask"
}

test_trusted_falls_back_to_allowlist_then_strict if {
	allow_only := {"granted_sites": [], "allowlisted_sites": ["example.com"]}
	decision("trusted", observe, allow_only) == "allow"
	decision("trusted", click, allow_only) == "ask"
	decision("trusted", click, unlisted) == "ask"
	decision("trusted", observe, unlisted) == "deny"
}

test_navigate_from_unshared_tab_asks_below_open if {
	decision("assisted", navigate, {"tab_shared": false}) == "ask"
	decision("trusted", navigate, {"tab_shared": false}) == "ask"
	decision("open", navigate, {"tab_shared": false}) == "allow"
}

test_subdomains_match_listed_site_but_not_lookalikes if {
	decision("trusted", click, {"site": "mail.example.com"}) == "allow"
	decision("trusted", click, {"site": "badexample.com"}) == "ask"
	decision("trusted", click, {"site": "example.com.evil.net"}) == "ask"
}

test_strict_observe_needs_shared_tab if {
	decision("strict", observe, {"tab_shared": false}) == "deny"
	decision("strict", screenshot, {"tab_shared": false}) == "deny"
	decision("strict", scroll, {"tab_shared": false}) == "deny"
	decision("strict", tabs, {"tab_shared": false}) == "allow"
}

# --- tier selection -----------------------------------------------------------

test_widened_tier_without_consent_is_strict if {
	user_browser.effective_tier == "strict" with input as base({"tier": "open", "tier_consent": false})
	user_browser.decision == "ask" with input as base(object.union(send, {"tier": "open", "tier_consent": false}))
	user_browser.decision == "ask" with input as base(object.union(click, {"tier": "trusted"}))
}

test_unknown_tier_is_strict if {
	user_browser.effective_tier == "strict" with input as base({"tier": "yolo", "tier_consent": true})
	user_browser.effective_tier == "strict" with input as base({"tier": 3, "tier_consent": true})
}

test_consent_must_be_boolean_true if {
	user_browser.effective_tier == "strict" with input as base({"tier": "open", "tier_consent": "true"})
}

# --- the floor holds in every tier --------------------------------------------

all_tiers := ["strict", "assisted", "trusted", "open"]

test_panic_denies_everything if {
	every name in all_tiers {
		decision(name, observe, {"panicked": true}) == "deny"
		decision(name, click, {"panicked": true}) == "deny"
	}
	user_browser.deny_reason == "panic" with input as tier("open", {"panicked": true})
}

test_missing_panic_flag_denies if {
	not user_browser.allow with input as object.remove(tier("open", click), ["panicked"])
}

test_unpaired_extension_denies if {
	every name in all_tiers {
		decision(name, observe, {"extension_paired": false}) == "deny"
	}
	user_browser.deny_reason == "not_paired" with input as tier("open", {"extension_paired": false})
	not user_browser.allow with input as object.remove(tier("open", click), ["extension_paired"])
}

test_secret_field_entry_denied_in_every_tier if {
	every name in all_tiers {
		decision(name, fill, {"sensitive_field": true}) == "deny"
		decision(name, {"action": "user_browser_act", "control": "press", "risk": "R2"}, {"sensitive_field": true}) == "deny"
	}
	user_browser.deny_reason == "sensitive_field" with input as tier("open", object.union(fill, {"sensitive_field": true}))
}

test_clicking_secret_field_is_not_entry if {
	decision("trusted", click, {"sensitive_field": true}) == "allow"
}

test_r4_denied_in_every_tier if {
	every name in all_tiers {
		decision(name, click, {"risk": "R4"}) == "deny"
	}
	decision("open", click, {"risk": "bogus"}) == "deny"
}

test_navigation_must_be_http_with_a_site if {
	every name in all_tiers {
		decision(name, navigate, {"url_scheme": "file"}) == "deny"
		decision(name, navigate, {"url_scheme": "javascript"}) == "deny"
		decision(name, navigate, {"site": ""}) == "deny"
	}
	user_browser.deny_reason == "navigation_not_http" with input as tier("open", object.union(navigate, {"url_scheme": "chrome"}))
}

test_malformed_actions_denied if {
	decision("open", {"action": "user_browser_read", "control": "click", "risk": "R0"}, {}) == "deny"
	decision("open", {"action": "user_browser_act", "control": "observe", "risk": "R0"}, {}) == "deny"
	decision("open", {"action": "browser_act", "control": "click", "risk": "R2"}, {}) == "deny"
	decision("open", click, {"profile": "agent"}) == "deny"
	not user_browser.allow with input as {}
}

test_deny_keeps_require_approval_closed if {
	user_browser.require_approval with input as tier("open", {"extension_paired": false})
	not user_browser.tier_allows_irreversible with input as tier("open", object.union(send, {"panicked": true}))
}
