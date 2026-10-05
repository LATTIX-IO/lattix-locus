package lattix.data_classification_test

import rego.v1

import data.lattix.data_classification

test_restricted_text if {
  data_classification.classification == "restricted" with input as {"text": "contains SSN data"}
}

test_confidential_text if {
  data_classification.classification == "confidential" with input as {"text": "customer escalation"}
}

test_restricted_private_key_text if {
  data_classification.classification == "restricted" with input as {"text": "contains private key material"}
}

test_confidential_password_text if {
  data_classification.classification == "confidential" with input as {"text": "password reset email"}
}

test_restricted_wins_over_confidential if {
  data.lattix.data_classification.classification == "restricted" with input as {"text": "customer ssn export"}
}
