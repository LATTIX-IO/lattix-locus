package lattix.data_classification

import rego.v1

# Highest matching label wins: restricted > confidential > internal.
# Each label is decided once, so overlapping matches can't conflict.
default classification := "internal"

restricted if contains(lower(input.text), "ssn")

restricted if regex.match("(social security|api[_-]?key|bearer|private key)", lower(input.text))

confidential if contains(lower(input.text), "customer")

confidential if regex.match("(password|phone|email)", lower(input.text))

classification := "restricted" if restricted

classification := "confidential" if {
  not restricted
  confidential
}
