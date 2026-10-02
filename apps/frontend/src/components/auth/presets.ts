export type AuthMode = "signin" | "signup";

export type FormField = {
  name: string;
  label: string;
  type: "text" | "email" | "password";
  placeholder: string;
  autoComplete: string;
  required: boolean;
};

export const SIGNIN_FIELDS: FormField[] = [
  {
    name: "email",
    label: "Email",
    type: "email",
    placeholder: "operator@lattix.io",
    autoComplete: "email",
    required: true,
  },
  {
    name: "password",
    label: "Password",
    type: "password",
    placeholder: "",
    autoComplete: "current-password",
    required: true,
  },
];

export const SIGNUP_FIELDS: FormField[] = [
  {
    name: "firstName",
    label: "First Name",
    type: "text",
    placeholder: "",
    autoComplete: "given-name",
    required: true,
  },
  {
    name: "lastName",
    label: "Last Name",
    type: "text",
    placeholder: "",
    autoComplete: "family-name",
    required: true,
  },
  {
    name: "email",
    label: "Email",
    type: "email",
    placeholder: "operator@lattix.io",
    autoComplete: "email",
    required: true,
  },
  {
    name: "password",
    label: "Password",
    type: "password",
    placeholder: "",
    autoComplete: "new-password",
    required: true,
  },
  {
    name: "confirmPassword",
    label: "Confirm Password",
    type: "password",
    placeholder: "",
    autoComplete: "new-password",
    required: true,
  },
];

export type AuthErrorCode =
  | "invalid_credentials"
  | "identity_link_required"
  | "seat_limit_exceeded"
  | "userinfo_incomplete"
  | "token_exchange_failed"
  | "passwords_mismatch"
  | "registration_failed";

export function resolveAuthErrorMessage(code: string | undefined | null): string | null {
  switch (code) {
    case "invalid_credentials":
      return "The email or password was not accepted. Check your details and try again.";
    case "identity_link_required":
      return "This identity is not linked to a workspace member yet. Contact your administrator.";
    case "seat_limit_exceeded":
      return "Your workspace has reached its seat limit. Contact your administrator.";
    case "userinfo_incomplete":
      return "The identity provider did not return enough profile information to complete sign-in.";
    case "token_exchange_failed":
      return "Casdoor could not complete sign-in. Try again in a moment.";
    case "passwords_mismatch":
      return "Passwords do not match. Re-enter them and try again.";
    case "registration_failed":
      return "We couldn't create your account. Check your details and try again.";
    default:
      return null;
  }
}
