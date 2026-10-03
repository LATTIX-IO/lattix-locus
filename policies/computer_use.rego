package lattix.computer_use

# Computer use (LOCUS-341, docs/product/12-computer-use.md §6).
#
# Input (built by locus_runtime.gateway.computer_use_input):
#   action          ui_observe | ui_click | ui_type | ui_key |
#                   browser_navigate | browser_read | browser_act
#   surface         "browser" (the dedicated agent browser) | "desktop"
#   control         observe | read | screenshot | navigate | click | fill |
#                   type | press | select | key
#   app             desktop: lower-case executable or bundle id
#   allowed_apps    desktop apps this run may drive (from the run envelope)
#   denied_apps     extra denied apps (run + LOCUS_COMPUTER_USE_DENIED_APPS)
#   sensitive_field the target is a password / card / CVV / SSN / OTP field
#   url_scheme      browser_navigate: scheme of the URL
#   egress_host     browser_navigate: host (also checked by network_egress)

import rego.v1

default allow := false

app := lower(trim_space(object.get(input, "app", "")))

surface := object.get(input, "surface", "")

control := object.get(input, "control", "")

entry_controls := {"fill", "type", "press", "key", "select"}

# Never driven, whatever a run's allowlist says (12 §6). Regexes over the app id.
builtin_denied_patterns := [
	# Password managers.
	"1password", "bitwarden", "keepass", "lastpass", "dashlane", "enpass",
	"roboform", "nordpass", "keeper", "protonpass", "proton pass",
	# Banking, payment and crypto apps.
	"bank", "paypal", "venmo", "wallet", "coinbase", "robinhood", "revolut",
	"cashapp", "cash app", "zelle", "ledger live", "metamask",
	# OS security, credential prompts, lock / login screens, system settings.
	"^consent\\.exe$", "credentialuibroker", "credui", "logonui", "lockapp",
	"sechealthui", "securityhealth", "windowsdefender", "msmpeng", "systemsettings",
	"^mmc\\.exe$", "regedit", "gpedit", "secpol", "^taskmgr\\.exe$",
	"keychain", "securityagent", "loginwindow", "systempreferences",
	"system settings", "coreautha", "com\\.apple\\.settings",
	# Shells and terminals: typing into them would bypass process_exec and the jail.
	"^cmd\\.exe$", "^powershell(_ise)?\\.exe$", "^pwsh(\\.exe)?$", "windowsterminal",
	"^wt\\.exe$", "^conhost\\.exe$", "openconsole", "^wsl\\.exe$", "^bash(\\.exe)?$",
	"com\\.apple\\.terminal", "iterm",
	# The user's own browsers: browser work uses the dedicated agent browser
	# (own-profile use is an H2 capability, 12 §7).
	"^chrome(\\.exe)?$", "^msedge(\\.exe)?$", "^firefox(\\.exe)?$", "^brave(\\.exe)?$",
	"^opera(\\.exe)?$", "^safari$", "com\\.apple\\.safari", "com\\.google\\.chrome",
	"org\\.mozilla\\.firefox", "com\\.microsoft\\.edgemac",
	# Locus itself (its Security space, grants and approvals).
	"locus", "lattix",
]

denied_builtin if {
	some pattern in builtin_denied_patterns
	regex.match(pattern, app)
}

denied_configured if {
	some denied in object.get(input, "denied_apps", [])
	lower(trim_space(denied)) == app
}

app_allowlisted if {
	some allowed in object.get(input, "allowed_apps", [])
	lower(trim_space(allowed)) == app
}

# Entering data into a secret-bearing field is R4 in the gateway; denied here too.
sensitive_entry if {
	control in entry_controls
	object.get(input, "sensitive_field", false) == true
}

navigation_ok if {
	input.action != "browser_navigate"
}

navigation_ok if {
	input.action == "browser_navigate"
	object.get(input, "url_scheme", "") in {"http", "https"}
	trim_space(object.get(input, "egress_host", "")) != ""
}

browser_action if {
	surface == "browser"
	startswith(object.get(input, "action", ""), "browser_")
}

desktop_action if {
	surface == "desktop"
	startswith(object.get(input, "action", ""), "ui_")
}

allow if {
	browser_action
	navigation_ok
	not sensitive_entry
}

allow if {
	desktop_action
	app != ""
	app_allowlisted
	not denied_builtin
	not denied_configured
	not sensitive_entry
}

# --- deny labels (reported by the gateway; never an allow input) ---------------

deny_reason := "sensitive_field" if {
	not allow
	sensitive_entry
} else := "app_denied" if {
	not allow
	desktop_action
	denied_builtin
} else := "app_denied" if {
	not allow
	desktop_action
	denied_configured
} else := "app_not_allowlisted" if {
	not allow
	desktop_action
} else := "navigation_not_http" if {
	not allow
	browser_action
	not navigation_ok
} else := "invalid_surface" if {
	not allow
}
