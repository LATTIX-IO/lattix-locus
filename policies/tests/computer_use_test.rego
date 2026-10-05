package lattix.computer_use_test

import rego.v1

import data.lattix.computer_use

desktop(overrides) := object.union(
	{
		"action": "ui_click",
		"surface": "desktop",
		"control": "click",
		"app": "notepad.exe",
		"allowed_apps": ["notepad.exe"],
		"denied_apps": [],
		"sensitive_field": false,
	},
	overrides,
)

browser(overrides) := object.union(
	{
		"action": "browser_act",
		"surface": "browser",
		"control": "click",
		"app": "",
		"allowed_apps": [],
		"denied_apps": [],
		"sensitive_field": false,
	},
	overrides,
)

test_allowlisted_desktop_app_allowed if {
	computer_use.allow with input as desktop({})
}

test_app_match_is_case_insensitive if {
	computer_use.allow with input as desktop({"app": "Notepad.EXE"})
}

test_unlisted_desktop_app_denied if {
	not computer_use.allow with input as desktop({"allowed_apps": []})
	computer_use.deny_reason == "app_not_allowlisted" with input as desktop({"allowed_apps": []})
}

test_empty_app_denied if {
	not computer_use.allow with input as desktop({"app": "", "allowed_apps": [""]})
}

test_password_manager_denied_even_if_allowlisted if {
	not computer_use.allow with input as desktop({"app": "1password.exe", "allowed_apps": ["1password.exe"]})
	computer_use.deny_reason == "app_denied" with input as desktop({"app": "keepassxc.exe", "allowed_apps": ["keepassxc.exe"]})
}

test_os_security_and_credential_prompts_denied if {
	not computer_use.allow with input as desktop({"app": "consent.exe", "allowed_apps": ["consent.exe"]})
	not computer_use.allow with input as desktop({"app": "systemsettings.exe", "allowed_apps": ["systemsettings.exe"]})
	not computer_use.allow with input as desktop({"app": "com.apple.keychainaccess", "allowed_apps": ["com.apple.keychainaccess"]})
}

test_shells_and_own_browsers_denied if {
	not computer_use.allow with input as desktop({"app": "powershell.exe", "allowed_apps": ["powershell.exe"]})
	not computer_use.allow with input as desktop({"app": "chrome.exe", "allowed_apps": ["chrome.exe"]})
}

test_banking_and_locus_denied if {
	not computer_use.allow with input as desktop({"app": "mybank.exe", "allowed_apps": ["mybank.exe"]})
	not computer_use.allow with input as desktop({"app": "locus.exe", "allowed_apps": ["locus.exe"]})
}

test_configured_deny_list_wins if {
	not computer_use.allow with input as desktop({"denied_apps": ["NOTEPAD.exe"]})
	computer_use.deny_reason == "app_denied" with input as desktop({"denied_apps": ["notepad.exe"]})
}

test_typing_into_secret_field_denied if {
	not computer_use.allow with input as desktop({"action": "ui_type", "control": "type", "sensitive_field": true})
	not computer_use.allow with input as browser({"control": "fill", "sensitive_field": true})
	computer_use.deny_reason == "sensitive_field" with input as browser({"control": "fill", "sensitive_field": true})
}

test_clicking_secret_field_allowed if {
	computer_use.allow with input as browser({"control": "click", "sensitive_field": true})
}

test_browser_act_allowed_without_app_list if {
	computer_use.allow with input as browser({})
}

test_navigate_requires_http_and_host if {
	computer_use.allow with input as browser({"action": "browser_navigate", "control": "navigate", "url_scheme": "https", "egress_host": "example.com"})
	not computer_use.allow with input as browser({"action": "browser_navigate", "control": "navigate", "url_scheme": "file", "egress_host": ""})
	not computer_use.allow with input as browser({"action": "browser_navigate", "control": "navigate", "url_scheme": "https", "egress_host": ""})
	computer_use.deny_reason == "navigation_not_http" with input as browser({"action": "browser_navigate", "control": "navigate", "url_scheme": "javascript", "egress_host": ""})
}

test_surface_must_match_action if {
	not computer_use.allow with input as desktop({"surface": "browser"})
	not computer_use.allow with input as browser({"surface": "desktop", "app": "notepad.exe", "allowed_apps": ["notepad.exe"]})
	not computer_use.allow with input as {}
}
