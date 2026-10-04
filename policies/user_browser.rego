package lattix.user_browser

# The principal's own signed-in browser (LOCUS-350, decision D-25).
#
# The agent drives the principal's Chrome / Edge / Firefox profile through the
# Locus extension. Every action is a gateway action evaluated here, under the
# principal's browser autonomy tier. The gateway also applies the risk class
# (R4 always denied; R3 asks unless the Open tier's consent covers it).
#
# Input (built by locus_runtime.gateway.user_browser_input):
#   action            user_browser_read | user_browser_navigate | user_browser_act
#   profile           "user"
#   control           read:     tabs | observe | screenshot
#                     navigate: navigate
#                     act:      click | fill | type | press | select | scroll
#   site              registrable site (eTLD+1) of the tab / target URL
#   url_scheme        navigate: scheme of the target URL
#   tab_shared        the principal shared the tab from the extension UI, or
#                     the agent opened it
#   sensitive_field   the target is a password / card / CVV / SSN / OTP field
#   risk              the gateway's risk class, "R0".."R4"
#   tier              strict | assisted | trusted | open (principal setting)
#   tier_consent      a principal recorded informed consent for that tier
#   allowlisted_sites sites for the Assisted tier (principal setting)
#   granted_sites     per-site standing grants for the Trusted tier
#   extension_paired  a paired extension client is connected to the relay
#   panicked          the computer-use panic latch is set
#
# Tiers (each widens the one before; a site outside a tier's lists falls back
# to the next lower tier's rules):
#   strict   (default) reads only on tabs the principal shared; every other
#            action asks
#   assisted allowlisted sites: read and navigate allowed; click / type /
#            submit ask
#   trusted  granted sites: everything allowed except irreversible (R3)
#            actions, which ask
#   open     no prompts (R3 covered by the recorded consent)
#
# The floor, in every tier, is a deny here: no paired extension, panic,
# entering data into a secret field, R4, non-http(s) navigation, a malformed
# action, and (below Open) reading a tab that was neither shared nor covered by
# a site list. Tiers above strict without a consent record behave as strict.
# The agent and page content never reach the tier: it is not in the action.

import rego.v1

default allow := false

default require_approval := true

default tier_allows_irreversible := false

tiers := {"strict", "assisted", "trusted", "open"}

read_controls := {"tabs", "observe", "screenshot"}

act_controls := {"click", "fill", "type", "press", "select", "scroll"}

entry_controls := {"fill", "type", "press", "select"}

control := object.get(input, "control", "")

action := object.get(input, "action", "")

site := lower(trim_space(object.get(input, "site", "")))

risk := object.get(input, "risk", "R4")

# --- tier --------------------------------------------------------------------

requested_tier := t if {
	t := object.get(input, "tier", "strict")
	t in tiers
} else := "strict"

effective_tier := requested_tier if {
	requested_tier != "strict"
	object.get(input, "tier_consent", false) == true
} else := "strict"

site_listed(list) if {
	site != ""
	some entry in list
	listed := lower(trim_space(entry))
	listed != ""
	site == listed
}

site_listed(list) if {
	site != ""
	some entry in list
	listed := lower(trim_space(entry))
	listed != ""
	endswith(site, concat("", [".", listed]))
}

granted if site_listed(object.get(input, "granted_sites", []))

allowlisted if site_listed(object.get(input, "allowlisted_sites", []))

# The rules that apply to this action's site under the effective tier.
level := "open" if {
	effective_tier == "open"
} else := "trusted" if {
	effective_tier == "trusted"
	granted
} else := "assisted" if {
	effective_tier in {"assisted", "trusted"}
	allowlisted
} else := "assisted" if {
	effective_tier in {"assisted", "trusted"}
	granted
} else := "strict"

# --- floor (deny in every tier) ----------------------------------------------

well_formed if {
	action == "user_browser_read"
	control in read_controls
}

well_formed if {
	action == "user_browser_navigate"
	control == "navigate"
}

well_formed if {
	action == "user_browser_act"
	control in act_controls
}

navigation_ok if action != "user_browser_navigate"

navigation_ok if {
	action == "user_browser_navigate"
	object.get(input, "url_scheme", "") in {"http", "https"}
	site != ""
}

sensitive_entry if {
	control in entry_controls
	object.get(input, "sensitive_field", false) == true
}

shared := object.get(input, "tab_shared", false) == true

# Observing a page (or scrolling it) needs the tab shared, or the site covered
# by a tier list. The tab list itself is filtered by the relay (visible_tabs).
observation_scoped if control in {"tabs"}

observation_scoped if shared

observation_scoped if level != "strict"

floor_reason := "panic" if {
	object.get(input, "panicked", true) != false
} else := "not_paired" if {
	object.get(input, "extension_paired", false) != true
} else := "invalid_action" if {
	object.get(input, "profile", "") != "user"
} else := "invalid_action" if {
	not well_formed
} else := "sensitive_field" if {
	sensitive_entry
} else := "risk_r4" if {
	not risk in {"R0", "R1", "R2", "R3"}
} else := "navigation_not_http" if {
	not navigation_ok
} else := "tab_not_shared" if {
	control in (read_controls | {"scroll"})
	not observation_scoped
}

# --- decision ----------------------------------------------------------------

reads := read_controls | {"scroll"}

needs_ask if {
	level == "strict"
	not control in reads
}

needs_ask if {
	level == "assisted"
	action == "user_browser_act"
	control != "scroll"
}

# Navigating away from a tab the principal did not share asks below Open.
needs_ask if {
	level in {"assisted", "trusted"}
	action == "user_browser_navigate"
	not shared
}

needs_ask if {
	level == "trusted"
	risk == "R3"
}

decision := "deny" if {
	floor_reason
} else := "ask" if {
	needs_ask
} else := "allow"

allow if decision != "deny"

require_approval := false if decision == "allow"

tier_allows_irreversible if {
	decision == "allow"
	level == "open"
	risk == "R3"
}

deny_reason := floor_reason
